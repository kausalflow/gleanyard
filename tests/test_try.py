import json

import httpx
import pytest
from test_html import FARM_MAPPING, FIXTURES, TODAY, make_project, site
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.project import load_project
from gleanyard.tryout import try_source

runner = CliRunner()
FARM = (FIXTURES / "farm.html").read_bytes()


@pytest.fixture
def project(tmp_path):
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


def run_try(project, body=FARM, **kwargs):
    return try_source(
        project, project.sources[0], client=site({"/events": body}), today=TODAY, **kwargs
    )


def test_noisy_selector_mapping_fails_on_rejections(project):
    report = run_try(project)
    # fixture: 2 good rows, a header row and a row with a bad date
    assert (report.records, report.rejections) == (2, 2)
    assert not report.passed
    assert any("rejected" in reason for reason in report.reasons)
    assert report.field_fill_rates["title"] == 1.0
    assert any("start_date" in key for key in report.rejection_reasons)


def test_clean_page_passes_and_nothing_is_written(project):
    clean = b"\n".join(
        line for line in FARM.splitlines() if b"<th>" not in line and b"nicht bekannt" not in line
    )
    report = run_try(project, clean)
    assert report.passed and report.records == 2
    assert report.samples[0]["title"] == "Synthetic apple day"
    assert not (project.root / "data").exists()
    assert not (project.root / "status.json").exists()


def test_empty_page_fails_unless_seasonal(project):
    empty = b"<html><body><table class='table'></table></body></html>"
    assert not run_try(project, empty).passed
    seasonal = project.sources[0].model_copy(update={"expect_empty": "seasonal"})
    project.sources[0] = seasonal
    assert run_try(project, empty).passed


def test_source_errors_are_reported_as_a_failed_try(project):
    def handler(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = try_source(project, project.sources[0], client=client, today=TODAY)
    assert not report.passed and report.error_kind == "transport"


def test_markdown_is_a_pr_description(project):
    text = run_try(project).markdown()
    assert text.startswith("## `try s`: **FAIL**")
    assert "| title |" in text and "Synthetic apple day" in text


def test_cli_try_json_and_unknown_source(project, monkeypatch):
    import gleanyard.fetch as fetch_module

    real = httpx.Client

    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=FARM)
        )

    monkeypatch.setattr(
        fetch_module.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    result = runner.invoke(app, ["try", "s", str(project.root), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["records"] == 2
    assert runner.invoke(app, ["try", "nope", str(project.root)]).exit_code != 0


def test_cli_probe_prints_ranked_options(monkeypatch, tmp_path):
    import gleanyard.probe as probe_module

    real = httpx.Client
    page = b'<html><head><link rel="alternate" type="text/calendar" href="/a.ics"></head></html>'

    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, content=page, headers={"content-type": "text/html"})
        )

    monkeypatch.setattr(
        probe_module.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    result = runner.invoke(app, ["probe", "https://hof.example/e", "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "1. ical/fields  https://hof.example/a.ics" in result.output
