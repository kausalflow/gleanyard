from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from gleanyard.cache import RawCache
from gleanyard.config import SourceConfig
from gleanyard.errors import Blocked, BoundsExceeded, TransportError
from gleanyard.fetch import Fetcher
from gleanyard.storage import LocalObjectStore

UA = "gleanyard-test"


def source(**overrides) -> SourceConfig:
    document = {
        "id": "demo",
        "schema": "event",
        "delivery": "json",
        "url": "https://example.org/events.json",
        "extract": {"mode": "fields", "map": {"title": "t"}},
        "fetch": {"delay_seconds": 0},
        "rights": {"allowed_actions": {"private_store": True, "normalize": True}},
    }
    for key, value in overrides.items():
        document[key] = value
    return SourceConfig.model_validate(document)


class Site:
    """A tiny fake web server that records what it was asked."""

    def __init__(self, routes, robots="User-agent: *\nAllow: /\n"):
        self.routes = routes
        self.robots = robots
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/robots.txt":
            if isinstance(self.robots, int):
                return httpx.Response(self.robots)
            return httpx.Response(200, text=self.robots)
        route = self.routes[str(request.url)]
        return route(request) if callable(route) else route

    def page_calls(self):
        return [call for call in self.calls if call.url.path != "/robots.txt"]


def fetcher(site, cache=None, src=None, sleeps=None):
    return Fetcher(
        src or source(),
        user_agent=UA,
        cache=cache,
        client=httpx.Client(transport=httpx.MockTransport(site)),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


@pytest.fixture
def cache(tmp_path):
    return RawCache(LocalObjectStore(tmp_path))


def test_fetch_stores_body_and_manifest(cache):
    site = Site({"https://example.org/events.json": httpx.Response(200, json={"a": 1})})
    run = fetcher(site, cache)
    response = run.get("https://example.org/events.json")
    manifest = run.finish()
    assert response.body == b'{"a":1}'
    assert cache.get_body(response.sha256) == response.body
    assert manifest.page_hashes() == {"https://example.org/events.json": response.sha256}
    assert cache.latest_manifest("demo").run_id == manifest.run_id
    assert site.calls[1].headers["user-agent"] == UA


def test_conditional_request_reuses_cached_body(cache):
    def page(request):
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, text="hello", headers={"etag": '"v1"'})

    site = Site({"https://example.org/events.json": page})
    first = fetcher(site, cache)
    first.get("https://example.org/events.json")
    first.finish()

    second = fetcher(site, cache)
    response = second.get("https://example.org/events.json")
    assert response.from_cache and response.not_modified
    assert response.body == b"hello"
    assert site.page_calls()[-1].headers["if-none-match"] == '"v1"'


def test_no_cache_without_private_store_permission(cache):
    site = Site({"https://example.org/events.json": httpx.Response(200, text="x")})
    run = fetcher(site, cache, source(rights={}))
    run.get("https://example.org/events.json")
    run.finish()
    assert cache.store.list_keys("raw") == []


def test_other_hosts_are_refused():
    site = Site({})
    with pytest.raises(BoundsExceeded, match="not one of the source's hosts"):
        fetcher(site).get("https://evil.example.com/")


def test_cross_host_redirect_is_refused():
    site = Site(
        {
            "https://example.org/events.json": httpx.Response(
                302, headers={"location": "https://other.org/x"}
            )
        }
    )
    with pytest.raises(BoundsExceeded):
        fetcher(site).get("https://example.org/events.json")


def test_same_host_redirect_is_followed():
    site = Site(
        {
            "https://example.org/events.json": httpx.Response(
                301, headers={"location": "/v2/events.json"}
            ),
            "https://example.org/v2/events.json": httpx.Response(200, text="ok"),
        }
    )
    response = fetcher(site).get("https://example.org/events.json")
    assert response.url == "https://example.org/v2/events.json"


def test_robots_disallow_blocks():
    site = Site({}, robots=f"User-agent: {UA}\nDisallow: /\n")
    with pytest.raises(Blocked, match="robots.txt"):
        fetcher(site).get("https://example.org/events.json")


def test_missing_robots_allows():
    site = Site({"https://example.org/events.json": httpx.Response(200, text="ok")}, robots=404)
    assert fetcher(site).get("https://example.org/events.json").body == b"ok"


@pytest.mark.parametrize(("status", "error"), [(403, Blocked), (404, TransportError)])
def test_http_errors_are_typed(status, error):
    site = Site({"https://example.org/events.json": httpx.Response(status)})
    with pytest.raises(error):
        fetcher(site).get("https://example.org/events.json")


def test_server_errors_are_retried():
    responses = iter([httpx.Response(503), httpx.Response(200, text="ok")])
    site = Site({"https://example.org/events.json": lambda _: next(responses)})
    sleeps = []
    assert fetcher(site, sleeps=sleeps).get("https://example.org/events.json").body == b"ok"
    assert 2 in sleeps


@pytest.mark.parametrize(("headers", "wait"), [({"retry-after": "7"}, 7), ({}, 30)])
def test_too_many_requests_waits_for_retry_after(headers, wait):
    responses = iter([httpx.Response(429, headers=headers), httpx.Response(200, text="ok")])
    site = Site({"https://example.org/events.json": lambda _: next(responses)})
    sleeps = []
    assert fetcher(site, sleeps=sleeps).get("https://example.org/events.json").body == b"ok"
    assert wait in sleeps


def test_retry_after_accepts_an_http_date():
    later = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    responses = iter([httpx.Response(429, headers={"retry-after": later}), httpx.Response(200)])
    site = Site({"https://example.org/events.json": lambda _: next(responses)})
    sleeps = []
    fetcher(site, sleeps=sleeps).get("https://example.org/events.json")
    assert any(10 < s <= 20 for s in sleeps)


@pytest.mark.parametrize("retry_after", ["3600", "soon"])
def test_long_or_unreadable_retry_after_is_blocked(retry_after):
    site = Site(
        {
            "https://example.org/events.json": httpx.Response(
                429, headers={"retry-after": retry_after}
            )
        }
    )
    with pytest.raises(Blocked, match="HTTP 429"):
        fetcher(site).get("https://example.org/events.json")
    assert len(site.page_calls()) == 1


def test_persistent_too_many_requests_is_blocked():
    site = Site(
        {"https://example.org/events.json": httpx.Response(429, headers={"retry-after": "1"})}
    )
    with pytest.raises(Blocked, match="HTTP 429"):
        fetcher(site).get("https://example.org/events.json")
    assert len(site.page_calls()) == 3


def test_response_size_limit():
    big = source(fetch={"delay_seconds": 0, "max_response_bytes": 1024})
    site = Site({"https://example.org/events.json": httpx.Response(200, content=b"x" * 2000)})
    with pytest.raises(BoundsExceeded, match="exceeds 1024 bytes"):
        fetcher(site, src=big).get("https://example.org/events.json")


def test_request_limit_counts_robots():
    small = source(fetch={"delay_seconds": 0, "max_requests": 2})
    site = Site(
        {
            "https://example.org/events.json": httpx.Response(200, text="a"),
            "https://example.org/b": httpx.Response(200, text="b"),
        }
    )
    run = fetcher(site, src=small)
    run.get("https://example.org/events.json")
    with pytest.raises(BoundsExceeded, match="limit of 2 requests"):
        run.get("https://example.org/b")


def test_delay_between_requests():
    slow = source(fetch={"delay_seconds": 5})
    site = Site({"https://example.org/events.json": httpx.Response(200, text="a")})
    sleeps = []
    run = fetcher(site, src=slow, sleeps=sleeps)
    run.get("https://example.org/events.json")
    assert sleeps and 4 < sleeps[0] <= 5


def test_text_uses_declared_charset():
    body = "Köln".encode("latin-1")
    site = Site(
        {
            "https://example.org/events.json": httpx.Response(
                200, content=body, headers={"content-type": "text/html; charset=ISO-8859-1"}
            )
        }
    )
    assert fetcher(site).get("https://example.org/events.json").text == "Köln"


def test_retention_keeps_last_good_and_collects_garbage(cache):
    now = datetime.now(UTC)
    site = Site({"https://example.org/events.json": lambda _: httpx.Response(200, text=str(now))})
    runs = []
    for days_ago, text in [(60, "old"), (45, "good"), (1, "new")]:
        site.routes["https://example.org/events.json"] = httpx.Response(200, text=text)
        run = fetcher(site, cache)
        run.manifest.started_at = now - timedelta(days=days_ago)
        run.manifest.run_id = text
        run.get("https://example.org/events.json")
        runs.append(run.finish())
    cache.mark_good(runs[1])

    expired = cache.retention_plan("demo", raw_days=30, now=now)
    assert expired == ["raw/runs/demo/old.json"]
    deleted = cache.apply_retention(expired)
    assert len(deleted) == 1
    assert cache.get_body(runs[1].responses[0].sha256) == b"good"
    assert cache.get_body(runs[2].responses[0].sha256) == b"new"
