"""``status.json``: one health report per project, written after every ``collect``.

The status hub reads this file from each project repo, so it carries everything needed to
alert on a change without opening the data: per-source health, record counts against the
last good run, and when each source last succeeded. State between runs (last success,
consecutive failures, the baseline of the last good run) is read back from the previous report.

A run that works can still be broken. Each check below compares the run with the source's
baseline and adds a warning, which makes the source ``degraded``:

- ``record_drop``: over 25% fewer records than the last good run;
- ``fill_rate_drop``: a field that most records carried now fills half as often or less;
- ``rejection_spike``: a much larger share of records is rejected than before;
- ``unchanged``: the records have not changed for ``expect_change_days`` (opt-in per source).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from gleanyard.config import SourceConfig
from gleanyard.project import Project
from gleanyard.run import SourceRun
from gleanyard.terms import TermsState

Health = Literal["ok", "degraded", "failing", "disabled"]
CheckKind = Literal["record_drop", "fill_rate_drop", "rejection_spike", "unchanged"]

MIN_RECORDS = 10  # smaller sources swing too much for count and fill-rate checks
DROP_RATIO = 0.75  # a run with over 25% fewer records than the last good one is "degraded"
FILL_MIN_BASELINE = 0.5  # only fields most records carried are checked for a fill-rate drop
FILL_DROP_RATIO = 0.5  # ... and they warn when they fill half as often or less
REJECTION_RISE = 0.2  # the rejected share rose by at least 20 percentage points
REJECTION_MIN = 3  # ... and at least this many records were rejected


class Check(BaseModel):
    kind: CheckKind
    message: str
    field: str | None = None


class SourceStatus(BaseModel):
    id: str
    enabled: bool
    health: Health
    checked_at: datetime | None = None
    last_success_at: datetime | None = None
    consecutive_failures: int = 0
    records: int = 0
    previous_records: int | None = None
    rejections: int = 0
    pages: int = 0
    model_calls: int = 0
    cost_eur: float = 0.0
    field_fill_rates: dict[str, float] = Field(default_factory=dict)
    warnings: list[Check] = Field(default_factory=list)
    baseline_fill_rates: dict[str, float] = Field(
        default_factory=dict, description="Fill rates of the last run without warnings."
    )
    baseline_rejection_rate: float | None = Field(
        default=None, description="Rejected share of the last run without warnings."
    )
    records_digest: str | None = None
    records_changed_at: datetime | None = None
    error_kind: str | None = None
    error: str | None = None
    note: str | None = None
    terms: TermsState | None = Field(
        default=None, description="Fingerprints of the terms pages, from `gleanyard terms`."
    )


class ProjectStatus(BaseModel):
    version: Literal[1] = 1
    generated_at: datetime
    schema_name: str | None
    healthy: bool
    sources: list[SourceStatus]


def load_status(project: Project) -> ProjectStatus | None:
    path = project.root / project.config.status
    try:
        return ProjectStatus.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_status(
    project: Project,
    runs: list[SourceRun],
    previous: ProjectStatus | None = None,
    now: datetime | None = None,
) -> ProjectStatus:
    """Merge this run's results into the previous report; sources not run keep their entry."""
    now = now or datetime.now(UTC)
    before = {item.id: item for item in previous.sources} if previous else {}
    ran = {run.source_id: run for run in runs}
    entries = []
    for source in project.sources:
        old = before.get(source.id)
        run = ran.get(source.id)
        if run is None or run.from_cache:
            entry = (
                old.model_copy() if old else SourceStatus(id=source.id, enabled=True, health="ok")
            )
            entry.enabled = source.enabled
            if not source.enabled:
                entry.health = "disabled"
            elif old is None:
                entry.note = "not collected yet"
            entries.append(entry)
            continue
        entries.append(_from_run(source, run, old, now))
    return ProjectStatus(
        generated_at=now,
        schema_name=project.schema.name if project.schema else None,
        healthy=all(entry.health in {"ok", "disabled"} for entry in entries),
        sources=entries,
    )


def _baseline(old: SourceStatus | None) -> int | None:
    """The record count a new run is compared with: the last count before any drop."""
    if old is None or old.health == "disabled":
        return None
    return old.previous_records if old.health == "degraded" else old.records


def _baseline_fill_rates(old: SourceStatus | None) -> dict[str, float]:
    if old is None or old.health == "disabled":
        return {}
    if old.baseline_fill_rates:
        return dict(old.baseline_fill_rates)
    # Reports written before baselines existed: a healthy entry is its own baseline.
    return dict(old.field_fill_rates) if old.health == "ok" else {}


def _baseline_rejection_rate(old: SourceStatus | None) -> float | None:
    if old is None or old.health == "disabled":
        return None
    if old.baseline_rejection_rate is not None:
        return old.baseline_rejection_rate
    if old.health == "ok" and old.records + old.rejections:
        return old.rejections / (old.records + old.rejections)
    return None


def _from_run(
    source: SourceConfig, run: SourceRun, old: SourceStatus | None, now: datetime
) -> SourceStatus:
    last_count = _baseline(old)
    fill_baseline = _baseline_fill_rates(old)
    rejection_baseline = _baseline_rejection_rate(old)
    status = SourceStatus(
        id=source.id,
        enabled=source.enabled,
        health="ok",
        checked_at=now,
        last_success_at=old.last_success_at if old else None,
        records=run.records,
        previous_records=last_count,
        rejections=run.rejections,
        pages=run.pages,
        model_calls=run.model_calls,
        cost_eur=run.cost_eur,
        field_fill_rates=run.field_fill_rates,
        baseline_fill_rates=fill_baseline,
        baseline_rejection_rate=rejection_baseline,
        records_digest=old.records_digest if old else None,
        records_changed_at=old.records_changed_at if old else None,
        terms=old.terms if old else None,
        error_kind=run.error_kind,
        error=run.error,
    )
    if not run.ok:
        status.health = "failing"
        status.consecutive_failures = (old.consecutive_failures if old else 0) + 1
        status.records = last_count or 0
        status.previous_records = None
        return status
    status.last_success_at = now
    if run.records_digest is not None and run.records_digest != status.records_digest:
        status.records_digest = run.records_digest
        status.records_changed_at = now
    elif status.records_changed_at is None:
        status.records_changed_at = now
    status.warnings = _checks(source, run, status, now)
    if status.warnings:
        status.health = "degraded"
        status.note = "; ".join(check.message for check in status.warnings)
        return status
    # A clean run becomes the baseline the next runs are compared with.
    status.baseline_fill_rates = dict(run.field_fill_rates)
    total = run.records + run.rejections
    status.baseline_rejection_rate = run.rejections / total if total else rejection_baseline
    if run.records == 0:
        status.note = "no records, as expected for a seasonal source"
    return status


def _checks(
    source: SourceConfig, run: SourceRun, status: SourceStatus, now: datetime
) -> list[Check]:
    """Compare a run that worked with the baseline that ``status`` carries."""
    checks: list[Check] = []
    last_count = status.previous_records
    seasonal_gap = source.expect_empty == "seasonal" and run.records == 0
    if (
        last_count
        and last_count >= MIN_RECORDS
        and not seasonal_gap
        and run.records < last_count * DROP_RATIO
    ):
        checks.append(
            Check(kind="record_drop", message=f"records fell from {last_count} to {run.records}")
        )
    if run.records >= MIN_RECORDS:
        for field, before in sorted(status.baseline_fill_rates.items()):
            now_rate = run.field_fill_rates.get(field, 0.0)
            if before >= FILL_MIN_BASELINE and now_rate <= before * FILL_DROP_RATIO:
                checks.append(
                    Check(
                        kind="fill_rate_drop",
                        field=field,
                        message=f"{field} filled in {now_rate:.0%} of records, was {before:.0%}",
                    )
                )
    total = run.records + run.rejections
    if total and run.rejections >= REJECTION_MIN:
        share = run.rejections / total
        before = status.baseline_rejection_rate or 0.0
        if share - before >= REJECTION_RISE:
            checks.append(
                Check(
                    kind="rejection_spike",
                    message=f"{share:.0%} of records rejected, was {before:.0%}",
                )
            )
    days = source.expect_change_days
    if days and run.records and status.records_changed_at is not None:
        idle = now - status.records_changed_at
        if idle.days >= days:
            checks.append(
                Check(
                    kind="unchanged",
                    message=(
                        f"records unchanged since {status.records_changed_at:%Y-%m-%d} "
                        f"({idle.days} days, limit {days})"
                    ),
                )
            )
    return checks


def write_status(project: Project, status: ProjectStatus) -> Path:
    path = project.root / project.config.status
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return path
