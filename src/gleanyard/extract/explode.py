"""``explode``: one candidate record per element of a list inside an item.

A page or feed often groups items: a heading over a list of models, an event with a list of
dates. ``explode: groups.models`` turns one item into one per element of ``models`` in each
of ``groups``. Keys of the enclosing objects are copied into each new item, and a nested
key wins over an outer key of the same name. An item where the path is missing or empty
yields nothing.
"""

from __future__ import annotations

from typing import Any

from gleanyard.readers import Item


def explode_item(item: Item, path: str) -> list[Item]:
    return _explode(item, path.split("."))


def _explode(item: Item, parts: list[str]) -> list[Item]:
    key, rest = parts[0], parts[1:]
    value = item.get(key)
    outer = {name: other for name, other in item.items() if name != key}
    elements = value if isinstance(value, list) else [] if value is None else [value]
    items: list[Item] = []
    for element in elements:
        if rest:
            if isinstance(element, dict):
                items.extend(_explode({**outer, **element}, rest))
        elif isinstance(element, dict):
            items.append({**outer, **element})
        elif element not in (None, ""):
            items.append({**outer, key: element})
    return items


def explode_items(items: list[Any], path: str | None) -> list[Any]:
    if not path:
        return items
    return [new for item in items if isinstance(item, dict) for new in explode_item(item, path)]
