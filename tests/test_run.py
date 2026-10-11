import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.project import load_project
from gleanyard.rights import AllowedActions
from gleanyard.run import _store, _write_jsonl, collect_source, export_project, load_records

EXAMPLE = Path(__file__).parents[1] / "examples" / "events"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "events"
    shutil.copytree(EXAMPLE, root)
    path = root / "sources" / "aachen-city-events.yaml"
    document = yaml.safe_load(path.read_text())
    document["enabled"] = True
    document["fetch"] = {"delay_seconds": 0}
    document["reader"]["past_days"] = 0
    document["reader"]["horizon_days"] = 30
    document["rights"] = {
        "review_status": "approved",
        "effective_tier": "facts_only",
        "allowed_actions": {"private_store": True, "normalize": True, "redistribute_facts": True},
        "public_fields": ["title", "start_date", "source_url"],
    }
    path.write_text(yaml.safe_dump(document))
    return root


def client(body: bytes, calls: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "text/calendar"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_collect_then_export_then_reextract(project_dir):
    project = load_project(project_dir)
    assert project.ok, project.issues
    source = next(item for item in project.sources if item.id == "aachen-city-events")
    body = (FIXTURES / "calendar.ics").read_bytes()

    run = collect_source(project, source, client=client(body), today=date(2026, 10, 10))
    assert run.ok and run.records > 0, run
    records = load_records(project, source.id)
    assert {record.provenance.mode for record in records} == {"fields"}

    path, count = export_project(project)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert count == len(rows) > 0
    assert set(rows[0]) == {"id", "title", "start_date", "source_url", "_source"}

    calls = []
    again = collect_source(
        project, source, from_cache=True, client=client(b"", calls), today=date(2026, 10, 10)
    )
    assert again.ok and again.records == run.records and again.from_cache
    assert calls == []
    assert load_project(project_dir).sources  # still valid


def test_records_with_unicode_line_separators_load(project_dir):
    # Organisers paste text with U+2028, U+0085 or form feeds; JSON keeps them raw inside
    # strings, so the record store must split on "\n" only.
    project = load_project(project_dir)
    source = next(item for item in project.sources if item.id == "aachen-city-events")
    body = (FIXTURES / "calendar.ics").read_bytes()
    collect_source(project, source, client=client(body), today=date(2026, 10, 10))
    records = load_records(project, source.id)
    records[0].values["title"] = "Parodien\u2028einmalig\x85gut\x0c."
    _write_jsonl(_store(project), f"records/{source.id}.jsonl", records)

    again = load_records(project, source.id)
    assert len(again) == len(records)
    assert again[0].values["title"] == "Parodien\u2028einmalig\x85gut\x0c."
    export_project(project)


def test_collect_refuses_without_rights(project_dir):
    project = load_project(project_dir)
    source = next(item for item in project.sources if item.id == "gut-hixholz-farm-events")
    source = source.model_copy(
        update={"rights": source.rights.model_copy(update={"allowed_actions": AllowedActions()})}
    )
    run = collect_source(project, source)
    assert not run.ok and run.error_kind == "rights"


def test_from_cache_without_cache_is_reported(project_dir):
    project = load_project(project_dir)
    source = project.sources[0]
    run = collect_source(project, source, from_cache=True)
    assert run.error_kind == "no_cache"


def test_cli_collect_unknown_source(project_dir):
    result = CliRunner().invoke(app, ["collect", str(project_dir), "--source", "nope"])
    assert result.exit_code == 2
    assert "unknown source" in result.output
