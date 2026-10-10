import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.errors import ConfigError
from gleanyard.hub.alerts import LABEL, compute_alerts, sync_issues, weekly_digest
from gleanyard.hub.config import HubConfig, load_hub_config
from gleanyard.hub.github import HttpGitHub, Issue, PullRequest
from gleanyard.hub.state import collect_state
from gleanyard.status import ProjectStatus, SourceStatus

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # a Monday
runner = CliRunner()


def status(*sources: SourceStatus, age_hours: float = 2) -> bytes:
    return (
        ProjectStatus(
            generated_at=NOW - timedelta(hours=age_hours),
            schema_name="event",
            healthy=all(s.health == "ok" for s in sources),
            sources=list(sources),
        )
        .model_dump_json()
        .encode()
    )


def source(id_: str, health: str = "ok", **kw) -> SourceStatus:
    return SourceStatus(id=id_, enabled=True, health=health, records=10, **kw)


class FakeGitHub:
    def __init__(self, files=None, pulls=None):
        self.files, self.pulls = files or {}, pulls or {}
        self.issues: dict[int, dict] = {}
        self.log: list[tuple] = []

    def get_file(self, repo, path):
        return self.files.get(repo)

    def open_pulls(self, repo):
        return self.pulls.get(repo, [])

    def open_issues(self, repo, label):
        return [
            Issue(number=n, title=i["title"], body=i["body"])
            for n, i in self.issues.items()
            if i["open"] and label in i["labels"]
        ]

    def create_issue(self, repo, title, body, labels):
        number = len(self.issues) + 1
        self.issues[number] = {"title": title, "body": body, "labels": labels, "open": True}
        self.log.append(("create", title))
        return number

    def comment(self, repo, number, body):
        self.log.append(("comment", number))

    def close_issue(self, repo, number):
        self.issues[number]["open"] = False
        self.log.append(("close", number))


def config(**kw):
    return HubConfig(repos=["o/a", "o/b"], digest_weekday=None, **kw)


def test_config_validates_repos(tmp_path):
    path = tmp_path / "hub.yaml"
    path.write_text("repos: [not-a-repo]\n")
    with pytest.raises(ConfigError, match="owner/name"):
        load_hub_config(path)
    path.write_text("repos: [o/a, o/a]\n")
    with pytest.raises(ConfigError, match="twice"):
        load_hub_config(path)
    path.write_text("repos: [o/a]\nmissed_run_hours: 12\n")
    assert load_hub_config(path).missed_run_hours == 12


def test_state_levels_and_pr_label_filter():
    pull = lambda n, labels: PullRequest(  # noqa: E731
        number=n, title="t", url="u", created_at=NOW, labels=labels
    )
    github = FakeGitHub(
        files={"o/a": status(source("x"), source("y", "failing")), "o/b": b"{not json"},
        pulls={"o/a": [pull(1, ["repair"]), pull(2, ["docs"])]},
    )
    state = collect_state(config(), github, NOW)
    a, b = state.repos
    assert a.level == "red" and [p.number for p in a.pulls] == [1]
    assert b.status is None and "not a valid report" in b.problem and b.level == "red"
    assert (
        collect_state(config(), FakeGitHub(files={"o/a": status(source("x"))}), NOW).repos[0].level
        == "green"
    )
    amber = collect_state(config(), FakeGitHub(files={"o/a": status(source("x", "degraded"))}), NOW)
    assert amber.repos[0].level == "amber"


def test_alerts_cover_failing_degraded_missed_run_and_waiting_pr():
    old_pr = PullRequest(
        number=7, title="Repair x", url="u", created_at=NOW - timedelta(days=9), labels=["repair"]
    )
    github = FakeGitHub(
        files={
            "o/a": status(
                source("ok-one"),
                source(
                    "down", "failing", error_kind="transport", error="boom", consecutive_failures=3
                ),
                source("small", "degraded", note="records fell from 10 to 3"),
            ),
            "o/b": status(source("x"), age_hours=60),
        },
        pulls={"o/a": [old_pr]},
    )
    state = collect_state(config(), github, NOW)
    alerts = {a.key: a for a in compute_alerts(state, config())}
    assert set(alerts) == {
        "o/a:down:failing",
        "o/a:small:degraded",
        "o/a#7:waiting",
        "o/b:missed-run",
    }
    assert "3 run(s) in a row" in alerts["o/a:down:failing"].body
    assert "9 days" in alerts["o/a#7:waiting"].title


def test_unreadable_repo_alerts():
    state = collect_state(config(), FakeGitHub(), NOW)
    assert {a.key for a in compute_alerts(state, config())} == {"o/a:no-status", "o/b:no-status"}


def test_issues_open_once_stay_quiet_and_close_on_recovery():
    hub = "o/hub"
    github = FakeGitHub(
        files={"o/a": status(source("down", "failing")), "o/b": status(source("x"))}
    )
    alerts = compute_alerts(collect_state(config(), github, NOW), config())
    first = sync_issues(github, hub, alerts)
    assert first.opened == ["o/a:down:failing"] and first.closed == []
    assert github.issues[1]["labels"] == [LABEL, "source_failing"]
    again = sync_issues(github, hub, alerts)
    assert again.opened == [] and again.unchanged == 1  # a source that stays down does not nag
    assert [entry[0] for entry in github.log] == ["create"]

    github.files["o/a"] = status(source("down"))
    healed = compute_alerts(collect_state(config(), github, NOW), config())
    last = sync_issues(github, hub, healed)
    assert last.closed == ["o/a:down:failing"] and not github.issues[1]["open"]


def test_dry_run_changes_nothing():
    github = FakeGitHub(files={"o/a": status(source("down", "failing")), "o/b": status()})
    alerts = compute_alerts(collect_state(config(), github, NOW), config())
    result = sync_issues(github, "o/hub", alerts, dry_run=True)
    assert result.opened == ["o/a:down:failing"] and github.log == []


def test_weekly_digest_once_per_week_and_replaces_the_old_one():
    cfg = HubConfig(repos=["o/a", "o/b"], digest_weekday=0)
    github = FakeGitHub(files={"o/a": status(source("x")), "o/b": status(source("y"))})
    state = collect_state(cfg, github, NOW)
    assert weekly_digest(github, "o/hub", state, cfg) == "digest:2026-W42"
    assert weekly_digest(github, "o/hub", state, cfg) is None
    assert "| o/a | green | 1 | 10 |" in github.issues[1]["body"]
    next_week = state.model_copy(update={"generated_at": NOW + timedelta(days=7)})
    assert weekly_digest(github, "o/hub", next_week, cfg) == "digest:2026-W43"
    assert not github.issues[1]["open"] and github.issues[2]["open"]
    tuesday = state.model_copy(update={"generated_at": NOW + timedelta(days=1)})
    assert weekly_digest(github, "o/hub", tuesday, cfg) is None
    # the digest issue is not closed as a stale alert by the next sync
    assert sync_issues(github, "o/hub", []).closed == []


def test_http_github_speaks_the_rest_api():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("accept")))
        path = request.url.path
        if path.endswith("/contents/status.json"):
            return httpx.Response(200, content=b"{}")
        if path.endswith("/contents/missing.json"):
            return httpx.Response(404)
        if path.endswith("/pulls"):
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 3,
                        "title": "t",
                        "html_url": "u",
                        "created_at": "2026-10-01T00:00:00Z",
                        "labels": [{"name": "repair"}],
                    }
                ],
            )
        if path.endswith("/issues") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"number": 1, "title": "a", "body": "b"},
                    {"number": 2, "title": "pr", "pull_request": {}},
                ],
            )
        if path.endswith("/issues") and request.method == "POST":
            return httpx.Response(201, json={"number": 9})
        return httpx.Response(200, json={})

    api = HttpGitHub(
        "token",
        httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)),
    )
    assert (
        api.get_file("o/a", "status.json") == b"{}" and api.get_file("o/a", "missing.json") is None
    )
    assert api.open_pulls("o/a")[0].labels == ["repair"]
    assert [i.number for i in api.open_issues("o/hub", LABEL)] == [1]
    assert api.create_issue("o/hub", "t", "b", [LABEL]) == 9
    api.comment("o/hub", 9, "x")
    api.close_issue("o/hub", 9)
    assert ("GET", "/repos/o/a/contents/status.json", "application/vnd.github.raw+json") in seen


def test_cli_hub_run_dry_run(tmp_path, monkeypatch):
    import gleanyard.cli as cli_module

    github = FakeGitHub(
        files={"o/a": status(source("down", "failing")), "o/b": status(source("x"))}
    )
    monkeypatch.setattr(cli_module, "HttpGitHub", lambda token: github)
    monkeypatch.setenv("GH_TOKEN", "t")
    (tmp_path / "hub.yaml").write_text("repos: [o/a, o/b]\ndigest_weekday: null\n")
    result = runner.invoke(
        app,
        [
            "hub",
            "run",
            "--config",
            str(tmp_path / "hub.yaml"),
            "--state",
            str(tmp_path / "state.json"),
            "--hub-repo",
            "o/hub",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["alerts"] == ["o/a:down:failing"] and github.log == []
    assert json.loads((tmp_path / "state.json").read_text())["repos"][0]["repo"] == "o/a"
