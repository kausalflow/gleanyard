"""``fields`` mode: map feed keys straight onto schema fields, with no model."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from gleanyard.config import Case, FieldsExtract, Join
from gleanyard.readers import Item, lookup

# A join file's rows by key value, for each file of a source's ``join``.
Tables = dict[str, dict[str, dict[str, str]]]


def read_table(path: Path, join: Join) -> dict[str, dict[str, str]]:
    """A join file's rows by the value of its key column; raises if the column is missing."""
    column = join.column or join.on
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))
    if column not in (reader.fieldnames or []):
        raise ValueError(f"{join.file} has no column {column!r}")
    return {
        row[column]: {key: value for key, value in row.items() if key != column and value}
        for row in reader
    }


def join_tables(root: Path, extract: Any) -> Tables:
    if not isinstance(extract, FieldsExtract):
        return {}
    return {join.file: read_table(root / join.file, join) for join in extract.join}


def map_item(item: Item, extract: FieldsExtract, tables: Tables | None = None) -> dict | None:
    """The item's schema values, or None when it is filtered out or no case matches."""
    for path, pattern in extract.where.items():
        value = lookup(item, path)
        if value is None or not re.fullmatch(pattern, str(value)):
            return None
    for join in extract.join:
        value = lookup(item, join.on)
        row = (tables or {}).get(join.file, {}).get(str(value)) if value is not None else None
        if row is None:
            if join.unknown == "drop":
                return None
            continue
        item = {**item, **row}
    values: dict[str, Any] = dict(extract.defaults)
    for field, paths in extract.map.items():
        for path in [paths] if isinstance(paths, str) else paths:
            value = lookup(item, path)
            if value is not None and value != "" and value != []:
                values[field] = value
                break
    if extract.cases:
        case = next((case for case in extract.cases if _matches(item, case)), None)
        if case is None:
            return None
        values.update(case.set)
    for field, rewrite in extract.rewrite.items():
        if isinstance(values.get(field), str):
            values[field] = re.sub(rewrite.pattern, rewrite.replace, values[field])
    return values


def _matches(item: Item, case: Case) -> bool:
    for path, wanted in case.when.items():
        value = lookup(item, path)
        options = wanted if isinstance(wanted, list) else [wanted]
        if value is None or str(value) not in options:
            return False
    return True
