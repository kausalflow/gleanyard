"""DataHerb output: list a project in a DataHerb catalog and report its runs there.

With a ``dataherb`` block in ``gleanyard.yaml``, ``export`` writes ``dataherb.json``, the
dataset metadata a DataHerb catalog reads (https://dataherb.github.io/specs/dataset/v2.json,
a Frictionless Data Package underneath), and ``collect`` writes a job status file
(``dataherb.status/v1``) under ``<status_dir>/<id>/``: ``latest.json`` plus one file per run
in ``runs/``. Both are plain files in the repo, so a catalog reads them from git like any
other DataHerb dataset.

The metadata describes the export only, so it never lists a field export would drop
(personal data) and lists no resource when no source may be exported. Each enabled source
becomes a check in the status file; a failing or degraded source is a failed check, which
DataHerb shows as a degraded job.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from gleanyard import __version__
from gleanyard.config import DataherbConfig
from gleanyard.project import Project
from gleanyard.rights import export_blocker
from gleanyard.run import SourceRun
from gleanyard.schema import FieldClass, FieldSpec, FieldType
from gleanyard.status import ProjectStatus

DATASET_SPEC = "dataherb/v2"
STATUS_SPEC = "dataherb.status/v1"

# gleanyard field types as Frictionless Table Schema types.
_TYPES = {
    FieldType.DATE: "date",
    FieldType.DATETIME: "datetime",
    FieldType.INT: "integer",
    FieldType.FLOAT: "number",
    FieldType.BOOL: "boolean",
    FieldType.MONEY: "object",
}


def job_id(project: Project, config: DataherbConfig) -> str:
    return config.id or re.sub(r"[^A-Za-z0-9._-]+", "-", project.root.name).strip("-._")


def _repo_url(config: DataherbConfig) -> str | None:
    if config.uri:
        return config.uri
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        return None
    return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}"


def _field(name: str, spec: FieldSpec) -> dict[str, Any]:
    field: dict[str, Any] = {"name": name, "type": _TYPES.get(spec.type, "string")}
    if spec.many:
        field["type"] = "array"
    if spec.description:
        field["description"] = spec.description
    return field


def build_metadata(project: Project, rows: int, size: int, today: date | None = None) -> dict:
    """The ``dataherb.json`` document for the export just written."""
    config = project.config.dataherb
    schema = project.schema
    assert config is not None and schema is not None
    today = today or date.today()
    exportable = [
        source.rights
        for source in project.sources
        if source.enabled
        and export_blocker(source.rights, project.config.rights_policy, today) is None
    ]
    licences = {rights.license_id for rights in exportable if rights.license_id}
    licence = config.license or (licences.pop() if len(licences) == 1 else None)
    identifier = job_id(project, config)
    document: dict[str, Any] = {
        "spec": DATASET_SPEC,
        "id": identifier,
        "name": config.name or schema.name,
        "description": config.description or schema.description,
        "tags": config.tags or None,
        "owner": config.owner,
        "license": licence,
        "classification": "public" if exportable else "internal",
        "update_frequency": config.expected_interval,
        "status_job": identifier,
    }
    uri = _repo_url(config)
    if uri:
        document["source"] = "git"
        document["uri"] = uri
    resources = []
    if exportable:
        fields = [{"name": "id", "type": "string", "description": "Stable record id."}]
        fields += [
            _field(name, spec)
            for name, spec in schema.fields.items()
            if spec.field_class != FieldClass.PERSONAL
        ]
        fields.append(
            {
                "name": "_source",
                "type": "object",
                "description": "Provenance: source_id, url, retrieved_at, mode, attribution.",
            }
        )
        resources.append(
            {
                "name": "records",
                "path": project.config.export,
                "format": "ndjson",
                "mediatype": "application/x-ndjson",
                "rows": rows,
                "bytes": size,
                "schema": {"fields": fields, "primaryKey": "id"},
            }
        )
    document["datapackage"] = {"resources": resources}
    return {key: value for key, value in document.items() if value is not None}


def write_metadata(project: Project, rows: int, export_path: Path) -> Path | None:
    config = project.config.dataherb
    if config is None or project.schema is None:
        return None
    document = build_metadata(project, rows, export_path.stat().st_size)
    path = project.root / config.metadata
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_info() -> dict[str, Any]:
    """Run id, link and trigger from GitHub Actions, when running there."""
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return {"trigger": "manual"}
    info: dict[str, Any] = {
        "id": f"{run_id}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}",
        "attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
    }
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    info["trigger"] = {"schedule": "schedule", "workflow_dispatch": "manual"}.get(
        event, "event" if event else "unknown"
    )
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository:
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        info["url"] = f"{server}/{repository}/actions/runs/{run_id}"
    return info


def _run_status(runs: list[SourceRun]) -> str:
    failed = [run for run in runs if not run.ok]
    if not failed:
        return "success"
    return "failed" if len(failed) == len(runs) else "partial"


def _checks(report: ProjectStatus) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for entry in report.sources:
        if not entry.enabled:
            continue
        check: dict[str, Any] = {
            "name": entry.id,
            "status": "pass" if entry.health == "ok" else "fail",
            "value": entry.records,
        }
        message = entry.error or entry.note
        if message:
            check["message"] = f"{entry.health}: {message}"
        checks.append(check)
        for warning in entry.warnings:
            name = f"{entry.id}/{warning.kind}"
            if warning.field:
                name += f"/{warning.field}"
            checks.append({"name": name, "status": "warn", "message": warning.message})
    return checks


def build_job_status(
    project: Project,
    report: ProjectStatus,
    runs: list[SourceRun],
    started_at: datetime,
    previous: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """The ``latest.json`` document for one ``collect`` run."""
    config = project.config.dataherb
    assert config is not None
    now = now or datetime.now(UTC)
    identifier = job_id(project, config)
    info = _run_info()
    run: dict[str, Any] = {
        "id": info.pop("id", _iso(started_at)),
        "status": _run_status(runs),
        "started_at": _iso(started_at),
        "finished_at": _iso(now),
        "duration_seconds": round((now - started_at).total_seconds(), 3),
        **info,
    }
    failed = [item for item in runs if not item.ok]
    if failed:
        first = failed[0]
        run["message"] = f"{len(failed)} of {len(runs)} sources failed"
        run["error"] = {
            "type": first.error_kind or "error",
            "message": f"{first.source_id}: {first.error or 'no records'}",
        }
    job: dict[str, Any] = {
        "id": identifier,
        "name": config.name or (project.schema.name if project.schema else identifier),
        "orchestrator": "github-actions" if os.environ.get("GITHUB_RUN_ID") else "manual",
        "expected_interval": config.expected_interval,
        "max_duration": config.max_duration,
        "owner": config.owner,
        "tags": config.tags or None,
    }
    repo = _repo_url(config)
    if repo:
        job["url"] = repo
    previous = previous or {}
    last_success = run if run["status"] == "success" else previous.get("last_success")
    enabled = [entry for entry in report.sources if entry.enabled]
    return {
        "spec": STATUS_SPEC,
        "job": {key: value for key, value in job.items() if value is not None},
        "run": run,
        "last_success": last_success,
        "datasets": [{"id": identifier, "rows": sum(entry.records for entry in enabled)}],
        "checks": _checks(report),
        "metrics": {
            "sources": len(enabled),
            "sources_failing": sum(entry.health == "failing" for entry in enabled),
            "sources_degraded": sum(entry.health == "degraded" for entry in enabled),
            "records": sum(item.records for item in runs),
            "rejections": sum(item.rejections for item in runs),
            "pages": sum(item.pages for item in runs),
            "model_calls": sum(item.model_calls for item in runs),
            "cost_eur": round(sum(item.cost_eur for item in runs), 4),
        },
        "producer": {"name": "gleanyard", "version": __version__},
        "written_at": _iso(now),
    }


def write_job_status(
    project: Project, report: ProjectStatus, runs: list[SourceRun], started_at: datetime
) -> Path | None:
    """Write ``latest.json`` and this run's file in ``runs/``; return the latest path."""
    config = project.config.dataherb
    if config is None:
        return None
    base = project.root / config.status_dir / job_id(project, config)
    latest = base / "latest.json"
    try:
        previous = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    document = build_job_status(project, report, runs, started_at, previous)
    body = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    run = document["run"]
    started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(run["id"]))[:120]
    (base / "runs").mkdir(parents=True, exist_ok=True)
    (base / "runs" / f"{started:%Y%m%dT%H%M%SZ}-{safe_id}.json").write_text(body, encoding="utf-8")
    latest.write_text(body, encoding="utf-8")
    return latest
