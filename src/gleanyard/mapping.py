"""``gleanyard map SOURCE``: draft or repair a selector mapping file with crawl4ai.

crawl4ai's ``generate_schema`` asks a language model to write a JSON CSS schema from a
sample page. It is a setup and repair tool only: it is an optional extra (``gleanyard[map]``),
never imported by scheduled runs, and what it writes is the same mapping file the
``selectors`` mode runs without crawl4ai. The draft is checked before it is written:
it must validate, find records on the sample page, and say which schema fields it covers.
``gleanyard try`` then judges it with the full schema.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from gleanyard.config import Delivery, SelectorsExtract, SourceConfig
from gleanyard.errors import ConfigError, ModelError, SourceError
from gleanyard.extract.llm import field_guide
from gleanyard.extract.selectors import SelectorSchema, run_selectors
from gleanyard.fetch import Fetcher, Response
from gleanyard.project import Project
from gleanyard.readers import read_sitemap
from gleanyard.schema import ProjectSchema

# (page html, what to extract, example output, model id) -> a crawl4ai JSON CSS schema
Generator = Callable[[str, str, str, str | None], dict[str, Any]]

_EXAMPLE_VALUES = {"integer": 0, "number": 0.0, "boolean": False}


class MapReport(BaseModel):
    source_id: str
    path: str
    written: bool = False
    records: int = 0
    page: str | None = None
    field_fill_rates: dict[str, float] = Field(default_factory=dict)
    unknown_fields: list[str] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None and self.records > 0


def _crawl4ai() -> tuple[Any, Any]:
    try:
        from crawl4ai import JsonCssExtractionStrategy, LLMConfig
    except ImportError as error:
        raise ConfigError(
            "`map` needs crawl4ai, which is not installed; run: pip install 'gleanyard[map]'"
        ) from error
    return JsonCssExtractionStrategy, LLMConfig


def crawl4ai_generator(html: str, query: str, example: str, model: str | None) -> dict[str, Any]:
    JsonCssExtractionStrategy, LLMConfig = _crawl4ai()
    config = LLMConfig(provider=model) if model else LLMConfig()
    return JsonCssExtractionStrategy.generate_schema(
        html=html,
        schema_type="CSS",
        query=query,
        target_json_example=example,
        llm_config=config,
    )


def draft_mapping(
    project: Project,
    source: SourceConfig,
    *,
    generator: Generator = crawl4ai_generator,
    model: str | None = None,
    client: httpx.Client | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> MapReport:
    assert project.schema is not None
    if not isinstance(source.extract, SelectorsExtract):
        raise ConfigError(
            f"{source.id}: map drafts a selector mapping, but this source uses "
            f"mode {source.extract.mode!r}"
        )
    path = project.root / source.extract.selectors
    report = MapReport(source_id=source.id, path=source.extract.selectors)
    if not source.rights.allowed_actions.send_to_model:
        raise ConfigError(
            f"{source.id}: map sends the page to a model provider; set "
            "rights.allowed_actions.send_to_model once the source's rights allow it"
        )
    if generator is crawl4ai_generator:
        _crawl4ai()  # fail before fetching anything if crawl4ai is missing
    if path.exists() and not force:
        raise ConfigError(f"{report.path} already exists; use --force to replace it")
    try:
        page = _sample_page(project, source, client)
    except SourceError as error:
        report.error = f"{error.kind.value}: {error}"
        return report
    report.page = page.url
    try:
        draft = generator(page.text, _query(project.schema), _example(project.schema), model)
    except ConfigError:
        raise
    except Exception as error:
        raise ModelError(source.id, f"crawl4ai could not draft a mapping: {error}") from error
    try:
        mapping = SelectorSchema.model_validate(draft)
    except ValidationError as error:
        first = error.errors()[0]
        loc = ".".join(str(part) for part in first["loc"])
        report.error = f"the draft is not a usable mapping ({loc}: {first['msg']})"
        return report
    try:
        items = run_selectors(source.id, page.body, mapping)
    except SourceError as error:
        report.error = f"the draft does not run: {error}"
        return report
    report.records = len(items)
    if not items:
        report.error = "the draft finds no records on the sample page; not written"
        return report
    names = mapping.field_names
    report.field_fill_rates = {
        name: sum(1 for item in items if item.get(name) not in (None, "", [])) / len(items)
        for name in names
    }
    report.unknown_fields = [name for name in names if name not in project.schema.fields]
    covered = set(names) | set(source.extract.defaults)
    report.missing_required = [
        name for name in project.schema.required_fields if name not in covered
    ]
    if report.unknown_fields:
        report.notes.append(
            "fields not in the schema are ignored or rejected: rename or remove "
            + ", ".join(report.unknown_fields)
        )
    if report.missing_required:
        report.notes.append(
            "required fields not covered: add selectors or extract.defaults for "
            + ", ".join(report.missing_required)
        )
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(mapping.model_dump(exclude_defaults=True), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        report.written = True
        report.notes.append(f"next: gleanyard try {source.id}")
    return report


def _sample_page(project: Project, source: SourceConfig, client: httpx.Client | None) -> Response:
    fetcher = Fetcher(source, user_agent=project.config.user_agent, cache=None, client=client)
    first = fetcher.get(str(source.url))
    if source.delivery != Delivery.SITEMAP:
        return first
    urls = read_sitemap(source, first.body, source.reader_options)
    if not urls:
        raise ConfigError(f"{source.id}: the sitemap lists no pages to sample")
    return fetcher.get(urls[0])


def _query(schema: ProjectSchema) -> str:
    description = f" ({schema.description})" if schema.description else ""
    return (
        f"Extract every {schema.name} record{description} on the page. One schema entry per "
        "record, with fields named exactly as below; leave out fields the page does not show.\n"
        f"{field_guide(schema)}"
    )


def _example(schema: ProjectSchema) -> str:
    return json.dumps(
        {name: _EXAMPLE_VALUES.get(spec.type.value, "") for name, spec in schema.fields.items()}
    )
