"""Collect and export for a whole project.

Storage layout under the project's storage root (``data/`` by default)::

    raw/                     content-addressed raw cache (see cache.py)
    records/<source>.jsonl   latest schema-valid records per source
    review/<source>.jsonl    latest rejected records per source, for review
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from pydantic import BaseModel, Field

from gleanyard.cache import RawCache
from gleanyard.collect import Extraction, OutputRecord, Rejection, extract
from gleanyard.config import Delivery, LlmExtract, Pagination, SelectorsExtract, SourceConfig
from gleanyard.errors import FilterIgnored, PaginationChanged, SourceError, StructureChanged
from gleanyard.export import public_record
from gleanyard.extract.llm import LlmSession, ModelClient, PydanticAiClient
from gleanyard.extract.selectors import SelectorSchema
from gleanyard.fetch import Fetcher, Response
from gleanyard.project import Project
from gleanyard.readers import read_sitemap
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
        field_fill_rates=result.field_fill_rates,
        pages=len(responses),
        model_calls=session.calls if session else 0,
        cost_eur=round(session.cost_eur, 6) if session else 0.0,
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
    # Split on "\n" only: str.splitlines() also breaks on U+2028, U+0085 and form feeds,
    # which JSON leaves raw inside strings.
    lines = store.get(key).decode().split("\n")
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
