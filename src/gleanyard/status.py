"""``status.json``: one health report per project, written after every ``collect``.

The status hub reads this file from each project repo, so it carries everything needed to
alert on a change without opening the data: per-source health, record counts against the
last good run, and when each source last succeeded. State between runs (last success,
consecutive failures, previous count) is read back from the previous report.
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

Health = Literal["ok", "degraded", "failing", "disabled"]
DROP_RATIO = 0.5  # a run with under half the last good record count is "degraded"


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
    field_fill_rates: dict[str, float] = Field(default_factory=dict)
    error_kind: str | None = None
    error: str | None = None
    note: str | None = None


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


def _from_run(
    source: SourceConfig, run: SourceRun, old: SourceStatus | None, now: datetime
) -> SourceStatus:
    last_success = old.last_success_at if old else None
    last_count = _baseline(old)
    status = SourceStatus(
        id=source.id,
        enabled=source.enabled,
        health="ok",
        checked_at=now,
        last_success_at=last_success,
        records=run.records,
        previous_records=last_count,
        rejections=run.rejections,
        pages=run.pages,
        field_fill_rates=run.field_fill_rates,
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
    seasonal_gap = source.expect_empty == "seasonal" and run.records == 0
    if last_count and not seasonal_gap and run.records < last_count * DROP_RATIO:
        status.health = "degraded"
        status.note = f"records fell from {last_count} to {run.records}"
    elif run.records == 0:
        status.note = "no records, as expected for a seasonal source"
    return status


def write_status(project: Project, status: ProjectStatus) -> Path:
    path = project.root / project.config.status
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
    return path
