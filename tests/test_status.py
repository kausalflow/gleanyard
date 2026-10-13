import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from test_html import FARM_MAPPING, make_project
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.project import load_project
from gleanyard.run import SourceRun
from gleanyard.status import build_status, load_status, write_status

T0 = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path):
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "html",
            "url": "https://farm.example/events",
            "extract": {"mode": "selectors", "selectors": "mappings/m.json"},
        },
        FARM_MAPPING,
    )
    return load_project(root)


def ok(records: int) -> SourceRun:
    return SourceRun(source_id="s", ok=True, records=records)


def failed() -> SourceRun:
    return SourceRun(source_id="s", ok=False, error_kind="transport", error="down")


def test_first_run_is_ok(project):
    status = build_status(project, [ok(10)], None, T0)
    entry = status.sources[0]
    assert (entry.health, entry.records, entry.last_success_at) == ("ok", 10, T0)
    assert status.healthy


def test_failures_count_and_keep_last_good_state(project):
    first = build_status(project, [ok(10)], None, T0)
    second = build_status(project, [failed()], first, T0 + timedelta(days=1))
    third = build_status(project, [failed()], second, T0 + timedelta(days=2))
    entry = third.sources[0]
    assert entry.health == "failing" and entry.consecutive_failures == 2
    assert entry.last_success_at == T0 and entry.records == 10
    assert not third.healthy
    recovered = build_status(project, [ok(9)], third, T0 + timedelta(days=3))
    assert recovered.sources[0].health == "ok"
    assert recovered.sources[0].consecutive_failures == 0
    assert recovered.sources[0].previous_records == 10


def test_big_drop_is_degraded_and_keeps_its_baseline(project):
    first = build_status(project, [ok(10)], None, T0)
    drop = build_status(project, [ok(3)], first, T0 + timedelta(days=1))
    assert drop.sources[0].health == "degraded"
    assert "10 to 3" in drop.sources[0].note
    again = build_status(project, [ok(4)], drop, T0 + timedelta(days=2))
    assert again.sources[0].health == "degraded"  # still compared with 10, not with 3
    assert not again.healthy


def filled(records: int, rates: dict[str, float], rejections: int = 0, digest: str = "a"):
    return SourceRun(
        source_id="s",
        ok=True,
        records=records,
        rejections=rejections,
        field_fill_rates=rates,
        records_digest=digest,
    )


def test_fill_rate_drop_is_degraded_and_keeps_its_baseline(project):
    first = build_status(project, [filled(10, {"title": 1.0, "start_date": 0.9})], None, T0)
    broken = build_status(
        project, [filled(10, {"title": 1.0, "start_date": 0.1})], first, T0 + timedelta(days=1)
    )
    entry = broken.sources[0]
    assert entry.health == "degraded"
    assert [(c.kind, c.field) for c in entry.warnings] == [("fill_rate_drop", "start_date")]
    assert "start_date filled in 10% of records, was 90%" in entry.note
    # The next run still compares with the good run, not with the broken one.
    again = build_status(
        project, [filled(10, {"title": 1.0, "start_date": 0.2})], broken, T0 + timedelta(days=2)
    )
    assert again.sources[0].health == "degraded"
    assert again.sources[0].baseline_fill_rates["start_date"] == 0.9
    fixed = build_status(
        project, [filled(10, {"title": 1.0, "start_date": 0.85})], again, T0 + timedelta(days=3)
    )
    assert fixed.sources[0].health == "ok" and not fixed.sources[0].warnings


def test_a_field_that_disappears_counts_and_rare_fields_are_ignored(project):
    first = build_status(project, [filled(10, {"title": 1.0, "age_min": 0.3})], None, T0)
    later = build_status(project, [filled(10, {"age_min": 0.0})], first, T0 + timedelta(days=1))
    assert [c.field for c in later.sources[0].warnings] == ["title"]


def test_rejection_spike_is_degraded(project):
    first = build_status(project, [filled(20, {}, rejections=1)], None, T0)
    spike = build_status(project, [filled(16, {}, rejections=9)], first, T0 + timedelta(days=1))
    kinds = [c.kind for c in spike.sources[0].warnings]
    assert kinds == ["rejection_spike"]
    assert "36% of records rejected, was 5%" in spike.sources[0].note


def test_a_few_rejections_are_not_a_spike(project):
    first = build_status(project, [filled(4, {})], None, T0)
    later = build_status(project, [filled(4, {}, rejections=2)], first, T0 + timedelta(days=1))
    assert later.sources[0].health == "ok"


def test_unchanged_records_warn_only_when_the_source_asks(project):
    days = [T0 + timedelta(days=n) for n in range(10)]
    status = None
    for day in days:
        status = build_status(project, [filled(5, {}, digest="same")], status, day)
    assert status.sources[0].health == "ok"  # off by default
    project.sources[0] = project.sources[0].model_copy(update={"expect_change_days": 7})
    status = build_status(project, [filled(5, {}, digest="same")], status, T0 + timedelta(days=10))
    entry = status.sources[0]
    assert [c.kind for c in entry.warnings] == ["unchanged"]
    assert entry.records_changed_at == T0
    changed = build_status(project, [filled(5, {}, digest="new")], status, T0 + timedelta(days=11))
    assert changed.sources[0].health == "ok"
    assert changed.sources[0].records_changed_at == T0 + timedelta(days=11)


def test_reports_without_baselines_use_the_last_healthy_entry(project):
    first = build_status(project, [filled(10, {"title": 1.0}, rejections=0)], None, T0)
    legacy = first.model_copy(deep=True)
    entry = legacy.sources[0]
    entry.baseline_fill_rates = {}
    entry.baseline_rejection_rate = None
    later = build_status(project, [filled(10, {"title": 0.0})], legacy, T0 + timedelta(days=1))
    assert [c.kind for c in later.sources[0].warnings] == ["fill_rate_drop"]


def test_seasonal_gap_is_not_a_drop(project):
    source = project.sources[0].model_copy(update={"expect_empty": "seasonal"})
    project.sources[0] = source
    first = build_status(project, [ok(10)], None, T0)
    empty = build_status(project, [ok(0)], first, T0 + timedelta(days=1))
    assert empty.sources[0].health == "ok"


def test_sources_not_run_keep_their_entry_and_disabled_is_marked(project):
    first = build_status(project, [ok(5)], None, T0)
    untouched = build_status(project, [], first, T0 + timedelta(days=1))
    assert untouched.sources[0] == first.sources[0]
    project.sources[0] = project.sources[0].model_copy(update={"enabled": False})
    assert build_status(project, [], first, T0).sources[0].health == "disabled"


def test_roundtrip(project):
    status = build_status(project, [ok(2)], None, T0)
    write_status(project, status)
    assert load_status(project) == status
    assert json.loads((project.root / "status.json").read_text())["version"] == 1


def test_cli_collect_writes_status_and_status_command(project, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(500)

    import gleanyard.run as run_module

    real = httpx.Client
    monkeypatch.setattr(
        run_module.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    root = str(project.root)
    assert runner.invoke(app, ["status", root]).exit_code == 1  # nothing yet
    result = runner.invoke(app, ["collect", root])
    assert result.exit_code == 1
    report = runner.invoke(app, ["status", root, "--json"])
    assert report.exit_code == 1
    assert json.loads(report.output)["sources"][0]["health"] == "failing"


def test_small_sources_skip_count_and_fill_checks(project):
    first = build_status(project, [filled(4, {"title": 1.0})], None, T0)
    later = build_status(project, [filled(2, {"title": 0.5})], first, T0 + timedelta(days=1))
    assert later.sources[0].health == "ok"
