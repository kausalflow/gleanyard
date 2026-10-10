"""Phase 5 gate: a project with a different schema runs on the unchanged package.

The example is made with `gleanyard init`, then given a schema and three sources (a JSON API,
an RSS feed and a page with schema.org data). Nothing in it is about events.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import httpx
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.init import init_project
from gleanyard.project import load_project
from gleanyard.run import collect_source, load_records

EXAMPLE = Path(__file__).parents[1] / "examples" / "datasets"
FIXTURES = EXAMPLE / "tests" / "fixtures"
runner = CliRunner()
PAGES = {
    "data.example": ("application/json", "portal-api.json"),
    "city.example": ("application/rss+xml", "city-feed.rss"),
    "lab.example": ("text/html", "lab-pages.html"),
}


def build_project(tmp_path: Path) -> Path:
    root = tmp_path / "open-data-catalog"
    init_project(root, "open-data-catalog", "dataset")
    for name in ("schema.yaml", "research.md"):
        shutil.copy(EXAMPLE / name, root / name)
    shutil.copytree(EXAMPLE / "sources", root / "sources", dirs_exist_ok=True)
    return root


def transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        content_type, name = PAGES[request.url.host]
        return httpx.Response(
            200, content=(FIXTURES / name).read_bytes(), headers={"content-type": content_type}
        )

    return httpx.MockTransport(handler)


def client() -> httpx.Client:
    return httpx.Client(transport=transport())


def test_second_project_validates_and_collects(tmp_path, monkeypatch):
    root = build_project(tmp_path)
    project = load_project(root)
    assert project.ok, project.issues
    assert {source.delivery.value for source in project.sources} == {"json", "rss", "html"}

    runs = {
        source.id: collect_source(project, source, client=client(), today=date(2026, 10, 10))
        for source in project.sources
    }
    assert all(run.ok for run in runs.values()), runs
    assert {k: v.records for k, v in runs.items()} == {
        "portal-api": 2,
        "city-feed": 2,
        "lab-pages": 1,
    }
    air = next(r for r in load_records(project, "portal-api") if "air" in r.values["title"])
    assert air.values["formats"] == ["CSV", "JSON"]  # resources.*.format
    assert air.values["size_bytes"] == 1048576
    assert air.values["modified"].startswith("2026-09-01")
    lab = load_records(project, "lab-pages")[0].values
    assert lab["publisher"] == "Synthetic Soil Lab"
    assert lab["formats"] == ["CSV", "GeoJSON"]  # distribution.*.encodingFormat
    assert lab["modified"].startswith("2026-07-04")
    feed = {r.values["title"]: r.values for r in load_records(project, "city-feed")}
    assert feed["Synthetic school list"]["modified"].startswith("2026-08-17")  # RFC 2822 date


def test_second_project_exports_by_rights_and_reports_status(tmp_path, monkeypatch):
    import gleanyard.fetch as fetch_module

    root = build_project(tmp_path)
    real = httpx.Client
    monkeypatch.setattr(fetch_module.httpx, "Client", lambda **kw: real(transport=transport()))
    # keep the run quick: no delay between requests
    for path in (root / "sources").glob("*.yaml"):
        path.write_text(path.read_text() + "fetch: {delay_seconds: 0}\n")
    assert runner.invoke(app, ["collect", str(root)]).exit_code == 0
    assert runner.invoke(app, ["export", str(root)]).exit_code == 0
    rows = [
        json.loads(line) for line in (root / "exports" / "records.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 5
    assert all("description" not in row and "size_bytes" not in row for row in rows)
    assert {row["_source"]["source_id"] for row in rows} == {"portal-api", "city-feed", "lab-pages"}
    report = json.loads((root / "status.json").read_text())
    assert report["healthy"] is True and report["schema_name"] == "dataset"
    assert {s["health"] for s in report["sources"]} == {"ok"}
    assert runner.invoke(app, ["status", str(root)]).exit_code == 0
