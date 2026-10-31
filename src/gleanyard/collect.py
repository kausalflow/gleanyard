"""Turn fetched responses into schema-valid records for one source.

Every record carries provenance: the source, the page it came from, when it
was retrieved, the body's hash in the raw cache and the extraction mode that
produced it. Records that fail the schema are returned as rejections for the
review file, never mixed into the output.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from gleanyard.config import (
    FieldsExtract,
    JsonLdExtract,
    LlmExtract,
    SelectorsExtract,
    SourceConfig,
)
from gleanyard.errors import ModelError
from gleanyard.extract.explode import explode_items
from gleanyard.extract.fields import Tables, map_item
from gleanyard.extract.jsonld import jsonld_nodes, map_node
from gleanyard.extract.llm import LlmSession
from gleanyard.extract.selectors import SelectorSchema, run_selectors
from gleanyard.fetch import Response
from gleanyard.readers import read_items
from gleanyard.schema import FieldIssue, ProjectSchema


class Provenance(BaseModel):
    source_id: str
    url: str
    retrieved_at: datetime
    raw_sha256: str
    mode: str
    schema_version: int


class OutputRecord(BaseModel):
    id: str
    values: dict[str, Any]
    provenance: Provenance
    warnings: list[FieldIssue] = Field(default_factory=list)
    first_seen: datetime | None = Field(
        default=None, description="When a record with this id was first collected."
    )


class Rejection(BaseModel):
    source_id: str
    url: str
    raw_sha256: str
    values: dict[str, Any]
    errors: list[FieldIssue]


class Extraction(BaseModel):
    records: list[OutputRecord] = Field(default_factory=list)
    rejections: list[Rejection] = Field(default_factory=list)

    @property
    def field_fill_rates(self) -> dict[str, float]:
        if not self.records:
            return {}
        counts: dict[str, int] = {}
        for record in self.records:
            for name in record.values:
                counts[name] = counts.get(name, 0) + 1
        return {name: count / len(self.records) for name, count in sorted(counts.items())}


def mapped_values(
    source: SourceConfig,
    schema: ProjectSchema,
    response: Response,
    today: date | None,
    selectors: SelectorSchema | None,
    llm: LlmSession | None = None,
    tables: Tables | None = None,
) -> list[dict[str, Any]]:
    """Run the source's pinned mode over one response; one dict per candidate record."""
    mode = source.extract
    if isinstance(mode, FieldsExtract):
        items = explode_items(read_items(source, response.body, today=today), mode.explode)
        mapped = (map_item(item, mode, tables) for item in items)
        return [values for values in mapped if values is not None]
    if isinstance(mode, JsonLdExtract):
        nodes = jsonld_nodes(source.id, response.body, mode.types)
        return [map_node(node, schema, mode.map, mode.defaults) for node in nodes]
    if isinstance(mode, SelectorsExtract):
        if selectors is None:
            raise ValueError(f"{source.id}: the selector mapping was not loaded")
        items = explode_items(run_selectors(source.id, response.body, selectors), mode.explode)
        return [{**mode.defaults, **item} for item in items]
    if isinstance(mode, LlmExtract):
        if llm is None:
            raise ModelError(source.id, "no model session available")
        items = llm.items(source, schema, response)
        return [{**mode.defaults, **_present(item)} for item in items]
    raise NotImplementedError(f"extract mode {mode.mode!r} is not implemented yet")


def _present(item: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if value not in (None, "", [])}


def extract(
    source: SourceConfig,
    schema: ProjectSchema,
    response: Response,
    today: date | None = None,
    selectors: SelectorSchema | None = None,
    llm: LlmSession | None = None,
    base_url: str | None = None,
    tables: Tables | None = None,
) -> Extraction:
    """Extract one response with the source's pinned mode."""
    result = Extraction()
    base_url = base_url or str(source.homepage or source.url)
    seen: set[str] = set()
    for values in mapped_values(source, schema, response, today, selectors, llm, tables):
        validation = schema.validate_record(
            values, source_id=source.id, base_url=base_url, today=today
        )
        if validation.record is None:
            result.rejections.append(
                Rejection(
                    source_id=source.id,
                    url=response.url,
                    raw_sha256=response.sha256,
                    values=_jsonable(values),
                    errors=validation.errors,
                )
            )
            continue
        if validation.record.id in seen:
            continue
        seen.add(validation.record.id)
        result.records.append(
            OutputRecord(
                id=validation.record.id,
                values=validation.record.values,
                provenance=Provenance(
                    source_id=source.id,
                    url=response.url,
                    retrieved_at=response.retrieved_at,
                    raw_sha256=response.sha256,
                    mode=source.extract.mode,
                    schema_version=schema.version,
                ),
                warnings=validation.warnings,
            )
        )
    return result


def _jsonable(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in values.items()
    }
