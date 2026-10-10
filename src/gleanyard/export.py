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
    if rights.attribution_text:
        source["attribution"] = rights.attribution_text
    if rights.license_id:
        source["license"] = rights.license_id
        source["license_url"] = rights.license_url
    return {"id": record.id, **values, "_source": source}
