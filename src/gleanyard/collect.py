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

from gleanyard.config import FieldsExtract, SourceConfig
from gleanyard.extract.fields import map_item
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


def extract(
    source: SourceConfig,
    schema: ProjectSchema,
    response: Response,
    today: date | None = None,
) -> Extraction:
    """Extract one response with the source's pinned mode."""
    if not isinstance(source.extract, FieldsExtract):
        raise NotImplementedError(f"extract mode {source.extract.mode!r} is not implemented yet")
    result = Extraction()
    base_url = str(source.homepage or source.url)
    seen: set[str] = set()
    for item in read_items(source, response.body, today=today):
        values = map_item(item, source.extract)
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
