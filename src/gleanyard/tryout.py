"""``gleanyard try SOURCE``: a live dry run that judges whether a source works.

Nothing is stored (no raw cache, records, review file or status): the run fetches the
source, extracts with its pinned mode and reports sample records, how often each field is
filled, why records were rejected, and a pass or fail against fixed thresholds. The verdict
is deterministic, so setup and repair end when ``try`` passes, not when a model is satisfied.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from gleanyard.config import LlmExtract, SourceConfig
from gleanyard.errors import SourceError
from gleanyard.extract.llm import LlmSession, ModelClient, PydanticAiClient
from gleanyard.fetch import Fetcher, Response
from gleanyard.project import Project
from gleanyard.run import extract_pages, fetch_pages, selector_schema
from gleanyard.storage import LocalObjectStore

MAX_REJECTED_SHARE = 0.2  # more than this share of candidates rejected fails the source
MAX_WARNED_SHARE = 0.5  # more than this share of records with a dropped field fails it
SAMPLES = 5


class TryReport(BaseModel):
    source_id: str
    verdict: str = "fail"
    reasons: list[str] = Field(default_factory=list)
    mode: str
    records: int = 0
    rejections: int = 0
    pages: int = 0
    samples: list[dict[str, Any]] = Field(default_factory=list)
    field_fill_rates: dict[str, float] = Field(default_factory=dict)
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    warning_reasons: dict[str, int] = Field(default_factory=dict)
    model_calls: int = 0
    cost_eur: float = 0.0
    error_kind: str | None = None
    error: str | None = None
    saved_pages: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

    def markdown(self) -> str:
        """A PR description: verdict, fill rates and sample records."""
        lines = [
            f"## `try {self.source_id}`: **{self.verdict.upper()}** (mode `{self.mode}`)",
            "",
            f"{self.records} records, {self.rejections} rejected, {self.pages} page(s).",
        ]
        lines += [f"- {reason}" for reason in self.reasons]
        if self.error:
            lines += ["", f"Error `{self.error_kind}`: {self.error}"]
        if self.field_fill_rates:
            lines += ["", "| Field | Filled |", "| --- | --- |"]
            lines += [f"| {k} | {v:.0%} |" for k, v in self.field_fill_rates.items()]
        if self.rejection_reasons:
            lines += ["", "Rejected because:"]
            lines += [f"- {key}: {count}" for key, count in self.rejection_reasons.items()]
        if self.samples:
            columns = list(self.samples[0])
            lines += ["", "| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]
            for sample in self.samples:
                cells = (str(sample.get(column, "")).replace("|", "\\|") for column in columns)
                lines.append("| " + " | ".join(cells) + " |")
        if self.model_calls:
            lines += ["", f"Model calls: {self.model_calls}, about {self.cost_eur:.4f} EUR."]
        return "\n".join(lines) + "\n"


def try_source(
    project: Project,
    source: SourceConfig,
    *,
    client: httpx.Client | None = None,
    today: date | None = None,
    llm_client: ModelClient | None = None,
    save_pages: Path | None = None,
) -> TryReport:
    """Fetch and extract ``source`` without storing anything.

    ``save_pages`` names a scratch directory for the fetched pages, so whoever repairs a
    mapping can read the page the source returns today.
    """
    assert project.schema is not None
    report = TryReport(source_id=source.id, mode=source.extract.mode)
    session = None
    if isinstance(source.extract, LlmExtract):
        if not source.rights.allowed_actions.send_to_model:
            report.error_kind, report.error = "rights", "llm mode needs send_to_model"
            report.reasons.append("rights do not allow send_to_model")
            return report
        # Model output is cached under a scratch store only for this run's lifetime.
        store = LocalObjectStore(project.root / project.config.storage.root)
        session = LlmSession(store=store, client=llm_client, make_client=PydanticAiClient)
    try:
        selectors = selector_schema(project, source)
        fetcher = Fetcher(source, user_agent=project.config.user_agent, cache=None, client=client)
        pages = fetch_pages(fetcher, source)
        if save_pages is not None:
            report.saved_pages = write_pages(pages, save_pages)
        result = extract_pages(project, source, pages, today, selectors, session)
    except SourceError as error:
        report.error_kind, report.error = error.kind.value, str(error)
        report.reasons.append(f"{error.kind.value}: {error}")
        return report
    report.records, report.rejections = len(result.records), len(result.rejections)
    report.pages = len(pages)
    report.field_fill_rates = result.field_fill_rates
    report.samples = [record.values for record in result.records[:SAMPLES]]
    report.rejection_reasons = dict(
        Counter(
            f"{issue.field}: {issue.reason}" for item in result.rejections for issue in item.errors
        )
    )
    report.warning_reasons = dict(
        Counter(
            f"{issue.field}: {issue.reason}" for item in result.records for issue in item.warnings
        )
    )
    if session:
        report.model_calls, report.cost_eur = session.calls, round(session.cost_eur, 6)
    _judge(report, source, result.records, result.rejections)
    return report


EXTENSIONS = {"html": ".html", "json": ".json", "xml": ".xml", "calendar": ".ics", "csv": ".csv"}


def write_pages(pages: list[Response], directory: Path) -> list[str]:
    """Write each fetched page as ``<n>.<ext>`` and return the paths."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for index, page in enumerate(pages, start=1):
        kind = (page.content_type or "").lower()
        ext = next((e for key, e in EXTENSIONS.items() if key in kind), ".txt")
        path = directory / f"{index:02d}{ext}"
        path.write_bytes(page.body)
        written.append(str(path))
    return written


def _judge(report: TryReport, source: SourceConfig, records: list, rejections: list) -> None:
    candidates = len(records) + len(rejections)
    if not records:
        if source.expect_empty == "seasonal":
            report.reasons.append("no records, as expected for a seasonal source")
            report.verdict = "pass"
        else:
            report.reasons.append("no records")
        return
    if len(rejections) / candidates > MAX_REJECTED_SHARE:
        report.reasons.append(
            f"{len(rejections)} of {candidates} candidates rejected "
            f"(limit {MAX_REJECTED_SHARE:.0%})"
        )
    warned = sum(1 for record in records if record.warnings)
    if warned / len(records) > MAX_WARNED_SHARE:
        report.reasons.append(f"{warned} of {len(records)} records lost a field to a warning")
    if not report.reasons:
        report.verdict = "pass"
        report.reasons.append(f"{len(records)} records pass the schema")
