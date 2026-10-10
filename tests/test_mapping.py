import json
from pathlib import Path

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.errors import ConfigError, ModelError
from gleanyard.mapping import crawl4ai_generator, draft_mapping
from gleanyard.project import load_project

FIXTURES = Path(__file__).parent / "fixtures"
RIGHTS = {
    "review_status": "approved",
    "effective_tier": "internal_only",
    "allowed_actions": {"private_store": True, "normalize": True, "send_to_model": True},
}
SCHEMA = {
    "name": "event",
    "version": 1,
    "description": "A public event",
    "identity": ["title", "start_date"],
    "fields": {
        "title": {"type": "string", "required": True},
        "start_date": {"type": "date", "required": True},
        "time_text": {"type": "time_text"},
        "source_url": {"type": "url", "required": True},
    },
}
DRAFT = {
    "name": "events",
    "baseSelector": "table.table tr",
    "fields": [
        {"name": "start_date", "selector": "td:nth-child(2)"},
        {"name": "title", "selector": "td:nth-child(4)"},
        {"name": "time_text", "selector": "td:nth-child(3)"},
        {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"},
    ],
}


def make_project(root: Path, rights: dict | None = None, mode: str = "selectors") -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "schema.yaml").write_text(yaml.safe_dump(SCHEMA))
    extract = (
        {"mode": "selectors", "selectors": "mappings/farm.json"}
        if mode == "selectors"
        else {"mode": "jsonld", "types": ["Event"]}
    )
    source = {
        "id": "farm",
        "schema": "event",
        "enabled": True,
        "delivery": "html",
        "url": "https://example.org/events",
        "extract": extract,
        "fetch": {"delay_seconds": 0},
        "rights": rights or RIGHTS,
    }
    (root / "sources" / "farm.yaml").write_text(yaml.safe_dump(source))
    return root


def site() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=(FIXTURES / "farm.html").read_bytes())

    return httpx.Client(transport=httpx.MockTransport(handler))


class Recorder:
    def __init__(self, draft):
        self.draft = draft
        self.calls: list[tuple] = []

    def __call__(self, html, query, example, model):
        self.calls.append((html, query, example, model))
        if isinstance(self.draft, Exception):
            raise self.draft
        return self.draft


def project_at(tmp_path: Path, **kwargs):
    project = load_project(make_project(tmp_path / "p", **kwargs))
    # The mapping file is missing on purpose; that is the only issue allowed.
    assert [issue.path for issue in project.issues] in ([], ["extract.selectors"])
    return project


def test_draft_is_validated_run_and_written(tmp_path):
    project = project_at(tmp_path)
    generator = Recorder(DRAFT)
    report = draft_mapping(
        project, project.sources[0], generator=generator, model="some/model", client=site()
    )
    assert report.ok and report.written
    assert report.records >= 2
    assert report.unknown_fields == ["link"]
    assert report.missing_required == ["source_url"]
    written = json.loads((project.root / "mappings" / "farm.json").read_text())
    assert written["baseSelector"] == "table.table tr"
    html, query, example, model = generator.calls[0]
    assert "table" in html and model == "some/model"
    assert "- title (string, required)" in query and "A public event" in query
    assert json.loads(example)["title"] == ""
    assert load_project(project.root).ok  # the file now exists, so the source validates


def test_dry_run_writes_nothing(tmp_path):
    project = project_at(tmp_path)
    report = draft_mapping(
        project, project.sources[0], generator=Recorder(DRAFT), client=site(), dry_run=True
    )
    assert report.ok and not report.written
    assert not (project.root / "mappings" / "farm.json").exists()


def test_existing_mapping_needs_force(tmp_path):
    project = project_at(tmp_path)
    (project.root / "mappings").mkdir()
    (project.root / "mappings" / "farm.json").write_text("{}")
    with pytest.raises(ConfigError, match="--force"):
        draft_mapping(project, project.sources[0], generator=Recorder(DRAFT), client=site())
    report = draft_mapping(
        project, project.sources[0], generator=Recorder(DRAFT), client=site(), force=True
    )
    assert report.written


def test_unusable_draft_is_reported_not_written(tmp_path):
    project = project_at(tmp_path)
    bad = {"baseSelector": "li[", "fields": [{"name": "a"}]}
    report = draft_mapping(project, project.sources[0], generator=Recorder(bad), client=site())
    assert not report.ok and "not a usable mapping" in report.error
    assert not (project.root / "mappings" / "farm.json").exists()


def test_draft_that_finds_nothing_is_not_written(tmp_path):
    project = project_at(tmp_path)
    empty = {"baseSelector": "section.nothing", "fields": [{"name": "title"}]}
    report = draft_mapping(project, project.sources[0], generator=Recorder(empty), client=site())
    assert not report.ok and "finds no records" in report.error
    assert not report.written


def test_generator_failure_is_a_model_error(tmp_path):
    project = project_at(tmp_path)
    with pytest.raises(ModelError, match="boom"):
        draft_mapping(
            project, project.sources[0], generator=Recorder(RuntimeError("boom")), client=site()
        )


def test_map_needs_send_to_model(tmp_path):
    rights = {**RIGHTS, "allowed_actions": {"private_store": True, "normalize": True}}
    project = project_at(tmp_path, rights=rights)
    with pytest.raises(ConfigError, match="send_to_model"):
        draft_mapping(project, project.sources[0], generator=Recorder(DRAFT), client=site())


def test_map_only_for_selector_sources(tmp_path):
    project = project_at(tmp_path, mode="jsonld")
    with pytest.raises(ConfigError, match="selector mapping"):
        draft_mapping(project, project.sources[0], generator=Recorder(DRAFT), client=site())


def test_missing_crawl4ai_says_how_to_install(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "crawl4ai", None)
    with pytest.raises(ConfigError, match=r"gleanyard\[map\]"):
        crawl4ai_generator("<html></html>", "q", "{}", None)


def test_cli_map_reports_install_hint(tmp_path, monkeypatch):
    root = make_project(tmp_path / "p")
    monkeypatch.setitem(__import__("sys").modules, "crawl4ai", None)
    result = CliRunner().invoke(app, ["map", "farm", str(root)], catch_exceptions=False)
    assert result.exit_code == 2
    assert "gleanyard[map]" in result.output


def test_cli_map_unknown_source(tmp_path):
    root = make_project(tmp_path / "p")
    result = CliRunner().invoke(app, ["map", "nope", str(root)])
    assert result.exit_code != 0
