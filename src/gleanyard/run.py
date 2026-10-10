"""Collect and export for a whole project.

Storage layout under the project's storage root (``data/`` by default)::

    raw/                     content-addressed raw cache (see cache.py)
    records/<source>.jsonl   latest schema-valid records per source
    review/<source>.jsonl    latest rejected records per source, for review
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
from pydantic import BaseModel

from gleanyard.cache import RawCache
from gleanyard.collect import OutputRecord, extract
from gleanyard.config import SourceConfig
from gleanyard.errors import SourceError
from gleanyard.export import public_record
from gleanyard.fetch import Fetcher, Response
from gleanyard.project import Project
from gleanyard.storage import LocalObjectStore


class SourceRun(BaseModel):
    source_id: str
    ok: bool
    records: int = 0
    rejections: int = 0
    error_kind: str | None = None
    error: str | None = None
    from_cache: bool = False


def _store(project: Project) -> LocalObjectStore:
    return LocalObjectStore(project.root / project.config.storage.root)


def collect_source(
    project: Project,
    source: SourceConfig,
    *,
    from_cache: bool = False,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> SourceRun:
    assert project.schema is not None
    actions = source.rights.allowed_actions
    if not (actions.private_store and actions.normalize):
        return SourceRun(
            source_id=source.id,
            ok=False,
            error_kind="rights",
            error="rights do not allow private_store and normalize",
        )
    store = _store(project)
    cache = RawCache(store)
    try:
        if from_cache:
            response = _cached_response(cache, source)
        else:
            fetcher = Fetcher(
                source, user_agent=project.config.user_agent, cache=cache, client=client
            )
            response = fetcher.get(str(source.url))
            manifest = fetcher.finish()
        result = extract(source, project.schema, response, today=today)
    except FileNotFoundError as error:
        return SourceRun(source_id=source.id, ok=False, error_kind="no_cache", error=str(error))
    except SourceError as error:
        return SourceRun(
            source_id=source.id,
            ok=False,
            error_kind=error.kind.value,
            error=str(error),
            from_cache=from_cache,
        )
    _write_jsonl(store, f"records/{source.id}.jsonl", result.records)
    _write_jsonl(store, f"review/{source.id}.jsonl", result.rejections)
    healthy = bool(result.records) or source.expect_empty == "seasonal"
    if healthy and not from_cache:
        cache.mark_good(manifest)
    return SourceRun(
        source_id=source.id,
        ok=healthy,
        records=len(result.records),
        rejections=len(result.rejections),
        error_kind=None if healthy else "empty",
        from_cache=from_cache,
    )


def _cached_response(cache: RawCache, source: SourceConfig) -> Response:
    manifest = cache.latest_manifest(source.id)
    if manifest is None or not manifest.responses:
        raise FileNotFoundError(f"{source.id}: no cached run to re-extract")
    cached = manifest.responses[-1]
    return Response(**cached.model_dump(), body=cache.get_body(cached.sha256), from_cache=True)


def load_records(project: Project, source_id: str) -> list[OutputRecord]:
    store = _store(project)
    key = f"records/{source_id}.jsonl"
    if not store.exists(key):
        return []
    lines = store.get(key).decode().splitlines()
    return [OutputRecord.model_validate_json(line) for line in lines if line.strip()]


def export_project(project: Project, today: date | None = None) -> tuple[Path, int]:
    """Write the public export from stored records and the current rights config."""
    assert project.schema is not None
    rights = {source.id: source.rights for source in project.sources}
    rows = []
    for source in project.sources:
        for record in load_records(project, source.id):
            row = public_record(record, project.schema, rights, project.config.rights_policy, today)
            if row is not None:
                rows.append(row)
    path = project.root / project.config.export
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path, len(rows)


def _write_jsonl(store: LocalObjectStore, key: str, rows: list[BaseModel]) -> None:
    store.put(key, "".join(row.model_dump_json() + "\n" for row in rows).encode())
