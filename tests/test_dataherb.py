import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from test_html import FARM_MAPPING, make_project
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.config import DataherbConfig
from gleanyard.dataherb import build_job_status, build_metadata, write_job_status
from gleanyard.init import init_project
from gleanyard.project import load_project
from gleanyard.run import SourceRun
from gleanyard.status import build_status

T0 = datetime(2026, 10, 13, 5, 17, tzinfo=UTC)
OPEN = {
    "review_status": "approved",
    "effective_tier": "open",
    "license_id": "CC-BY-4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "allowed_actions": {"private_store": True, "normalize": True, "redistribute_facts": True},
    "public_fields": ["title", "start_date", "source_url"],
}
runner = CliRunner()


@pytest.fixture(autouse=True)
def no_github_env(monkeypatch):
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_REPOSITORY", "GITHUB_EVENT_NAME"):
        monkeypatch.delenv(name, raising=False)


def project_with(tmp_path: Path, rights: dict | None = None, dataherb: dict | None = None):
    source = {
        "delivery": "html",
        "url": "https://city.example/events",
        "extract": {"mode": "selectors", "selectors": "mappings/m.json"},
    }
    if rights:
        source["rights"] = rights
    root = make_project(tmp_path / "city-events", source, FARM_MAPPING)
    (root / "gleanyard.yaml").write_text(yaml.safe_dump({"dataherb": dataherb or {}}))
    project = load_project(root)
    assert project.ok, project.issues
    return project


def test_metadata_lists_the_export_with_its_schema_and_licence(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/city-events")
    project = project_with(tmp_path, OPEN, {"tags": ["events"]})
    doc = build_metadata(project, rows=12, size=3400)
    assert doc["id"] == "city-events"
    assert doc["status_job"] == "city-events"
    assert doc["license"] == "CC-BY-4.0"
    assert doc["classification"] == "public"
    assert doc["uri"] == "https://github.com/someone/city-events"
    (resource,) = doc["datapackage"]["resources"]
    assert resource["path"] == "exports/records.jsonl"
    assert (resource["format"], resource["rows"], resource["bytes"]) == ("ndjson", 12, 3400)
    types = {field["name"]: field["type"] for field in resource["schema"]["fields"]}
    assert types["start_date"] == "date"
    assert types["id"] == "string" and types["_source"] == "object"
    assert resource["schema"]["primaryKey"] == "id"


def test_metadata_lists_no_files_when_nothing_may_be_exported(tmp_path):
    project = project_with(tmp_path)  # internal_only
    doc = build_metadata(project, rows=0, size=0)
    assert doc["classification"] == "internal"
    assert doc["datapackage"]["resources"] == []
    assert "uri" not in doc and "license" not in doc


def test_export_writes_metadata_only_when_configured(tmp_path):
    project = project_with(tmp_path, OPEN)
    result = runner.invoke(app, ["export", str(project.root), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dataherb"].endswith("dataherb.json")
    assert json.loads((project.root / "dataherb.json").read_text())["id"] == "city-events"

    (project.root / "gleanyard.yaml").unlink()
    (project.root / "dataherb.json").unlink()
    result = runner.invoke(app, ["export", str(project.root), "--json"])
    assert "dataherb" not in json.loads(result.output)
    assert not (project.root / "dataherb.json").exists()


def ok_run(records=20) -> SourceRun:
    return SourceRun(source_id="s", ok=True, records=records, pages=1)


def failed_run() -> SourceRun:
    return SourceRun(source_id="s", ok=False, error_kind="transport", error="down")


def job_status(project, runs, previous=None, previous_report=None, now=T0):
    report = build_status(project, runs, previous_report, now=now)
    return build_job_status(
        project, report, runs, now - timedelta(minutes=2), previous, now
    ), report


def test_job_status_reports_a_good_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/city-events")
    project = project_with(tmp_path, dataherb={"expected_interval": "PT6H"})
    doc, _ = job_status(project, [ok_run()])
    assert doc["spec"] == "dataherb.status/v1"
    assert doc["job"]["id"] == "city-events"
    assert doc["job"]["expected_interval"] == "PT6H"
    assert doc["job"]["orchestrator"] == "github-actions"
    run = doc["run"]
    assert (run["id"], run["status"], run["trigger"]) == ("42-1", "success", "schedule")
    assert run["url"] == "https://github.com/someone/city-events/actions/runs/42"
    assert run["duration_seconds"] == 120
    assert doc["last_success"] == run
    assert doc["checks"] == [{"name": "s", "status": "pass", "value": 20}]
    assert doc["datasets"] == [{"id": "city-events", "rows": 20}]
    assert doc["producer"]["name"] == "gleanyard"


def test_failed_run_keeps_the_last_success(tmp_path):
    project = project_with(tmp_path)
    good, report = job_status(project, [ok_run()])
    doc, _ = job_status(
        project, [failed_run()], previous=good, previous_report=report, now=T0 + timedelta(days=1)
    )
    assert doc["run"]["status"] == "failed"
    assert doc["run"]["error"] == {"type": "transport", "message": "s: down"}
    assert doc["last_success"] == good["run"]
    assert doc["checks"][0]["status"] == "fail"
    assert doc["metrics"]["sources_failing"] == 1


def test_degraded_source_is_a_failed_check_with_its_warnings(tmp_path):
    project = project_with(tmp_path)
    _, report = job_status(project, [ok_run(100)])
    doc, _ = job_status(project, [ok_run(10)], previous_report=report, now=T0 + timedelta(days=1))
    assert doc["run"]["status"] == "success"
    source, warning = doc["checks"]
    assert source["status"] == "fail" and source["message"].startswith("degraded: ")
    assert warning == {
        "name": "s/record_drop",
        "status": "warn",
        "message": "records fell from 100 to 10",
    }
    assert doc["metrics"]["sources_degraded"] == 1


def test_write_job_status_writes_latest_and_a_run_file(tmp_path):
    project = project_with(tmp_path, dataherb={"id": "my-job", "status_dir": "status"})
    report = build_status(project, [ok_run()], None)
    latest = write_job_status(project, report, [ok_run()], T0)
    assert latest == project.root / "status" / "my-job" / "latest.json"
    (run_file,) = (latest.parent / "runs").iterdir()
    assert run_file.name.startswith("20261013T051700Z-")
    assert json.loads(run_file.read_text()) == json.loads(latest.read_text())


def test_bad_interval_is_rejected():
    with pytest.raises(ValueError, match="ISO 8601 duration"):
        DataherbConfig(expected_interval="daily")


def test_template_turns_dataherb_on(tmp_path):
    root = tmp_path / "new-project"
    init_project(root, "new-project", "event")
    project = load_project(root)
    assert project.config.dataherb is not None
    assert project.config.dataherb.id == "new-project"
