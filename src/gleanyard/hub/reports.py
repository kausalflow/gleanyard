"""Read a project's report: gleanyard's ``status.json`` or a DataHerb job status.

A DataHerb job status (``dataherb.status/v1``, https://dataherb.github.io) is what
``collect`` writes under ``_dataherb/status/<id>/latest.json`` when a project has a
``dataherb`` block, and what other DataHerb producers write for their own jobs. The hub turns
it into the same ``ProjectStatus`` it reads from ``status.json``, so alerts and the dashboard
work the same for both:

- a check named ``<source>`` is a source: ``pass`` is ok, ``fail`` is failing (or degraded,
  when gleanyard wrote ``degraded:`` in front of its message);
- a check named ``<source>/<kind>[/<field>]`` with status ``warn`` is a warning on that
  source; a gleanyard kind (``record_drop`` ...) keeps its alert, any other is a ``warning``;
- a file with no checks is one source, named after the job, healthy when its run succeeded.

The DataHerb file carries no fill rates, terms or baselines, so those parts of the dashboard
stay empty for such a project.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, get_args

from gleanyard.status import Check, CheckKind, ProjectStatus, SourceStatus

DATAHERB_STATUS = "dataherb.status/v1"
_KINDS = set(get_args(CheckKind))


def parse_report(body: bytes) -> ProjectStatus:
    """A project's report from either format; ``ValueError`` when it is neither."""
    data = json.loads(body)
    if isinstance(data, dict) and data.get("spec") == DATAHERB_STATUS:
        return from_dataherb(data)
    return ProjectStatus.model_validate(data)


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def from_dataherb(data: dict[str, Any]) -> ProjectStatus:
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    last = data.get("last_success") if isinstance(data.get("last_success"), dict) else {}
    generated_at = (
        _time(data.get("written_at"))
        or _time(run.get("finished_at"))
        or _time(run.get("started_at"))
    )
    if generated_at is None:
        raise ValueError("the DataHerb status has no written_at or run time")
    last_success_at = _time(last.get("finished_at")) or _time(last.get("started_at"))
    if run.get("status") == "success":
        last_success_at = last_success_at or generated_at

    sources: dict[str, SourceStatus] = {}

    def source(name: str) -> SourceStatus:
        if name not in sources:
            sources[name] = SourceStatus(
                id=name,
                enabled=True,
                health="ok",
                checked_at=generated_at,
                last_success_at=last_success_at,
            )
        return sources[name]

    checks = [check for check in data.get("checks") or [] if isinstance(check, dict)]
    for check in checks:
        name = str(check.get("name") or "")
        message = str(check.get("message") or "")
        if not name:
            continue
        if check.get("status") == "warn":
            owner, _, rest = name.partition("/")
            kind, _, field = rest.partition("/")
            entry = source(owner)
            entry.warnings.append(
                Check(
                    kind=kind if kind in _KINDS else "warning",
                    message=message or name,
                    field=field or None,
                )
            )
            if entry.health == "ok":
                entry.health = "degraded"
            continue
        entry = source(name)
        if isinstance(check.get("value"), int) and not isinstance(check.get("value"), bool):
            entry.records = check["value"]
        if check.get("status") == "fail":
            if message.startswith("degraded:"):
                entry.note = message.removeprefix("degraded:").strip() or None
                if entry.health == "ok":
                    entry.health = "degraded"
            else:
                entry.health = "failing"
                entry.consecutive_failures = 1
                entry.error_kind = "check_failed"
                entry.error = message.removeprefix("failing:").strip() or None

    if not sources:
        name = str(job.get("id") or job.get("name") or "job")
        entry = source(name)
        entry.records = sum(
            dataset.get("rows") or 0
            for dataset in data.get("datasets") or []
            if isinstance(dataset, dict) and isinstance(dataset.get("rows"), int)
        )
        status = run.get("status")
        error = run.get("error") if isinstance(run.get("error"), dict) else {}
        if status in {"failed", "partial"}:
            entry.health = "failing" if status == "failed" else "degraded"
            entry.consecutive_failures = 1 if status == "failed" else 0
            entry.error_kind = str(error.get("type") or "run_failed")
            entry.error = str(error.get("message") or run.get("message") or status)
            if status == "partial":
                entry.warnings.append(Check(kind="warning", message=entry.error))
    elif run.get("status") in {"failed", "partial"} and all(
        entry.health == "ok" for entry in sources.values()
    ):
        # The run failed though every check passed: say so on the first source.
        error = run.get("error") if isinstance(run.get("error"), dict) else {}
        first = next(iter(sources.values()))
        first.health = "failing"
        first.consecutive_failures = 1
        first.error_kind = str(error.get("type") or "run_failed")
        first.error = str(error.get("message") or run.get("message") or run.get("status"))

    report = list(sources.values())
    return ProjectStatus(
        generated_at=generated_at,
        schema_name=str(job.get("name")) if job.get("name") else None,
        healthy=all(entry.health == "ok" for entry in report),
        sources=report,
    )
