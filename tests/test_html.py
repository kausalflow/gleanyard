import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from gleanyard.errors import StructureChanged
from gleanyard.extract.jsonld import jsonld_nodes, lookup
from gleanyard.extract.selectors import SelectorSchema, run_selectors
from gleanyard.project import load_project
from gleanyard.run import collect_source, load_records

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 10)
RIGHTS = {
    "review_status": "approved",
    "effective_tier": "internal_only",
    "allowed_actions": {"private_store": True, "normalize": True},
}
SCHEMA = {
    "name": "event",
    "version": 1,
    "identity": ["title", "start_date"],
    "fields": {
        "title": {"type": "string", "required": True, "aliases": ["name"]},
        "start_date": {"type": "date", "required": True, "aliases": ["startDate"]},
        "time_text": {"type": "time_text"},
        "venue": {"type": "string", "aliases": ["location"]},
        "source_url": {"type": "url", "required": True, "aliases": ["url"]},
    },
}
FARM_MAPPING = {
    "baseSelector": "table.table tr",
    "fields": [
        {"name": "start_date", "selector": "td:nth-child(2)"},
        {"name": "time_text", "selector": "td:nth-child(3)"},
        {"name": "title", "selector": "td:nth-child(4)"},
        {"name": "source_url", "selector": "a", "type": "attribute", "attribute": "href"},
    ],
}


def make_project(root: Path, source: dict, mapping: dict | None = None) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "schema.yaml").write_text(yaml.safe_dump(SCHEMA))
    if mapping is not None:
        (root / "mappings").mkdir()
        (root / "mappings" / "m.json").write_text(json.dumps(mapping))
    source = {
        "id": "s",
        "schema": "event",
        "enabled": True,
        "fetch": {"delay_seconds": 0},
        "rights": RIGHTS,
        **source,
    }
    (root / "sources" / "s.yaml").write_text(yaml.safe_dump(source))
    return root


def site(pages: dict[str, bytes], calls: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if path in pages:
            return httpx.Response(200, content=pages[path])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def run(root: Path, client: httpx.Client, **kwargs):
    project = load_project(root)
    assert project.ok, project.issues
    return project, collect_source(
        project, project.sources[0], client=client, today=TODAY, **kwargs
    )


def test_selectors_runner_reads_rows():
    schema = SelectorSchema.model_validate(FARM_MAPPING)
    items = run_selectors("s", (FIXTURES / "farm.html").read_bytes(), schema)
    assert items[1] == {
        "start_date": "15.10.2026",
        "time_text": "11 Uhr",
        "title": "Synthetic apple day",
        "source_url": "/events/apple",
    }


def test_selector_field_types():
    html = (
        b'<ul><li data-id="7"><h2>Title</h2><span class=t>a</span><span class=t>b</span>'
        b"<p>Price: 12 EUR</p><div class=x><i>x</i></div></li></ul>"
    )
    schema = SelectorSchema.model_validate(
        {
            "baseSelector": "li",
            "baseFields": [{"name": "id", "type": "attribute", "attribute": "data-id"}],
            "fields": [
                {"name": "title", "selector": "h2", "transform": "uppercase"},
                {"name": "tags", "selector": ".t", "type": "list"},
                {"name": "price", "selector": "p", "type": "regex", "pattern": r"(\d+) EUR"},
                {"name": "box", "selector": ".x", "type": "html"},
                {"name": "missing", "selector": ".nope", "default": "n/a"},
                {
                    "name": "pair",
                    "selector": ".x",
                    "type": "nested",
                    "fields": [{"name": "k", "selector": "i"}],
                },
            ],
        }
    )
    assert run_selectors("s", html, schema) == [
        {
            "id": "7",
            "title": "TITLE",
            "tags": ["a", "b"],
            "price": "12",
            "box": "<i>x</i>",
            "missing": "n/a",
            "pair": {"k": "x"},
        }
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {"baseSelector": "li[", "fields": [{"name": "a"}]},
        {"baseSelector": "li", "fields": [{"name": "a", "type": "attribute"}]},
        {"baseSelector": "li", "fields": [{"name": "a"}, {"name": "a"}]},
        {"baseSelector": "li", "fields": []},
    ],
)
def test_bad_selector_schema_is_rejected(bad):
    with pytest.raises(ValueError):
        SelectorSchema.model_validate(bad)


def test_empty_page_is_a_structure_change():
    with pytest.raises(StructureChanged):
        run_selectors("s", b"  ", SelectorSchema.model_validate(FARM_MAPPING))


def test_jsonld_finds_nodes_in_graph_and_multi_type():
    nodes = jsonld_nodes("s", (FIXTURES / "event-page.html").read_bytes(), ["Event"])
    assert [node["name"] for node in nodes] == ["Synthetic concert", "Synthetic reading"]
    assert lookup(nodes[0], "offers.price") == "12.50"
    assert lookup(nodes[0], "location") == "Synthetic Hall"
    assert lookup(nodes[0], "nothing.here") is None


def test_selectors_source_end_to_end(tmp_path):
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "html",
            "url": "https://farm.example/events",
            "extract": {
                "mode": "selectors",
                "selectors": "mappings/m.json",
                "defaults": {"venue": "Synthetic Farm"},
            },
        },
        FARM_MAPPING,
    )
    body = (FIXTURES / "farm.html").read_bytes()
    project, result = run(root, site({"/events": body}))
    assert result.ok and result.records == 2 and result.rejections == 2  # header + bad date
    records = load_records(project, "s")
    assert records[0].values["source_url"] == "https://farm.example/events/apple"
    assert records[0].values["venue"] == "Synthetic Farm"
    assert records[0].provenance.mode == "selectors"


def test_jsonld_source_needs_no_mapping(tmp_path):
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "html",
            "url": "https://club.example/events",
            "extract": {"mode": "jsonld", "types": ["Event"]},
        },
    )
    project, result = run(root, site({"/events": (FIXTURES / "event-page.html").read_bytes()}))
    assert result.ok and result.records == 2
    first = load_records(project, "s")[0].values
    assert first["venue"] == "Synthetic Hall"
    assert first["source_url"] == "https://club.example/e/concert"


def test_sitemap_fetches_each_page_and_replays_from_cache(tmp_path):
    sitemap = (
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://club.example/a</loc></url>"
        b"<url><loc>https://club.example/b</loc></url>"
        b"<url><loc>https://club.example/skip</loc></url></urlset>"
    )
    page = (FIXTURES / "event-page.html").read_bytes()
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "sitemap",
            "url": "https://club.example/sitemap.xml",
            "reader": {"include": "/(a|b)$"},
            "extract": {"mode": "jsonld", "types": ["Event"]},
        },
    )
    calls: list[str] = []
    project, result = run(root, site({"/sitemap.xml": sitemap, "/a": page, "/b": page}, calls))
    assert result.ok and result.records == 2  # the same events on both pages count once
    assert "/skip" not in calls

    calls.clear()
    again = collect_source(
        project, project.sources[0], from_cache=True, client=site({}, calls), today=TODAY
    )
    assert again.ok and again.records == 2 and calls == []


def test_missing_selector_file_is_reported_by_validate(tmp_path):
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "html",
            "url": "https://farm.example/events",
            "extract": {"mode": "selectors", "selectors": "mappings/m.json"},
        },
    )
    assert any("not found" in issue.message for issue in load_project(root).issues)
