"""``gleanyard terms``: notice when a source's terms or robots.txt change.

A source's rights rest on the pages its owner read at setup: the evidence file
``evidence/<id>/<date>-terms.yaml`` lists them under ``pages`` (each with a ``url``). This
command fetches those pages and the source host's robots.txt, and keeps a fingerprint of each
in ``status.json``. The fingerprint is a hash of the page's visible text, so a new session
cookie or a rotated script does not count as a change; the wording does.

A changed page stays flagged until someone reads it again and adds a newer evidence file: that
review becomes the new baseline. Terms are the owner's decision, so a change never makes a
source ``degraded`` and never starts a repair; the hub opens a ``terms_changed`` issue instead.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from lxml import html as lxml_html
from pydantic import BaseModel, Field

from gleanyard.config import SourceConfig
from gleanyard.errors import SourceError, TransportError
from gleanyard.fetch import Fetcher
from gleanyard.project import Project

EVIDENCE = Path("evidence")
ABSENT = "absent"  # the fingerprint of a page that answers 404 or 410, such as no robots.txt
DEFAULT_INTERVAL_DAYS = 7
NOISE = ("script", "style", "noscript", "template", "svg", "nav", "header", "footer", "form")
DATED_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class TermsState(BaseModel):
    reviewed_on: date | None = Field(
        default=None, description="Date of the evidence file the fingerprints belong to."
    )
    checked_at: datetime | None = None
    pages: dict[str, str] = Field(
        default_factory=dict, description="Fingerprint per page, as of the review."
    )
    changed: list[str] = Field(
        default_factory=list, description="Pages whose text differs from the review."
    )
    changed_at: datetime | None = None
    errors: dict[str, str] = Field(
        default_factory=dict, description="Pages that could not be read on the last check."
    )


class Evidence(BaseModel):
    path: str
    reviewed_on: date
    urls: list[str]


class TermsResult(BaseModel):
    source_id: str
    checked: bool = False
    note: str | None = None
    state: TermsState | None = None


def evidence_id(source: SourceConfig) -> str:
    """The evidence folder of a source: its own id unless it shares a review."""
    return source.evidence or source.id


def latest_evidence(project: Project, source: SourceConfig) -> Evidence | None:
    """The newest ``evidence/<folder>/*-terms.yaml`` with its date and page URLs."""
    directory = project.root / EVIDENCE / evidence_id(source)
    if not directory.is_dir():
        return None
    files = sorted(directory.glob("*terms*.yaml"))
    if not files:
        return None
    path = files[-1]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        data = {}
    reviewed = None
    if isinstance(data, dict):
        reviewed = data.get("read_at", data.get("checked_at"))
    if not isinstance(reviewed, date):
        match = DATED_NAME.match(path.name)
        reviewed = date.fromisoformat(match.group(1)) if match else None
    if reviewed is None:
        return None
    pages = data.get("pages") if isinstance(data, dict) else None
    urls = [
        page["url"]
        for page in pages or []
        if isinstance(page, dict) and isinstance(page.get("url"), str)
    ]
    origin = urlsplit(str(source.url))
    robots = f"{origin.scheme}://{origin.netloc}/robots.txt"
    if robots not in urls:
        urls.append(robots)
    return Evidence(
        path=str(path.relative_to(project.root)),
        reviewed_on=reviewed,
        urls=list(dict.fromkeys(urls)),
    )


def fingerprint(body: bytes, content_type: str | None) -> str:
    """A hash of the text a reader sees: markup, scripts and spacing do not count."""
    if "html" in (content_type or "").lower() or body.lstrip()[:1] == b"<":
        try:
            tree = lxml_html.fromstring(body)
        except (ValueError, lxml_html.etree.ParserError):
            text = body.decode("utf-8", errors="replace")
        else:
            for element in tree.xpath("|".join(f"//{tag}" for tag in NOISE)):
                element.drop_tree()
            main = tree.xpath("//main") or tree.xpath("//body") or [tree]
            text = " ".join(main[0].itertext())
    else:
        text = body.decode("utf-8", errors="replace")
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def check_terms(
    project: Project,
    sources: list[SourceConfig],
    previous: dict[str, TermsState | None],
    *,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    interval_days: int = DEFAULT_INTERVAL_DAYS,
    force: bool = False,
) -> list[TermsResult]:
    """Fetch each source's terms pages once, at most every ``interval_days``."""
    now = now or datetime.now(UTC)
    seen: dict[str, tuple[str | None, str | None]] = {}  # url -> (fingerprint, error)
    results = []
    for source in sources:
        result = TermsResult(source_id=source.id)
        results.append(result)
        old = previous.get(source.id)
        evidence = latest_evidence(project, source)
        if evidence is None:
            folder = evidence_id(source)
            result.state, result.note = old, f"no evidence/{folder}/<date>-terms.yaml to watch"
            continue
        due = force or old is None or old.checked_at is None
        due = due or now - old.checked_at >= timedelta(days=interval_days)
        due = due or old.reviewed_on != evidence.reviewed_on
        if not due:
            result.state, result.note = old, "checked recently"
            continue
        current = _read_pages(project, source, evidence.urls, seen, client)
        result.checked = True
        result.state = _compare(old, evidence, current, now)
        if result.state.changed:
            result.note = f"changed since the review of {evidence.reviewed_on}"
    return results


def _read_pages(
    project: Project,
    source: SourceConfig,
    urls: list[str],
    seen: dict[str, tuple[str | None, str | None]],
    client: httpx.Client | None,
) -> dict[str, tuple[str | None, str | None]]:
    """Fingerprint or error per URL; a URL another source already read is not fetched again."""
    todo = [url for url in urls if url not in seen]
    if todo:
        hosts = frozenset(urlsplit(url).hostname or "" for url in todo) - {""}
        # The source's delay and size limits apply; its request budget is for collecting,
        # so the check gets room for each page and each host's robots.txt.
        budget = max(source.fetch.max_requests, 2 * len(todo) + len(hosts))
        limits = source.fetch.model_copy(update={"max_requests": min(budget, 1000)})
        fetcher = Fetcher(
            source.model_copy(update={"fetch": limits}),
            user_agent=project.config.user_agent,
            client=client,
            extra_hosts=hosts,
        )
        for url in todo:
            try:
                response = fetcher.get(url)
            except TransportError as error:
                if error.status_code in {404, 410}:
                    seen[url] = (ABSENT, None)
                else:
                    seen[url] = (None, str(error))
            except SourceError as error:
                seen[url] = (None, str(error))
            else:
                seen[url] = (fingerprint(response.body, response.content_type), None)
    return {url: seen[url] for url in urls}


def _compare(
    old: TermsState | None,
    evidence: Evidence,
    current: dict[str, tuple[str | None, str | None]],
    now: datetime,
) -> TermsState:
    errors = {url: error for url, (_, error) in current.items() if error}
    readings = {url: value for url, (value, _) in current.items() if value is not None}
    if old is None or old.reviewed_on != evidence.reviewed_on:
        # A new review: what the pages say today is what the owner read.
        return TermsState(
            reviewed_on=evidence.reviewed_on, checked_at=now, pages=readings, errors=errors
        )
    pages = dict(old.pages)
    changed = list(old.changed)
    for url, value in readings.items():
        if url not in pages:
            pages[url] = value
        elif pages[url] != value and url not in changed:
            changed.append(url)
    return TermsState(
        reviewed_on=old.reviewed_on,
        checked_at=now,
        pages=pages,
        changed=changed,
        changed_at=(old.changed_at or now) if changed else None,
        errors=errors,
    )
