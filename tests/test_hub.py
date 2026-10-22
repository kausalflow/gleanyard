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
from gleanyard.status import Check, ProjectStatus, SourceStatus

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # a Monday
runner = CliRunner()


def status(*sources: SourceStatus, age_hours: float = 2, now: datetime = NOW) -> bytes:
    return (
        ProjectStatus(
            generated_at=now - timedelta(hours=age_hours),
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
    def __init__(self, files=None, pulls=None, topics=None, pages=None):
        self.files, self.pulls = files or {}, pulls or {}
        self.topics, self.pages = topics or {}, pages or {}
        self.issues: dict[int, dict] = {}
        self.log: list[tuple] = []

    def find_repos(self, owner, topic):
        return [
            repo
            for repo, tags in self.topics.items()
            if repo.startswith(owner + "/") and topic in tags
        ]

    def pages_source(self, repo):
        return self.pages.get(repo)

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


def test_degraded_alerts_split_by_warning_kind():
    warnings = [
        Check(kind="fill_rate_drop", field="start_date", message="start_date filled in 0%"),
        Check(kind="fill_rate_drop", field="venue", message="venue filled in 10%"),
        Check(kind="rejection_spike", message="40% of records rejected, was 0%"),
    ]
    github = FakeGitHub(files={"o/a": status(source("s", "degraded", warnings=warnings))})
    alerts = {a.key: a for a in compute_alerts(collect_state(config(), github, NOW), config())}
    assert set(alerts) == {"o/a:s:fill_rate_drop", "o/a:s:rejection_spike", "o/b:no-status"}
    fill = alerts["o/a:s:fill_rate_drop"]
    assert fill.title == "o/a: s fields stopped filling"
    assert "- start_date filled in 0%" in fill.body and "- venue filled in 10%" in fill.body


def test_unreadable_repo_alerts():
    state = collect_state(config(), FakeGitHub(), NOW)
    assert {a.key for a in compute_alerts(state, config())} == {"o/a:no-status", "o/b:no-status"}


def test_issues_open_once_stay_quiet_and_close_on_recovery():
    hub = "o/hub"
    github = FakeGitHub(
        files={
            # `hub run` compares with the real clock, so these reports must be recent
            "o/a": status(source("down", "failing"), now=datetime.now(UTC)),
            "o/b": status(source("x"), now=datetime.now(UTC)),
        }
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
        files={
            # `hub run` compares with the real clock, so these reports must be recent
            "o/a": status(source("down", "failing"), now=datetime.now(UTC)),
            "o/b": status(source("x"), now=datetime.now(UTC)),
        }
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


def sample_state():
    pull = PullRequest(
        number=4,
        title="Repair <b>x</b>",
        url="https://github.com/o/a/pull/4",
        created_at=NOW - timedelta(days=3),
        labels=["repair"],
    )
    github = FakeGitHub(
        files={
            "o/a": status(
                source("fine"),
                source("down", "failing", error_kind="transport", error="<script>x</script>"),
            ),
            "o/b": status(source("ok")),
        },
        pulls={"o/a": [pull]},
    )
    return collect_state(config(), github, NOW)


def test_dashboard_is_escaped_static_html_with_levels():
    from gleanyard.hub.dashboard import render_dashboard

    page = render_dashboard(sample_state(), "Status <hub>")
    assert "<script" not in page.replace("<script>x", "")  # no scripts of ours
    assert "&lt;script&gt;x&lt;/script&gt;" in page and "<b>x</b>" not in page
    assert "Status &lt;hub&gt;" in page
    assert "class='dot red'" in page and "class='dot green'" in page
    assert page.index("o/a") < page.index("o/b")
    assert "1 red" in page and "1 green" in page and "3 day(s)" in page
    assert "<details open>" in page  # a repo that needs attention starts open


def test_cli_hub_build_and_init(tmp_path):
    state = sample_state()
    (tmp_path / "state.json").write_text(state.model_dump_json())
    result = runner.invoke(
        app, ["hub", "build", "--state", str(tmp_path / "state.json"), "--out", str(tmp_path / "s")]
    )
    assert result.exit_code == 0, result.output
    assert "o/a" in (tmp_path / "s" / "index.html").read_text()
    missing = runner.invoke(app, ["hub", "build", "--state", str(tmp_path / "none.json")])
    assert missing.exit_code == 2

    root = tmp_path / "collection-status"
    created = runner.invoke(app, ["hub", "init", str(root), "--repo", "o/a", "--repo", "o/b"])
    assert created.exit_code == 0, created.output
    assert load_hub_config(root / "hub.yaml").repos == ["o/a", "o/b"]
    import yaml

    workflow = yaml.safe_load((root / ".github/workflows/hub.yml").read_text())
    runs = [step.get("run", "") for step in workflow["jobs"]["hub"]["steps"]]
    assert any("gleanyard hub run" in run for run in runs)
    assert any("gleanyard hub build" in run for run in runs)
    assert (root / ".gitignore").exists() and "{{" not in (root / "README.md").read_text()
    assert runner.invoke(app, ["hub", "init", str(root), "--repo", "o/a"]).exit_code == 1
    assert runner.invoke(app, ["hub", "init", str(tmp_path / "x")]).exit_code == 2  # no repos


def fetcher(pages):
    def fetch(url):
        if url not in pages:
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=None)
        return pages[url]

    return fetch


def test_config_accepts_urls_and_discovery_and_needs_something(tmp_path):
    raw = "https://raw.githubusercontent.com/o/p/main/status.json"
    config = HubConfig(
        repos=["o/a", raw, {"name": "Markets", "url": "https://m.example/status.json"}],
        discover={"owner": "o"},
    )
    assert [(w.name, w.repo, w.url) for w in config.listed()] == [
        ("o/a", "o/a", None),
        ("o/p", None, raw),
        ("Markets", None, "https://m.example/status.json"),
    ]
    assert config.discover.topic == "gleanyard" and config.needs_github
    assert not HubConfig(repos=[raw]).needs_github
    path = tmp_path / "hub.yaml"
    path.write_text("missed_run_hours: 12\n")
    with pytest.raises(ConfigError, match="at least one repo"):
        load_hub_config(path)
    path.write_text("repos: [ftp://x]\n")
    with pytest.raises(ConfigError, match="owner/name or an http"):
        load_hub_config(path)


def test_state_reads_urls_without_github_and_adds_discovered_repos():
    url = "https://m.example/status.json"
    config = HubConfig(repos=["o/a", url], discover={"owner": "o"}, digest_weekday=None)
    github = FakeGitHub(
        files={"o/a": status(source("x")), "o/found": status(source("y", "failing"))},
        topics={"o/found": ["gleanyard"], "o/a": ["gleanyard"], "o/other": ["misc"]},
    )
    state = collect_state(config, github, NOW, fetch=fetcher({url: status(source("z"))}))
    assert [r.repo for r in state.repos] == ["o/a", "m.example", "o/found"]
    assert [r.level for r in state.repos] == ["green", "green", "red"]
    assert not state.repos[1].on_github and state.repos[1].pulls == []
    alone = collect_state(HubConfig(repos=[url]), None, NOW, fetch=fetcher({}))
    assert "could not fetch" in alone.repos[0].problem
    keys = {a.key for a in compute_alerts(alone, HubConfig(repos=[url]))}
    assert keys == {"m.example:no-status"}


def test_discovery_failure_is_an_alert():
    class Broken(FakeGitHub):
        def find_repos(self, owner, topic):
            raise httpx.ConnectError("down")

    state = collect_state(HubConfig(discover={"owner": "o"}), Broken(), NOW)
    alerts = compute_alerts(state, HubConfig(discover={"owner": "o"}))
    assert [a.key for a in alerts] == ["hub:problem"] and "could not search" in alerts[0].body


def test_cli_hub_run_without_a_token_for_url_only_hubs(tmp_path, monkeypatch):
    import gleanyard.hub.state as state_module

    url = "https://m.example/status.json"
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        state_module,
        "fetch_url",
        fetcher({url: status(source("x", "failing"), now=datetime.now(UTC))}),
    )
    (tmp_path / "hub.yaml").write_text(f"repos: [{url}]\ndigest_weekday: null\n")
    args = ["hub", "run", "--config", str(tmp_path / "hub.yaml"), "--state"]
    args += [str(tmp_path / "state.json"), "--dry-run", "--json"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["alerts"] == ["m.example:x:failing"]
    (tmp_path / "hub.yaml").write_text("repos: [o/a]\n")
    assert runner.invoke(app, args).exit_code == 2  # a repo needs a token


def test_hub_check_reports_each_problem_with_a_fix(tmp_path):
    from gleanyard.hub.check import check_hub

    url = "https://m.example/status.json"
    (tmp_path / "hub.yaml").write_text(f"repos: [o/a, o/private, {url}]\ndiscover: {{owner: o}}\n")
    workflow = tmp_path / "hub.yml"
    workflow.write_text("steps: [{uses: actions/deploy-pages@v4}]\n")
    github = FakeGitHub(files={"o/a": status(source("x"))}, pages={"o/hub": None})
    lines = check_hub(
        tmp_path / "hub.yaml", github, "o/hub", workflow, fetcher({url: status()}), NOW
    )
    failed = {line.what.split(":")[0]: line for line in lines if not line.ok}
    assert "no repos of o have the topic `gleanyard`" in failed
    assert "topic" in failed["no repos of o have the topic `gleanyard`"].fix
    assert "token read access" in failed["o/private"].fix
    assert "Pages" in lines[-1].what and "GitHub Actions" in lines[-1].fix
    assert any(line.ok and line.what.startswith("o/a: 1 sources") for line in lines)
    assert any(line.ok and line.what.startswith("m.example") for line in lines)

    workflow.write_text("steps: []\n")
    no_token = check_hub(tmp_path / "hub.yaml", None, None, workflow, fetcher({}), NOW)
    assert "no GitHub token" in no_token[1].what and "status.json URL" in no_token[1].fix
    assert "could not fetch" in no_token[2].what  # URL entries are still checked

    (tmp_path / "bad.yaml").write_text("repos: oops\n")
    assert not check_hub(tmp_path / "bad.yaml", None)[0].ok


def test_hub_init_alerts_only_by_default_and_pages_on_request(tmp_path):
    import yaml

    quiet = tmp_path / "quiet"
    assert runner.invoke(app, ["hub", "init", str(quiet), "--owner", "o"]).exit_code == 0
    config = load_hub_config(quiet / "hub.yaml")
    assert config.repos == [] and config.discover.owner == "o"
    workflow = yaml.safe_load((quiet / ".github/workflows/hub.yml").read_text())
    uses = [step.get("uses", "") for step in workflow["jobs"]["hub"]["steps"]]
    assert "pages" not in workflow["permissions"] and not any("pages" in u for u in uses)
    assert any("upload-artifact" in u for u in uses)
    assert "Nothing is published" in (quiet / "README.md").read_text()

    public = tmp_path / "public"
    created = runner.invoke(app, ["hub", "init", str(public), "--repo", "o/a", "--pages"])
    assert created.exit_code == 0, created.output
    workflow = yaml.safe_load((public / ".github/workflows/hub.yml").read_text())
    assert workflow["permissions"]["pages"] == "write"
    assert workflow["jobs"]["hub"]["environment"]["name"] == "github-pages"
    assert "public" in (public / "README.md").read_text()


def test_summary_lists_problems_for_the_job_summary():
    from gleanyard.hub.dashboard import render_summary

    text = render_summary(sample_state(), "Hub")
    assert text.startswith("# Hub")
    assert "🔴 **o/a**: 2 sources, 20 records, 1 need attention" in text
    assert "`down` failing: <script>x</script>" in text
    assert "waiting: [#4 Repair <b>x</b>](https://github.com/o/a/pull/4)" in text


def test_changed_terms_raise_an_alert_and_turn_the_repo_amber():
    from gleanyard.terms import TermsState

    terms = TermsState(
        reviewed_on="2026-10-01",
        changed=["https://x.example/impressum"],
        changed_at=NOW - timedelta(days=1),
    )
    github = FakeGitHub(files={"o/a": status(source("x", terms=terms)), "o/b": status(source("y"))})
    state = collect_state(config(), github, NOW)
    assert state.repos[0].level == "amber"
    alerts = {a.key: a for a in compute_alerts(state, config())}
    alert = alerts["o/a:terms_changed"]
    assert alert.kind == "terms_changed" and alert.title == "o/a: terms changed for x"
    assert "https://x.example/impressum" in alert.body and "2026-10-01" in alert.body


def test_sources_sharing_changed_terms_raise_one_alert_per_repo():
    from gleanyard.terms import TermsState

    def terms(*urls):
        return TermsState(reviewed_on="2026-10-01", changed=list(urls), changed_at=NOW)

    towns = [source(f"town-{n}", terms=terms("https://x.example/impressum")) for n in range(7)]
    towns[0] = source(
        "town-0", terms=terms("https://x.example/impressum", "https://x.example/robots.txt")
    )
    github = FakeGitHub(files={"o/a": status(*towns), "o/b": status(source("y"))})
    alerts = [a for a in compute_alerts(collect_state(config(), github, NOW), config())]
    terms_alerts = [a for a in alerts if a.kind == "terms_changed"]
    assert len(terms_alerts) == 1
    alert = terms_alerts[0]
    assert alert.title == "o/a: terms changed for 7 sources"
    assert "- https://x.example/impressum (7 source(s): `town-0`" in alert.body
    assert "and 2 more" in alert.body
    assert "- https://x.example/robots.txt (1 source(s): `town-0`)" in alert.body


def test_dashboard_folds_field_fill_rates_and_marks_drift():
    from gleanyard.hub.dashboard import drifting_fields, render_dashboard, render_summary

    drifting = source(
        "x",
        field_fill_rates={"title": 1.0, "venue": 0.6},
        baseline_fill_rates={"title": 1.0, "venue": 0.9},
    )
    assert drifting_fields(drifting) == ["venue"]
    state = collect_state(
        config(), FakeGitHub(files={"o/a": status(drifting), "o/b": status(source("y"))}), NOW
    )
    page = render_dashboard(state)
    assert "Fields (1 filling less than before)" in page and "bar drift" in page
    assert "fields filling less: venue" in render_summary(state)
