import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from gleanyard.config import Pagination
from gleanyard.project import load_project
from gleanyard.run import collect_source, load_records, page_url

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
        "source_url": {"type": "url", "required": True},
    },
}


def item(number: int) -> dict:
    return {
        "title": f"Event {number}",
        "date": f"2026-11-{number:02d}",
        "url": f"https://example.org/e/{number}",
    }


def make_project(root: Path, **overrides) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "schema.yaml").write_text(yaml.safe_dump(SCHEMA))
    source = {
        "id": "s",
        "schema": "event",
        "enabled": True,
        "delivery": "json",
        "url": "https://example.org/api/events?lang=en",
        "reader": {"items_path": "items"},
        "extract": {
            "mode": "fields",
            "map": {"title": "title", "start_date": "date", "source_url": "url"},
        },
        "pagination": {"param": "page", "start": 1, "max_pages": 5},
        "fetch": {"delay_seconds": 0},
        "rights": RIGHTS,
        **overrides,
    }
    (root / "sources" / "s.yaml").write_text(yaml.safe_dump(source))
    return root


def api(pages: dict[int, list[dict]], seen: list[str] | None = None, ignore_param=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if seen is not None:
            seen.append(str(request.url))
        number = 1 if ignore_param else int(request.url.params.get("page", "1"))
        body = json.dumps({"items": pages.get(number, [])})
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def run(tmp_path: Path, client: httpx.Client, **overrides):
    project = load_project(make_project(tmp_path / "p", **overrides))
    assert project.ok, project.issues
    return project, collect_source(project, project.sources[0], client=client, today=TODAY)


def test_page_url_sets_and_replaces_the_parameter():
    pagination = Pagination(param="offset", start=0, step=25)
    assert page_url("https://x.org/a?lang=en", pagination, 0) == "https://x.org/a?lang=en&offset=0"
    assert page_url("https://x.org/a?offset=9&q=1", pagination, 2) == (
        "https://x.org/a?q=1&offset=50"
    )


def test_collects_every_page_until_an_empty_one(tmp_path):
    seen: list[str] = []
    pages = {1: [item(1), item(2)], 2: [item(3)], 3: []}
    project, result = run(tmp_path, api(pages, seen))
    assert result.ok and result.records == 3 and result.pages == 3, result
    assert [url.split("?")[1] for url in seen] == [
        "lang=en&page=1",
        "lang=en&page=2",
        "lang=en&page=3",
    ]
    assert len(load_records(project, "s")) == 3


def test_a_parameter_the_source_ignores_is_pagination_changed(tmp_path):
    _, result = run(tmp_path, api({1: [item(1)]}, ignore_param=True))
    assert not result.ok
    assert result.error_kind == "pagination_changed"
    assert "same document" in result.error


def test_pages_that_only_repeat_records_are_pagination_changed(tmp_path):
    pages = {1: [item(1), item(2)], 2: [item(2), item(1)]}
    _, result = run(tmp_path, api(pages))
    assert result.error_kind == "pagination_changed"
    assert "repeats records" in result.error


def test_more_pages_than_configured_is_pagination_changed(tmp_path):
    pages = {n: [item(n)] for n in range(1, 10)}
    _, result = run(tmp_path, api(pages), pagination={"param": "page", "max_pages": 3})
    assert result.error_kind == "pagination_changed"
    assert "after 3 pages" in result.error


def test_from_cache_replays_all_pages(tmp_path):
    pages = {1: [item(1)], 2: [item(2)], 3: []}
    project, first = run(tmp_path, api(pages))
    assert first.ok
    again = collect_source(project, project.sources[0], from_cache=True, today=TODAY)
    assert again.ok and again.records == 2 and again.pages == 3


def test_pagination_needs_room_in_the_request_limit(tmp_path):
    root = make_project(tmp_path / "p", fetch={"max_requests": 3, "delay_seconds": 0})
    project = load_project(root)
    assert any("max_requests" in issue.message for issue in project.issues)


def test_pagination_does_not_fit_every_delivery(tmp_path):
    root = make_project(
        tmp_path / "p",
        delivery="ical",
        reader={},
        extract={"mode": "fields", "map": {"title": "SUMMARY"}},
    )
    project = load_project(root)
    assert any("does not fit delivery" in issue.message for issue in project.issues)


@pytest.mark.parametrize("bad", [{"param": "page", "max_pages": 1}, {"max_pages": 3}])
def test_bad_pagination_is_rejected(tmp_path, bad):
    project = load_project(make_project(tmp_path / "p", pagination=bad))
    assert not project.ok
