import json
from datetime import date
from pathlib import Path

import httpx
import yaml

from gleanyard.project import load_project
from gleanyard.run import collect_source, load_records

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
        "title": {"type": "string", "required": True},
        "start_date": {"type": "date", "required": True},
        "city": {"type": "string"},
        "source_url": {"type": "url", "required": True},
    },
}


def item(number: int, city: str | None) -> dict:
    row = {
        "title": f"Event {number}",
        "date": f"2026-11-{number:02d}",
        "url": f"https://example.org/e/{number}",
    }
    if city is not None:
        row["city"] = city
    return row


def make_project(root: Path, **overrides) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "schema.yaml").write_text(yaml.safe_dump(SCHEMA))
    source = {
        "id": "s",
        "schema": "event",
        "enabled": True,
        "delivery": "json",
        "url": "https://example.org/api/events?city=aachen",
        "reader": {"items_path": "items"},
        "extract": {
            "mode": "fields",
            "map": {
                "title": "title",
                "start_date": "date",
                "source_url": "url",
                "city": "city",
            },
        },
        "filter": {"field": "city", "one_of": ["Aachen"]},
        "fetch": {"delay_seconds": 0},
        "rights": RIGHTS,
        **overrides,
    }
    (root / "sources" / "s.yaml").write_text(yaml.safe_dump(source))
    return root


def api(items: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, content=json.dumps({"items": items}), headers={"content-type": "application/json"}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def run(tmp_path: Path, items: list[dict], **overrides):
    project = load_project(make_project(tmp_path / "p", **overrides))
    assert project.ok, project.issues
    return project, collect_source(project, project.sources[0], client=api(items), today=TODAY)


def test_records_inside_the_filter_pass(tmp_path):
    items = [item(n, "aachen") for n in range(1, 4)]
    project, result = run(tmp_path, items)
    assert result.ok and result.records == 3 and result.rejections == 0
    assert len(load_records(project, "s")) == 3


def test_a_source_that_ignores_the_filter_fails(tmp_path):
    items = [item(1, "Aachen"), item(2, "Berlin"), item(3, "Köln"), item(4, "Bonn")]
    _, result = run(tmp_path, items)
    assert not result.ok
    assert result.error_kind == "filter_ignored"
    assert "3 of 4" in result.error


def test_a_few_strays_go_to_the_review_file(tmp_path):
    items = [item(n, "Aachen") for n in range(1, 9)] + [item(9, "Berlin")]
    project, result = run(tmp_path, items)
    assert result.ok and result.records == 8 and result.rejections == 1
    review = (project.root / "data" / "review" / "s.jsonl").read_text()
    assert "outside the source filter" in review


def test_records_without_the_field_are_not_judged(tmp_path):
    items = [item(1, None), item(2, None), item(3, "Aachen")]
    _, result = run(tmp_path, items)
    assert result.ok and result.records == 3


def test_threshold_is_configurable(tmp_path):
    items = [item(1, "Aachen"), item(2, "Berlin")]
    _, result = run(
        tmp_path, items, filter={"field": "city", "one_of": ["Aachen"], "max_outside_share": 0.5}
    )
    assert result.ok and result.records == 1 and result.rejections == 1


def test_filter_field_must_be_in_the_schema(tmp_path):
    root = make_project(tmp_path / "p", filter={"field": "country", "one_of": ["DE"]})
    project = load_project(root)
    assert any("country" in issue.message for issue in project.issues)
