import json
from pathlib import Path

import httpx
import yaml
from test_html import make_project
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.config import SourceConfig
from gleanyard.project import load_project
from gleanyard.scaffold import scaffold

UA = "test-agent"
runner = CliRunner()
ICS = (
    b"BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:1\nSUMMARY:Synthetic\n"
    b"DTSTART:20261020T180000Z\nLOCATION:Hall\nURL:https://hof.example/e/1\nEND:VEVENT\n"
    b"END:VCALENDAR\n"
)
PAGE = b'<html><head><link rel="alternate" type="text/calendar" href="/a.ics"></head></html>'
EVENTS_JSON = json.dumps(
    {"meta": {}, "events": [{"name": "Synthetic", "startDate": "2026-10-20", "url": "/e/1"}]}
).encode()


def serve(pages):
    def handler(request):
        if request.url.path in pages:
            ctype, body = pages[request.url.path]
            return httpx.Response(200, content=body, headers={"content-type": ctype})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


EXAMPLE = Path(__file__).parents[1] / "examples" / "events"


def schema_of(tmp_path):
    """The example project's schema (its aliases name iCal and schema.org keys)."""
    return load_project(EXAMPLE)


def test_ical_feed_is_mapped_through_aliases(tmp_path):
    project = schema_of(tmp_path)
    client = serve({"/events": ("text/html", PAGE), "/a.ics": ("text/calendar", ICS)})
    draft = scaffold(
        "https://hof.example/events", "hof", project.schema, user_agent=UA, client=client
    )
    document = yaml.safe_load(draft.yaml_text)
    assert document["delivery"] == "ical" and document["enabled"] is False
    assert document["homepage"] == "https://hof.example/events"
    assert document["extract"]["map"] == {
        "title": "SUMMARY",
        "start_date": "DTSTART",
        "venue": "LOCATION",
        "source_url": "URL",
    }
    assert draft.unmapped_required == []
    SourceConfig.model_validate(document)  # the draft is a valid source file


def test_json_feed_finds_the_item_list_and_nested_keys(tmp_path):
    project = schema_of(tmp_path)
    client = serve({"/api": ("application/json", EVENTS_JSON)})
    draft = scaffold("https://hof.example/api", "hof", project.schema, user_agent=UA, client=client)
    document = yaml.safe_load(draft.yaml_text)
    assert document["reader"] == {"items_path": "events"}
    assert document["extract"]["map"]["start_date"] == "startDate"
    assert draft.unmapped_required == []


def test_missing_required_fields_are_reported(tmp_path):
    project = schema_of(tmp_path)
    ics = (
        b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:1\nSUMMARY:x\nDTSTART:20261020T180000Z\n"
        b"END:VEVENT\nEND:VCALENDAR\n"
    )
    client = serve({"/a.ics": ("text/calendar", ics)})
    draft = scaffold(
        "https://hof.example/a.ics", "hof", project.schema, user_agent=UA, client=client
    )
    assert draft.unmapped_required == ["source_url"]
    assert any("source_url" in note for note in draft.notes)


def test_jsonld_and_plain_page_skeletons(tmp_path):
    project = schema_of(tmp_path)
    ld = (
        b'<html><script type="application/ld+json">'
        b'{"@type":"Event","name":"x","startDate":"2026-10-20"}</script></html>'
    )
    draft = scaffold(
        "https://hof.example/e",
        "hof",
        project.schema,
        user_agent=UA,
        client=serve({"/e": ("text/html", ld)}),
    )
    assert yaml.safe_load(draft.yaml_text)["extract"] == {"mode": "jsonld", "types": ["Event"]}
    plain = scaffold(
        "https://hof.example/e",
        "hof",
        project.schema,
        user_agent=UA,
        client=serve({"/e": ("text/html", b"<html></html>")}),
    )
    assert yaml.safe_load(plain.yaml_text)["extract"]["mode"] == "selectors"
    assert any("crawl4ai" in note for note in plain.notes)


def test_cli_check_reports_sources_candidates_and_excluded(tmp_path):
    project = load_project(
        make_project(
            tmp_path / "p",
            {
                "delivery": "ical",
                "url": "https://hof.example/a.ics",
                "extract": {"mode": "fields", "map": {}},
            },
        )
    )
    root = str(project.root)
    (project.root / "candidates").mkdir()
    (project.root / "candidates" / "cand.example.yaml").write_text("reason: x\n")
    (project.root / "gleanyard.yaml").write_text("excluded_hosts: [bad.example]\n")
    ask = lambda url: runner.invoke(app, ["check", url, root, "--json"])  # noqa: E731
    assert json.loads(ask("https://hof.example/x").output)["status"] == "source"
    assert json.loads(ask("https://cand.example/").output)["status"] == "candidate"
    assert json.loads(ask("https://bad.example/").output)["status"] == "excluded"
    fresh = ask("https://new.example/")
    assert fresh.exit_code == 0 and json.loads(fresh.output)["status"] == "new"
    assert ask("https://hof.example/").exit_code == 1
