"""``fields`` mode: map feed keys straight onto schema fields, with no model."""

from __future__ import annotations

from typing import Any

from gleanyard.config import FieldsExtract
from gleanyard.readers import Item, lookup


def map_item(item: Item, extract: FieldsExtract) -> dict[str, Any]:
    values: dict[str, Any] = dict(extract.defaults)
    for field, path in extract.map.items():
        value = lookup(item, path)
        if value is not None and value != "" and value != []:
            values[field] = value
    return values
