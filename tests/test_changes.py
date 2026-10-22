import json
from datetime import UTC, date, datetime, timedelta

from test_run import FIXTURES, client, project_dir  # noqa: F401
from typer.testing import CliRunner

from gleanyard.changes import load_changes, prune_changes
from gleanyard.cli import app
from gleanyard.project import load_project
from gleanyard.run import collect_source, export_project, load_records, record_store

runner = CliRunner()
TODAY = date(2026, 10, 10)
FIRST = datetime(2026, 10, 10, 6, tzinfo=UTC)
SECOND = datetime(2026, 10, 11, 6, tzinfo=UTC)


def collect(project, body, now):
    source = next(item for item in project.sources if item.id == "aachen-city-events")
    return collect_source(project, source, client=client(body), today=TODAY, now=now)


def edited(body: bytes) -> bytes:
    return body.replace(b"SUMMARY:Herbstmarkt", b"SUMMARY:Wintermarkt").replace(
        b"URL:https://example.org/e/3", b"URL:https://example.org/e/3-new"
    )


def test_a_collect_logs_added_changed_and_removed_records(project_dir):  # noqa: F811
    project = load_project(project_dir)
    body = (FIXTURES / "calendar.ics").read_bytes()
    first = collect(project, body, FIRST)
    store = record_store(project)
    logged = load_changes(store)
    assert [c.change for c in logged] == ["added"] * first.records

    collect(project, edited(body), SECOND)
    later = [c for c in load_changes(store, since=SECOND.date())]
    kinds = sorted((c.change, c.record.values["title"]) for c in later)
    assert kinds == [("added", "Wintermarkt"), ("changed", "Late talk"), ("removed", "Herbstmarkt")]
    changed = next(c for c in later if c.change == "changed")
    assert changed.fields == ["source_url"]

    records = {r.values["title"]: r for r in load_records(project, "aachen-city-events")}
    late, new = records["Late talk"], records["Wintermarkt"]
    assert late.first_seen < late.provenance.retrieved_at
    assert new.first_seen == new.provenance.retrieved_at


def test_an_empty_failed_run_keeps_the_records_and_logs_nothing(project_dir):  # noqa: F811
    project = load_project(project_dir)
    collect(project, (FIXTURES / "calendar.ics").read_bytes(), FIRST)
    before = load_records(project, "aachen-city-events")
    run = collect(project, b"BEGIN:VCALENDAR\nEND:VCALENDAR\n", SECOND)
    assert not run.ok and run.error_kind == "empty"
    assert load_records(project, "aachen-city-events") == before
    assert load_changes(record_store(project), since=SECOND.date()) == []


def test_the_feed_shows_only_exported_fields_and_skips_hidden_changes(project_dir):  # noqa: F811
    project = load_project(project_dir)
    body = (FIXTURES / "calendar.ics").read_bytes()
    collect(project, body, FIRST)
    collect(project, edited(body).replace(b"DTEND;VALUE=DATE:20261019", b""), SECOND)
    export_project(project, today=SECOND.date())
    feed = json.loads((project_dir / "exports/changes.json").read_text())
    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    titles = [item["title"] for item in feed["items"][:3]]
    assert set(titles) >= {"Added: Wintermarkt / 2026-10-17", "Removed: Herbstmarkt / 2026-10-17"}
    for item in feed["items"]:
        record = item["_gleanyard"]["record"]
        assert set(record) <= {"id", "title", "start_date", "source_url", "_source"}
        assert "first_seen" in record["_source"]
    changed = [i for i in feed["items"] if i["_gleanyard"]["change"] == "changed"]
    assert [i["_gleanyard"]["fields"] for i in changed] == [["source_url"]]


def test_a_source_without_export_rights_stays_out_of_the_feed(project_dir):  # noqa: F811
    project = load_project(project_dir)
    collect(project, (FIXTURES / "calendar.ics").read_bytes(), FIRST)
    source = project_dir / "sources/aachen-city-events.yaml"
    source.write_text(
        source.read_text().replace("redistribute_facts: true", "redistribute_facts: false")
    )
    export_project(load_project(project_dir), today=FIRST.date())
    assert json.loads((project_dir / "exports/changes.json").read_text())["items"] == []


def test_old_change_logs_are_pruned(project_dir):  # noqa: F811
    project = load_project(project_dir)
    collect(project, (FIXTURES / "calendar.ics").read_bytes(), FIRST)
    store = record_store(project)
    assert prune_changes(store, 90, FIRST.date() + timedelta(days=90)) == []
    assert prune_changes(store, 90, FIRST.date() + timedelta(days=91)) == [
        "changes/2026-10-10.jsonl"
    ]


def test_cli_lists_changes(project_dir):  # noqa: F811
    project = load_project(project_dir)
    collect(project, (FIXTURES / "calendar.ics").read_bytes(), FIRST)
    result = runner.invoke(app, ["changes", str(project_dir), "--since", "2026-10-01"])
    assert result.exit_code == 0 and "added    aachen-city-events: Herbstmarkt" in result.stdout
    quiet = runner.invoke(app, ["changes", str(project_dir), "--since", "2026-11-01"])
    assert "no changes since 2026-11-01" in quiet.stdout
