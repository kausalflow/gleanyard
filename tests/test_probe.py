import httpx
import pytest

from gleanyard.probe import probe

UA = "test-agent"


def serve(pages: dict[str, tuple[str, bytes]], robots: str | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if robots is None:
                return httpx.Response(404)
            return httpx.Response(200, text=robots)
        if request.url.path in pages:
            content_type, body = pages[request.url.path]
            return httpx.Response(200, content=body, headers={"content-type": content_type})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


PAGE_WITH_FEEDS = b"""<html><head>
<link rel="alternate" type="text/calendar" href="/termine.ics" title="Termine">
<link rel="alternate" type="application/rss+xml" href="https://other.example/feed.rss">
</head><body>
<a href="/export/all.json">JSON</a><a href="/impressum">Impressum</a>
<a href="https://other.example/datenschutz">Privacy</a>
</body></html>"""

PAGE_WITH_JSONLD = b"""<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"},
{"@type":"Event","name":"Synthetic concert","startDate":"2026-10-20"}]}
</script></head><body></body></html>"""


def test_feeds_are_ranked_first_and_other_hosts_ignored():
    client = serve({"/events": ("text/html", PAGE_WITH_FEEDS)})
    report = probe("https://hof.example/events", user_agent=UA, client=client)
    assert [feed.url for feed in report.feeds] == [
        "https://hof.example/termine.ics",
        "https://hof.example/export/all.json",
    ]
    assert report.legal_pages == ["https://hof.example/impressum"]
    first = report.options[0]
    assert (first.rank, first.delivery, first.mode) == (1, "ical", "fields")
    assert report.options[-1].mode == "llm"


def test_jsonld_types_are_listed_and_ranked_above_selectors():
    client = serve({"/events": ("text/html", PAGE_WITH_JSONLD)})
    report = probe("https://hof.example/events", user_agent=UA, client=client)
    assert "Event" in report.jsonld_types
    top = report.options[0]
    assert (top.mode, top.types) == ("jsonld", ["Event"])
    assert [o.mode for o in report.options] == ["jsonld", "llm"]


def test_plain_page_suggests_selectors_then_llm():
    client = serve({"/events": ("text/html", b"<html><body><table></table></body></html>")})
    report = probe("https://hof.example/events", user_agent=UA, client=client)
    assert [o.mode for o in report.options] == ["selectors", "llm"]


@pytest.mark.parametrize(
    ("content_type", "body", "delivery"),
    [
        ("text/calendar", b"BEGIN:VCALENDAR\nEND:VCALENDAR", "ical"),
        ("application/xml", b"<?xml version='1.0'?><rss version='2.0'><channel/></rss>", "rss"),
        ("application/octet-stream", b'{"events": []}', "json"),
        ("application/pdf", b"%PDF-1.4 ...", "pdf"),
    ],
)
def test_the_url_itself_can_be_a_feed_or_document(content_type, body, delivery):
    client = serve({"/data": (content_type, body)})
    report = probe("https://hof.example/data", user_agent=UA, client=client)
    option = report.options[0]
    assert option.delivery == delivery
    assert option.mode == ("llm" if delivery == "pdf" else "fields")


def test_sitemap_url_is_reported_not_ranked():
    sitemap = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
    client = serve({"/sitemap.xml": ("application/xml", sitemap)})
    report = probe("https://hof.example/sitemap.xml", user_agent=UA, client=client)
    assert report.sitemaps == ["https://hof.example/sitemap.xml"] and report.options == []


def test_robots_disallow_stops_before_fetching_the_page():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /events\n")
        return httpx.Response(200, text="should not be fetched")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    report = probe("https://hof.example/events", user_agent=UA, client=client)
    assert not report.robots_allows and report.options == []
    assert calls == ["/robots.txt"]


def test_robots_sitemaps_are_reported():
    client = serve(
        {"/events": ("text/html", b"<html></html>")},
        robots="Sitemap: https://hof.example/sitemap.xml\n",
    )
    assert probe("https://hof.example/events", user_agent=UA, client=client).sitemaps == [
        "https://hof.example/sitemap.xml"
    ]


def test_rejects_urls_with_credentials():
    with pytest.raises(ValueError):
        probe("https://user:pw@hof.example/", user_agent=UA)
