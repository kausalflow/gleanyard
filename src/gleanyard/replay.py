"""``gleanyard test``: replay each source's saved fixture page offline.

A project keeps one sample page per source at ``tests/fixtures/<id>.<ext>``. Replaying it
runs the source's pinned extraction on that page, with no network and no model, and compares
the records with a snapshot at ``tests/fixtures/<id>.expected.json``. The snapshot also pins
the date the page was judged on, so a ``min: today`` bound does not reject an old fixture.

A mapping edit, a schema change or a new gleanyard version that changes what a source
extracts shows up here, in the pull request, before a scheduled run meets it. ``--update``
writes the snapshot, and only when the replay passes the same thresholds as ``try``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from gleanyard.collect import Extraction, extract
from gleanyard.config import Delivery, LlmExtract, SourceConfig
from gleanyard.errors import SourceError
from gleanyard.fetch import Response
from gleanyard.project import Project
from gleanyard.run import apply_filter, selector_schema
from gleanyard.tryout import TryReport, judge

FIXTURES = Path("tests/fixtures")
EXPECTED_SUFFIX = ".expected.json"
MAX_SHOWN = 5  # differences listed per source; the rest are counted
CONTENT_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".xml": "application/xml",
    ".rss": "application/rss+xml",
    ".atom": "application/atom+xml",
    ".ics": "text/calendar",
    ".csv": "text/csv",
    ".pdf": "application/pdf",
}

Verdict = Literal["pass", "fail", "skip"]


class Snapshot(BaseModel):
    today: date
    records: dict[str, dict[str, Any]]


class ReplayResult(BaseModel):
    source_id: str
    verdict: Verdict = "fail"
    fixture: str | None = None
    reasons: list[str] = Field(default_factory=list)
    records: int = 0
    rejections: int = 0
    updated: bool = False


class ReplayReport(BaseModel):
    results: list[ReplayResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(result.verdict != "fail" for result in self.results)

    def markdown(self) -> str:
        lines = [
            "## Fixture replay",
            "",
            "| Source | Verdict | Records | Notes |",
            "| --- | --- | --- | --- |",
        ]
        for result in self.results:
            notes = "; ".join(result.reasons).replace("|", "\\|")
            lines.append(
                f"| `{result.source_id}` | {result.verdict.upper()} | {result.records} | {notes} |"
            )
        return "\n".join(lines) + "\n"


def fixture_path(project: Project, source: SourceConfig) -> Path | None:
    """The source's fixture page: ``tests/fixtures/<id>.<ext>``, any single extension."""
    directory = project.root / FIXTURES
    if not directory.is_dir():
        return None
    matches = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.stem == source.id and path.suffix
    )
    return matches[0] if matches else None


def snapshot_path(project: Project, source: SourceConfig) -> Path:
    return project.root / FIXTURES / f"{source.id}{EXPECTED_SUFFIX}"


def replay_source(
    project: Project, source: SourceConfig, *, update: bool = False, today: date | None = None
) -> ReplayResult:
    """Extract the fixture and compare it with the snapshot, or write the snapshot."""
    assert project.schema is not None
    result = ReplayResult(source_id=source.id)
    fixture = fixture_path(project, source)
    if fixture is None:
        result.verdict = "skip"
        result.reasons.append(f"no fixture at {FIXTURES}/{source.id}.<ext>")
        return result
    result.fixture = str(fixture.relative_to(project.root))
    if isinstance(source.extract, LlmExtract):
        result.verdict = "skip"
        result.reasons.append("llm mode needs a model; replay covers the other modes")
        return result
    path = snapshot_path(project, source)
    snapshot = None if update or not path.is_file() else _load_snapshot(path)
    if snapshot is None and not update:
        result.verdict = "skip"
        result.reasons.append(f"no snapshot: run `gleanyard test -s {source.id} --update`")
        return result
    day = snapshot.today if snapshot else today or date.today()
    try:
        extraction = _extract_fixture(project, source, fixture, day)
    except SourceError as error:
        result.reasons.append(f"{error.kind.value}: {error}")
        return result
    result.records, result.rejections = len(extraction.records), len(extraction.rejections)
    judged = TryReport(source_id=source.id, mode=source.extract.mode)
    judge(judged, source, extraction.records, extraction.rejections)
    records = {record.id: record.model_dump(mode="json")["values"] for record in extraction.records}
    if snapshot is None:
        if not judged.passed:
            result.reasons += judged.reasons + ["snapshot not written"]
            return result
        path.write_text(
            json.dumps(
                {"today": day.isoformat(), "records": records},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        result.verdict, result.updated = "pass", True
        result.reasons.append(f"wrote {path.relative_to(project.root)} ({len(records)} records)")
        return result
    differences = compare(snapshot.records, records, project.schema.identity)
    if differences:
        result.reasons += differences
        return result
    result.verdict = "pass"
    result.reasons.append(f"{len(records)} records match the snapshot of {snapshot.today}")
    return result


def replay_project(
    project: Project,
    sources: list[SourceConfig] | None = None,
    *,
    update: bool = False,
    today: date | None = None,
) -> ReplayReport:
    selected = project.sources if sources is None else sources
    return ReplayReport(
        results=[replay_source(project, item, update=update, today=today) for item in selected]
    )


def compare(
    expected: dict[str, dict[str, Any]],
    actual: dict[str, dict[str, Any]],
    identity: list[str] | None = None,
) -> list[str]:
    """What changed between the snapshot and this replay, worst first, with counts.

    Record ids are hashes, so records are named by their identity fields instead.
    """

    def name(values: dict[str, Any]) -> str:
        parts = [str(values[field]) for field in identity or [] if field in values]
        return _short(" / ".join(parts)) if parts else "a record"

    missing = [name(values) for key, values in expected.items() if key not in actual]
    new = [name(values) for key, values in actual.items() if key not in expected]
    changed: list[str] = []
    for key, before in expected.items():
        after = actual.get(key)
        if after is None or before == after:
            continue
        for field in sorted(set(before) | set(after)):
            if before.get(field) != after.get(field):
                changed.append(
                    f"{name(before)}: {field} was {_short(before.get(field))}, "
                    f"now {_short(after.get(field))}"
                )
    problems = []
    for label, items in (("missing", missing), ("changed", changed), ("new", new)):
        if not items:
            continue
        shown = ", ".join(items[:MAX_SHOWN])
        more = f" and {len(items) - MAX_SHOWN} more" if len(items) > MAX_SHOWN else ""
        noun = "field(s)" if label == "changed" else "record(s)"
        problems.append(f"{len(items)} {noun} {label}: {shown}{more}")
    return problems


def _short(value: Any) -> str:
    if value is None:
        return "absent"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


def _load_snapshot(path: Path) -> Snapshot:
    return Snapshot.model_validate_json(path.read_text(encoding="utf-8"))


def _extract_fixture(
    project: Project, source: SourceConfig, fixture: Path, today: date
) -> Extraction:
    assert project.schema is not None
    body = fixture.read_bytes()
    # The fixture stands for one page of the source: for a sitemap that is a page it lists,
    # not the index, so it is extracted directly rather than through the fetch plan.
    response = Response(
        url=str(source.url),
        status_code=200,
        content_type=CONTENT_TYPES.get(fixture.suffix.lower()),
        sha256=hashlib.sha256(body).hexdigest(),
        size=len(body),
        retrieved_at=datetime.combine(today, datetime.min.time(), tzinfo=UTC),
        body=body,
        from_cache=True,
    )
    extraction = extract(
        source,
        project.schema,
        response,
        today=today,
        selectors=selector_schema(project, source),
        base_url=response.url if source.delivery == Delivery.SITEMAP else None,
    )
    apply_filter(source, extraction)
    return extraction
