"""``fields`` mode: map feed keys straight onto schema fields, with no model."""

from __future__ import annotations

import re
from typing import Any

from gleanyard.config import Case, FieldsExtract
from gleanyard.readers import Item, lookup


def map_item(item: Item, extract: FieldsExtract) -> dict[str, Any] | None:
    """The item's schema values, or None when ``cases`` are set and none matches."""
    values: dict[str, Any] = dict(extract.defaults)
    for field, path in extract.map.items():
        value = lookup(item, path)
        if value is not None and value != "" and value != []:
            values[field] = value
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
