"""``gleanyard probe URL``: find the best way to collect from a site, without a model.

A probe makes at most three same-host requests (robots.txt, the page, and nothing else),
looks for feeds, schema.org markup, sitemaps and legal pages, and ranks the ways in which
the data could be collected. Feeds come first because they need no extraction model, then
JSON-LD, then the page itself.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from lxml import etree
from pydantic import BaseModel, Field

from gleanyard.errors import SourceError
from gleanyard.extract.selectors import parse_html

FEED_TYPES = {
    "text/calendar": "ical",
    "application/rss+xml": "rss",
    "application/atom+xml": "rss",
    "application/json": "json",
    "application/feed+json": "json",
}
FEED_SUFFIXES = {".ics": "ical", ".rss": "rss", ".atom": "rss", ".json": "json"}
LEGAL = re.compile(
    r"impressum|imprint|legal[- ]notice|terms|agb|nutzungsbedingungen|datenschutz", re.I
)
MAX_BYTES = 2 * 1024 * 1024

Delivery = Literal["ical", "rss", "json", "csv", "pdf", "html", "sitemap"]


class Option(BaseModel):
    rank: int
    delivery: Delivery
    mode: Literal["fields", "jsonld", "selectors", "llm"]
    url: str
    reason: str
    types: list[str] = Field(default_factory=list, description="JSON-LD types, for jsonld.")


class Feed(BaseModel):
    url: str
    delivery: Delivery
    title: str | None = None


class ProbeReport(BaseModel):
    url: str
    host: str
    status: int
    content_type: str | None
    document: Delivery | None = None
    robots_allows: bool
    sitemaps: list[str] = Field(default_factory=list)
    jsonld_types: list[str] = Field(default_factory=list)
    feeds: list[Feed] = Field(default_factory=list)
    legal_pages: list[str] = Field(default_factory=list)
    options: list[Option] = Field(default_factory=list)


def probe(url: str, *, user_agent: str, client: httpx.Client | None = None) -> ProbeReport:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError(f"not a credential-free http(s) URL: {url}")
    owns_client = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=True)
    headers = {"User-Agent": user_agent}
    try:
        origin = f"{parts.scheme}://{parts.netloc}"
        robots_text = _get(client, f"{origin}/robots.txt", headers)
        robots = RobotFileParser()
        robots.parse(robots_text.text.splitlines() if robots_text.status_code == 200 else [])
        allows = robots_text.status_code not in {401, 403} and robots.can_fetch(user_agent, url)
        sitemaps = list(robots.site_maps() or []) if robots_text.status_code == 200 else []
        if not allows:
            return ProbeReport(
                url=url,
                host=parts.hostname,
                status=0,
                content_type=None,
                robots_allows=False,
                sitemaps=sitemaps,
            )
        response = _get(client, url, headers)
    except httpx.HTTPError as error:
        raise SourceError("probe", f"request failed: {error}") from error
    finally:
        if owns_client:
            client.close()
    report = ProbeReport(
        url=url,
        host=parts.hostname,
        status=response.status_code,
        content_type=response.headers.get("content-type"),
        robots_allows=True,
        sitemaps=sitemaps,
    )
    if response.status_code >= 400:
        return report
    _analyse(report, str(response.url), response.content)
    report.options = _rank(report)
    return report


def _get(client: httpx.Client, url: str, headers: dict[str, str]) -> httpx.Response:
    with client.stream("GET", url, headers=headers) as response:
        body = b""
        for chunk in response.iter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                break
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=body[:MAX_BYTES],
            request=response.request,
        )


def _kind_of(content_type: str | None, body: bytes) -> Delivery:
    """What the document itself is."""
    mime = (content_type or "").split(";")[0].strip().lower()
    head = body[:2000].lstrip()
    if mime == "text/calendar" or head.startswith(b"BEGIN:VCALENDAR"):
        return "ical"
    if head.startswith(b"%PDF"):
        return "pdf"
    if mime in {"application/json", "application/feed+json"} or _is_json(head):
        return "json"
    if mime in {"text/csv"}:
        return "csv"
    if mime.endswith("xml") or head.startswith(b"<?xml") or head.startswith(b"<rss"):
        root = _xml_root(body)
        if root in {"rss", "feed", "rdf"}:
            return "rss"
        if root in {"urlset", "sitemapindex"}:
            return "sitemap"
    return "html"


def _is_json(head: bytes) -> bool:
    return head[:1] in {b"{", b"["} and not head.startswith(b"<")


def _xml_root(body: bytes) -> str:
    try:
        return (
            etree.fromstring(body, parser=etree.XMLParser(resolve_entities=False))
            .tag.rsplit("}", 1)[-1]
            .lower()
        )
    except etree.XMLSyntaxError:
        return ""


def _analyse(report: ProbeReport, final_url: str, body: bytes) -> None:
    kind = _kind_of(report.content_type, body)
    report.document = kind
    if kind == "sitemap":
        report.sitemaps.append(final_url)
        return
    if kind != "html":
        report.feeds.append(Feed(url=final_url, delivery=kind))
        return
    root = parse_html("probe", body)
    seen: set[str] = set()
    for link in root.xpath("//link[@rel='alternate'][@type][@href]"):
        delivery = FEED_TYPES.get((link.get("type") or "").split(";")[0].strip().lower())
        _add_feed(report, seen, delivery, urljoin(final_url, link.get("href")), link.get("title"))
    for link in root.xpath("//a[@href]"):
        href = urljoin(final_url, link.get("href"))
        path = urlsplit(href).path.lower()
        suffix = next((s for s in FEED_SUFFIXES if path.endswith(s)), None)
        if suffix:
            _add_feed(report, seen, FEED_SUFFIXES[suffix], href, link.text_content().strip())
        text = f"{path} {link.text_content()}"
        same_host = urlsplit(href).hostname == report.host
        if LEGAL.search(text) and same_host and href not in report.legal_pages:
            report.legal_pages.append(href)
    report.jsonld_types = _all_types(root)


def _all_types(root) -> list[str]:
    """Every @type named in any JSON-LD block, so the probe can list what is there."""
    found: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, dict):
            kinds = value.get("@type", [])
            found.update([kinds] if isinstance(kinds, str) else [str(k) for k in kinds])
            for child in value.values():
                walk(child)

    for script in root.xpath('//script[@type="application/ld+json"]'):
        try:
            walk(json.loads(script.text_content()))
        except ValueError:
            continue
    return sorted(found)


def _add_feed(report, seen, delivery, url, title) -> None:
    if delivery and url not in seen and urlsplit(url).hostname == report.host:
        seen.add(url)
        report.feeds.append(Feed(url=url, delivery=delivery, title=(title or None)))


def _rank(report: ProbeReport) -> list[Option]:
    options: list[Option] = []
    for feed in report.feeds:
        itself = feed.url.rstrip("/") == report.url.rstrip("/")
        where = "the URL itself is" if itself else "the page links"
        if feed.delivery == "pdf":
            options.append(
                Option(
                    rank=0,
                    delivery="pdf",
                    mode="llm",
                    url=feed.url,
                    reason=f"{where} a PDF; its text layer goes to the model",
                )
            )
            continue
        options.append(
            Option(
                rank=0,
                delivery=feed.delivery,
                mode="fields",
                url=feed.url,
                reason=f"{where} a {feed.delivery} feed; fields map directly, no model",
            )
        )
    entity_types = [kind for kind in report.jsonld_types if kind not in GENERIC_TYPES]
    page_is_document = report.document != "html"
    if entity_types:
        options.append(
            Option(
                rank=0,
                delivery="html",
                mode="jsonld",
                url=report.url,
                types=entity_types,
                reason=f"the page publishes schema.org data ({', '.join(entity_types)})",
            )
        )
    if not page_is_document and not entity_types:
        options.append(
            Option(
                rank=0,
                delivery="html",
                mode="selectors",
                url=report.url,
                reason="no feed or markup found; draft CSS selectors with crawl4ai",
            )
        )
    if not page_is_document:
        options.append(
            Option(
                rank=0,
                delivery="html",
                mode="llm",
                url=report.url,
                reason="fallback if no selector mapping holds; costs money on every change",
            )
        )
    for rank, option in enumerate(options, 1):
        option.rank = rank
    return options


GENERIC_TYPES = {
    "Organization",
    "WebSite",
    "WebPage",
    "BreadcrumbList",
    "ListItem",
    "ImageObject",
    "Place",
    "PostalAddress",
    "Person",
    "SearchAction",
    "Offer",
    "GeoCoordinates",
}
