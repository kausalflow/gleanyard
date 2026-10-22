"""Cross-source merge: one exported row for an item that several sources publish.

A schema may name ``merge`` fields, such as ``[title, start_date, venue]``. At export, rows
from different sources that agree on all of them, after folding case and spacing, become one
row. Merging happens after each row went through its own source's rights filter, so a merged
row holds only fields that some contributing source may publish, and each value comes from
the first source, in source order, that exports that field. ``_source`` names that first
source and ``_sources`` lists every contributing one with its own record id. A row that lacks
a merge field is never merged.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from gleanyard.schema import ProjectSchema


def merge_key(row: dict[str, Any], fields: list[str]) -> tuple[str, ...] | None:
    if any(row.get(name) in (None, "", []) for name in fields):
        return None
    return tuple(_fold(row[name]) for name in fields)


def merge_rows(rows: list[dict[str, Any]], schema: ProjectSchema) -> list[dict[str, Any]]:
    """Join rows that agree on the schema's merge fields; keep the rest unchanged, in order."""
    if not schema.merge:
        return rows
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    order: list[tuple[str, ...] | int] = []
    for index, row in enumerate(rows):
        key = merge_key(row, schema.merge)
        if key is None:
            order.append(index)
        else:
            if key not in groups:
                order.append(key)
            groups.setdefault(key, []).append(row)
    merged = []
    for item in order:
        if isinstance(item, int):
            merged.append(rows[item])
            continue
        group = groups[item]
        merged.append(group[0] if len(group) == 1 else _join(item, group, schema))
    return merged


def _join(
    key: tuple[str, ...], group: list[dict[str, Any]], schema: ProjectSchema
) -> dict[str, Any]:
    digest = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
    row: dict[str, Any] = {"id": f"merged_{digest[:24]}"}
    for name in schema.fields:
        for member in group:
            if name in member:
                row[name] = member[name]
                break
    row["_source"] = group[0]["_source"]
    row["_sources"] = [{"id": member["id"], **member["_source"]} for member in group]
    return row


def _fold(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return json.dumps(value, ensure_ascii=False, sort_keys=True).casefold()
