"""Rights-filtered export.

Export is checked against the *current* source config, not the rights stored
with a record, so withdrawing a permission removes records from the next
export. A field is exported only when it is in the source's ``public_fields``
and its schema field class is allowed: facts need ``redistribute_facts``,
protected text needs ``redistribute_text``, images need
``redistribute_images``, and personal data is never exported. A record whose
required fields cannot all be exported is left out entirely.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from typing import Any

from gleanyard.collect import OutputRecord
from gleanyard.rights import RightsPolicy, SourceRights, export_blocker
from gleanyard.schema import FieldClass, ProjectSchema


def field_allowed(field_class: FieldClass, rights: SourceRights) -> bool:
    actions = rights.allowed_actions
    if field_class == FieldClass.FACT:
        return actions.redistribute_facts
    if field_class == FieldClass.PROTECTED_TEXT:
        return actions.redistribute_text
    if field_class == FieldClass.IMAGE:
        return actions.redistribute_images
    return False


def public_record(
    record: OutputRecord,
    schema: ProjectSchema,
    current_rights: dict[str, SourceRights],
    policy: RightsPolicy,
    today: date | None = None,
) -> dict[str, Any] | None:
    rights = current_rights.get(record.provenance.source_id)
    if export_blocker(rights, policy, today or date.today()) is not None:
        return None
    assert rights is not None
    values = {
        name: value
        for name, value in record.values.items()
        if name in schema.fields
        and name in rights.public_fields
        and field_allowed(schema.fields[name].field_class, rights)
    }
    if any(name not in values for name in schema.required_fields):
        return None
    source = {
        "source_id": record.provenance.source_id,
        "url": record.provenance.url,
        "retrieved_at": record.provenance.retrieved_at.isoformat(),
        "mode": record.provenance.mode,
    }
    if record.first_seen is not None:
        source["first_seen"] = record.first_seen.isoformat()
    if rights.attribution_text:
        source["attribution"] = rights.attribution_text
    if rights.license_id:
        source["license"] = rights.license_id
        source["license_url"] = rights.license_url
    return {"id": record.id, **values, "_source": source}


# Prefixed like ``_source`` in the JSON export, so they never clash with a schema field.
CSV_SOURCE_COLUMNS = ["_source", "_source_url", "_attribution", "_license"]


def csv_columns(schema: ProjectSchema) -> list[str]:
    fields = [n for n, spec in schema.fields.items() if spec.field_class != FieldClass.PERSONAL]
    return ["id", *fields, *CSV_SOURCE_COLUMNS]


def csv_text(rows: list[dict[str, Any]], schema: ProjectSchema) -> str:
    """The public export as CSV: one column per schema field, list values joined by "; "."""
    columns = csv_columns(schema)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        source = row.get("_source", {})
        flat = {
            **row,
            "_source": source.get("source_id"),
            "_source_url": source.get("url"),
            "_attribution": source.get("attribution"),
            "_license": source.get("license"),
        }
        writer.writerow([_cell(flat.get(column)) for column in columns])
    return buffer.getvalue()


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "; ".join(_cell(element) for element in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
