"""The hub reads DataHerb job statuses (dataherb.status/v1) as well as status.json."""

import json
from datetime import timedelta

import pytest
from test_dataherb import (
    T0,
    failed_run,
    job_status,
    no_github_env,  # noqa: F401  (autouse: no GitHub Actions env)
    ok_run,
    project_with,
)
from test_hub import FakeGitHub, fetcher, source, status

from gleanyard.hub.alerts import compute_alerts
from gleanyard.hub.config import HubConfig
from gleanyard.hub.dashboard import render_dashboard
from gleanyard.hub.reports import parse_report
from gleanyard.hub.state import collect_state


def body(doc: dict) -> bytes:
    return json.dumps(doc).encode()


def test_a_good_gleanyard_run_reads_as_a_healthy_report(tmp_path):
    doc, report = job_status(project_with(tmp_path), [ok_run()])
    parsed = parse_report(body(doc))
    assert parsed.healthy and parsed.generated_at == T0
    assert [(s.id, s.health, s.records) for s in parsed.sources] == [("s", "ok", 20)]
    assert parsed.sources[0].last_success_at == T0
    assert parsed.schema_name == report.schema_name


def test_failing_and_degraded_sources_keep_their_alerts(tmp_path):
    project = project_with(tmp_path)
    good, report = job_status(project, [ok_run()])
    failed, _ = job_status(
        project, [failed_run()], previous=good, previous_report=report, now=T0 + timedelta(days=1)
    )
    parsed = parse_report(body(failed))
    (entry,) = parsed.sources
    assert (entry.health, entry.error) == ("failing", "down")
    assert entry.last_success_at == T0  # from last_success

    _, big = job_status(project, [ok_run(100)])
    dropped, _ = job_status(project, [ok_run(10)], previous_report=big, now=T0 + timedelta(days=1))
    (entry,) = parse_report(body(dropped)).sources
    assert entry.health == "degraded"
    assert [(w.kind, w.message) for w in entry.warnings] == [
        ("record_drop", "records fell from 100 to 10")
    ]


def test_another_producers_checks_and_warnings():
    doc = {
        "spec": "dataherb.status/v1",
        "job": {"id": "prices", "name": "Prices"},
        "run": {"status": "partial", "finished_at": "2026-10-12T06:00:00Z"},
        "checks": [
            {"name": "download", "status": "pass", "value": 7},
            {"name": "parse", "status": "fail", "message": "bad header"},
            {"name": "download/freshness", "status": "warn", "message": "file is 3 days old"},
        ],
    }
    parsed = parse_report(body(doc))
    assert parsed.schema_name == "Prices" and not parsed.healthy
    by_id = {s.id: s for s in parsed.sources}
    assert (by_id["download"].health, by_id["download"].records) == ("degraded", 7)
    assert [(w.kind, w.message) for w in by_id["download"].warnings] == [
        ("warning", "file is 3 days old")
    ]
    assert (by_id["parse"].health, by_id["parse"].error) == ("failing", "bad header")


@pytest.mark.parametrize(
    ("run_status", "health"), [("success", "ok"), ("partial", "degraded"), ("failed", "failing")]
)
def test_a_status_without_checks_is_one_source_named_after_the_job(run_status, health):
    doc = {
        "spec": "dataherb.status/v1",
        "job": {"id": "prices"},
        "run": {
            "status": run_status,
            "started_at": "2026-10-12T05:59:00Z",
            "error": {"type": "http", "message": "503"},
        },
        "datasets": [{"id": "prices", "rows": 12}],
    }
    (entry,) = parse_report(body(doc)).sources
    assert (entry.id, entry.health, entry.records) == ("prices", health, 12)
    if health != "ok":
        assert (entry.error_kind, entry.error) == ("http", "503")


def test_a_status_with_no_time_is_not_a_report():
    with pytest.raises(ValueError, match="no written_at"):
        parse_report(body({"spec": "dataherb.status/v1", "run": {}}))


def test_hub_reads_dataherb_by_url_and_from_a_repo_path(tmp_path):
    good, _ = job_status(project_with(tmp_path), [ok_run()], now=T0)
    bad, _ = job_status(project_with(tmp_path / "b"), [failed_run()], now=T0)
    url = "https://raw.githubusercontent.com/o/prices/main/_dataherb/status/prices/latest.json"
    path = "_dataherb/status/city-events/latest.json"

    class PathGitHub(FakeGitHub):
        def get_file(self, repo, wanted):
            return self.files.get((repo, wanted))

    github = PathGitHub(
        files={("o/a", "status.json"): status(source("x"), now=T0), ("o/b", path): body(bad)}
    )
    config = HubConfig(
        repos=["o/a", {"repo": "o/b", "status_path": path, "name": "City"}, url],
        digest_weekday=None,
    )
    assert config.needs_github
    state = collect_state(config, github, T0, fetch=fetcher({url: body(good)}))
    assert [(r.repo, r.level, r.problem) for r in state.repos] == [
        ("o/a", "green", None),
        ("City", "red", None),
        ("o/prices", "green", None),
    ]
    assert {a.key for a in compute_alerts(state, config)} == {"City:s:failing"}
    page = render_dashboard(state)
    assert ">latest.json</a>" in page


def test_status_repo_entries_are_validated():
    with pytest.raises(ValueError, match="listed twice"):
        HubConfig(repos=["o/a", {"repo": "o/a", "status_path": "x.json"}])
    with pytest.raises(ValueError):
        HubConfig(repos=[{"repo": "not a repo", "status_path": "x.json"}])
