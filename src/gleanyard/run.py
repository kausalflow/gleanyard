"""Collect and export for a whole project.

Storage layout under the project's storage root (``data/`` by default)::

    raw/                     content-addressed raw cache (see cache.py)
    records/<source>.jsonl   latest schema-valid records per source
    review/<source>.jsonl    latest rejected records per source, for review
    changes/<date>.jsonl     records added, changed or removed by each run (see changes.py)
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from pydantic import BaseModel, Field

from gleanyard.cache import RawCache
from gleanyard.changes import (
    FEED_DAYS,
    append_changes,
    carry_first_seen,
    change_feed,
    diff_records,
    load_changes,
    prune_changes,
)
from gleanyard.collect import Extraction, OutputRecord, Rejection, extract
from gleanyard.config import Delivery, LlmExtract, Pagination, SelectorsExtract, SourceConfig
from gleanyard.errors import FilterIgnored, PaginationChanged, SourceError, StructureChanged
from gleanyard.export import csv_text, public_record
from gleanyard.extract.fields import join_tables
from gleanyard.extract.llm import LlmSession, ModelClient, PydanticAiClient
from gleanyard.extract.selectors import SelectorSchema
from gleanyard.fetch import Fetcher, Response
from gleanyard.merge import merge_rows, prefer_rows
from gleanyard.project import Project
from gleanyard.readers import read_sitemap
from gleanyard.records import dump_records, parse_records
from gleanyard.rights import collect_blocker
from gleanyard.schema import FieldIssue
from gleanyard.storage import LocalObjectStore


class SourceRun(BaseModel):
    source_id: str
    ok: bool
    records: int = 0
    rejections: int = 0
    error_kind: str | None = None
    error: str | None = None
    from_cache: bool = False
    field_fill_rates: dict[str, float] = Field(default_factory=dict)
    pages: int = 0
    model_calls: int = 0
    cost_eur: float = 0.0
    records_digest: str | None = Field(
        default=None, description="Hash of the record values, to notice data that never changes."
    )


def digest_records(records: list[OutputRecord]) -> str:
    values = sorted(json.dumps(record.values, sort_keys=True, default=str) for record in records)
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def record_store(project: Project) -> LocalObjectStore:
    """The project's private storage: raw cache, records, review and change logs."""
    return _store(project)


def _store(project: Project) -> LocalObjectStore:
    return LocalObjectStore(project.root / project.config.storage.root)


def collect_source(
    project: Project,
    source: SourceConfig,
    *,
    from_cache: bool = False,
    client: httpx.Client | None = None,
    today: date | None = None,
    llm_client: ModelClient | None = None,
    now: datetime | None = None,
) -> SourceRun:
    assert project.schema is not None
    actions = source.rights.allowed_actions
    blocker = collect_blocker(source.rights, project.config.rights_policy)
    if blocker is not None:
        return SourceRun(source_id=source.id, ok=False, error_kind="rights", error=blocker)
    if isinstance(source.extract, LlmExtract) and not actions.send_to_model:
        return SourceRun(
            source_id=source.id,
            ok=False,
            error_kind="rights",
            error="rights do not allow send_to_model, which llm mode needs",
        )
    store = _store(project)
    cache = RawCache(store)
    session = None
    if isinstance(source.extract, LlmExtract):
        session = LlmSession(
            store=store,
            client=llm_client,
            make_client=None if from_cache or llm_client else PydanticAiClient,
        )
    try:
        selectors = selector_schema(project, source)
        if from_cache:
            responses = cached_pages(cache, source)
            result = extract_pages(project, source, responses, today, selectors, session)
        else:
            fetcher = Fetcher(
                source, user_agent=project.config.user_agent, cache=cache, client=client
            )
            if source.pagination is not None:
                responses, result = fetch_paginated(
                    fetcher,
                    source,
                    lambda page: extract_pages(project, source, [page], today, selectors, session),
                )
            else:
                responses = fetch_pages(fetcher, source)
                result = extract_pages(project, source, responses, today, selectors, session)
            manifest = fetcher.finish()
        apply_filter(source, result)
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
    healthy = bool(result.records) or source.expect_empty == "seasonal"
    previous = load_records(project, source.id)
    carry_first_seen(previous, result.records)
    if healthy:
        now = now or datetime.now(UTC)
        stored = result.records
        if source.keep_removed:
            found = {record.id for record in result.records}
            stored = result.records + [record for record in previous if record.id not in found]
        append_changes(store, diff_records(source.id, previous, stored, now))
        prune_changes(store, project.config.storage.changes_days, now.date())
        store.put(
            f"records/{source.id}.jsonl", dump_records(stored, project.config.storage.records)
        )
    # An empty run that is not seasonal is a failure, not the removal of every record: the
    # last records stay until a healthy run replaces them.
    _write_jsonl(store, f"review/{source.id}.jsonl", result.rejections)
    if healthy and not from_cache:
        cache.mark_good(manifest)
    return SourceRun(
        source_id=source.id,
        ok=healthy,
        records=len(result.records),
        rejections=len(result.rejections),
        error_kind=None if healthy else "empty",
        from_cache=from_cache,
        field_fill_rates=result.field_fill_rates,
        pages=len(responses),
        model_calls=session.calls if session else 0,
        cost_eur=round(session.cost_eur, 6) if session else 0.0,
        records_digest=digest_records(result.records),
    )


def apply_filter(source: SourceConfig, result: Extraction) -> None:
    """Check the result against the source's URL filter, in place.

    Records with no value for the filtered field cannot be judged and stay. When too
    many records fall outside the filter it was not applied by the source, so the run
    fails; a few strays are moved to the review file.
    """
    rule = source.filter
    if rule is None:
        return
    inside = {_fold(value) for value in rule.one_of}
    judged = [record for record in result.records if rule.field in record.values]
    outside = [record for record in judged if _fold(record.values[rule.field]) not in inside]
    if not outside:
        return
    share = len(outside) / len(judged)
    if share > rule.max_outside_share:
        raise FilterIgnored(
            source.id,
            f"{len(outside)} of {len(judged)} records have {rule.field} outside "
            f"{', '.join(map(str, rule.one_of))}; the source's filter is no longer applied",
        )
    gone = {record.id for record in outside}
    result.records = [record for record in result.records if record.id not in gone]
    for record in outside:
        result.rejections.append(
            Rejection(
                source_id=source.id,
                url=record.provenance.url,
                raw_sha256=record.provenance.raw_sha256,
                values=record.values,
                errors=[
                    FieldIssue(
                        field=rule.field,
                        reason="outside the source filter",
                        value=str(record.values[rule.field]),
                    )
                ],
            )
        )


def _fold(value: object) -> str:
    return str(value).casefold()


def selector_schema(project: Project, source: SourceConfig) -> SelectorSchema | None:
    if not isinstance(source.extract, SelectorsExtract):
        return None
    path = project.root / source.extract.selectors
    try:
        return SelectorSchema.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise StructureChanged(
            source.id, f"selector file {path.name} is invalid: {error}"
        ) from error


def fetch_pages(fetcher: Fetcher, source: SourceConfig) -> list[Response]:
    """The source URL; for a sitemap, the sitemap followed by each page it lists."""
    first = fetcher.get(str(source.url))
    if source.delivery != Delivery.SITEMAP:
        return [first]
    urls = read_sitemap(source, first.body, source.reader_options)
    return [first, *(fetcher.get(url) for url in urls)]


def page_url(url: str, pagination: Pagination, index: int) -> str:
    """``url`` with the paging parameter set for page ``index`` (0 is the first page)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    query = [(k, v) for k, v in query if k != pagination.param]
    query.append((pagination.param, str(pagination.start + index * pagination.step)))
    return parts._replace(query=urlencode(query)).geturl()


def fetch_paginated(
    fetcher: Fetcher,
    source: SourceConfig,
    extract_page: Callable[[Response], Extraction],
) -> tuple[list[Response], Extraction]:
    """Fetch and extract pages until one is empty; fail if paging stops behaving.

    Pagination has changed when a page repeats an earlier document or only records
    already seen (the parameter is ignored or loops), or when records are still coming
    after ``max_pages`` (the source has grown past what was configured).
    """
    assert source.pagination is not None
    pagination = source.pagination
    responses: list[Response] = []
    merged = Extraction()
    seen_documents: set[str] = set()
    seen_ids: set[str] = set()
    for index in range(pagination.max_pages):
        page = fetcher.get(page_url(str(source.url), pagination, index))
        if page.sha256 in seen_documents:
            raise PaginationChanged(
                source.id,
                f"page {index + 1} is the same document as an earlier page; "
                f"the {pagination.param!r} parameter no longer selects a page",
            )
        seen_documents.add(page.sha256)
        responses.append(page)
        result = extract_page(page)
        if not result.records and not result.rejections:
            return responses, merged
        fresh = [record for record in result.records if record.id not in seen_ids]
        if index > 0 and result.records and not fresh:
            raise PaginationChanged(
                source.id,
                f"page {index + 1} only repeats records from earlier pages; paging may be looping",
            )
        seen_ids.update(record.id for record in fresh)
        merged.records.extend(fresh)
        merged.rejections.extend(result.rejections)
    raise PaginationChanged(
        source.id,
        f"still returning records after {pagination.max_pages} pages; "
        "raise pagination.max_pages if the source really grew",
    )


def cached_pages(cache: RawCache, source: SourceConfig) -> list[Response]:
    manifest = cache.latest_manifest(source.id)
    if manifest is None or not manifest.responses:
        raise FileNotFoundError(f"{source.id}: no cached run to re-extract")
    replay_all = source.delivery == Delivery.SITEMAP or source.pagination is not None
    cached = manifest.responses if replay_all else manifest.responses[-1:]
    return [
        Response(**item.model_dump(), body=cache.get_body(item.sha256), from_cache=True)
        for item in cached
    ]


def extract_pages(
    project: Project,
    source: SourceConfig,
    responses: list[Response],
    today: date | None,
    selectors: SelectorSchema | None,
    llm: LlmSession | None = None,
) -> Extraction:
    """Extract every page; a sitemap's first response is the index, not a page."""
    assert project.schema is not None
    pages = responses[1:] if source.delivery == Delivery.SITEMAP else responses
    tables = join_tables(project.root, source.extract)
    merged = Extraction()
    seen: set[str] = set()
    for page in pages:
        result = extract(
            source,
            project.schema,
            page,
            today=today,
            selectors=selectors,
            llm=llm,
            base_url=page.url if source.delivery == Delivery.SITEMAP else None,
            tables=tables,
        )
        for record in result.records:
            if record.id not in seen:
                seen.add(record.id)
                merged.records.append(record)
        merged.rejections.extend(result.rejections)
    return merged


def load_records(project: Project, source_id: str) -> list[OutputRecord]:
    store = _store(project)
    key = f"records/{source_id}.jsonl"
    if not store.exists(key):
        return []
    return parse_records(store.get(key).decode())


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
    rows = merge_rows(prefer_rows(rows, project.schema), project.schema)
    config = project.config
    if config.export_sort:
        rows.sort(key=lambda row: tuple(_sort_key(row.get(name)) for name in config.export_sort))
    if config.export:
        _write_export(project.root / config.export, rows, config.export_partition, _jsonl_text)
    if config.export_csv:
        # CSV is compact enough to keep one whole file for spreadsheets; with a partition,
        # the parts are written next to it as well.
        schema, columns = project.schema, config.export_csv_columns
        csv_path = project.root / config.export_csv
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(csv_text(rows, schema, columns), encoding="utf-8")
        if config.export_partition:
            _write_export(
                csv_path,
                rows,
                config.export_partition,
                lambda part: csv_text(part, schema, columns),
                folder=csv_parts_dir(project),
            )
    if config.changes_feed:
        write_change_feed(project, today or date.today())
    main = config.export or config.export_csv
    assert main is not None  # validate requires one of them
    return export_location(project, main), len(rows)


def _sort_key(value: Any) -> tuple[int, Any]:
    """Missing values last; numbers before text, so mixed columns still sort."""
    if value in (None, "", []):
        return (2, "")
    if isinstance(value, bool | int | float):
        return (0, value)
    return (1, value if isinstance(value, str) else json.dumps(value, sort_keys=True))


def export_location(project: Project, configured: str) -> Path:
    """Where an export path's rows are: the file, or for partitioned JSON lines the folder."""
    path = project.root / configured
    partitioned = project.config.export_partition and configured == project.config.export
    return partition_dir(path) if partitioned else path


def export_files(project: Project, configured: str) -> list[Path]:
    """The files an export path's rows were written to, sorted."""
    path = project.root / configured
    if project.config.export_partition is None or configured != project.config.export:
        return [path] if path.is_file() else []
    return sorted(partition_dir(path).glob(f"*{path.suffix}"))


def csv_parts_dir(project: Project) -> Path | None:
    """The folder of the CSV parts, when the export is partitioned and written as CSV."""
    config = project.config
    if not (config.export_csv and config.export_partition):
        return None
    if config.export_csv_parts:
        return project.root / config.export_csv_parts
    return partition_dir(project.root / config.export_csv)


def partition_dir(path: Path) -> Path:
    """The folder of a partitioned export: exports/records.jsonl -> exports/records."""
    return path.with_suffix("")


def partition_name(value: Any) -> str:
    """File stem for one partition value: lower case, runs of other characters as ``-``."""
    if value in (None, "", []):
        return "_none"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return re.sub(r"[^a-z0-9_]+", "-", text.casefold()).strip("-") or "_other"


def _write_export(
    path: Path,
    rows: list[dict[str, Any]],
    partition: str | None,
    render: Callable[[list[dict[str, Any]]], str],
    folder: Path | None = None,
) -> None:
    if partition is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(rows), encoding="utf-8")
        return
    parts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parts.setdefault(partition_name(row.get(partition)), []).append(row)
    folder = folder or partition_dir(path)
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob(f"*{path.suffix}"):
        if stale.stem not in parts:
            stale.unlink()
    for name, part in sorted(parts.items()):
        (folder / f"{name}{path.suffix}").write_text(render(part), encoding="utf-8")


def _jsonl_text(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def write_change_feed(project: Project, today: date) -> Path:
    """Write the public JSON Feed of the last ``FEED_DAYS`` days of changes."""
    assert project.config.changes_feed is not None
    changes = load_changes(_store(project), since=today - timedelta(days=FEED_DAYS))
    feed = change_feed(project, changes, today)
    path = project.root / project.config.changes_feed
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_jsonl(store: LocalObjectStore, key: str, rows: list[BaseModel]) -> None:
    store.put(key, "".join(row.model_dump_json() + "\n" for row in rows).encode())
