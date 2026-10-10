"""``jsonld`` mode: read schema.org data that a page publishes for search engines.

A page lists its entities in ``<script type="application/ld+json">`` blocks. Gleanyard
collects every node whose ``@type`` is one of the source's ``types`` (looking inside
arrays, ``@graph`` and nested objects) and maps each to a schema record. Fields not named
in ``extract.map`` are found by trying the schema field's name and then its ``aliases``,
so a schema that lists ``aliases: [startDate]`` on ``start_date`` needs no mapping at all.
"""

from __future__ import annotations

import json
from typing import Any

from gleanyard.extract.selectors import parse_html
from gleanyard.readers import Item
from gleanyard.schema import ProjectSchema


def jsonld_nodes(source_id: str, body: bytes, types: list[str]) -> list[Item]:
    """Every JSON-LD node on the page whose ``@type`` is in ``types``."""
    root = parse_html(source_id, body)
    wanted = {name.removeprefix("schema:") for name in types}
    nodes: list[Item] = []
    for script in root.xpath('//script[@type="application/ld+json"]'):
        try:
            document = json.loads(script.text_content())
        except ValueError:
            continue  # one broken block should not hide the others
        _collect(document, wanted, nodes)
    return nodes


def _collect(value: Any, wanted: set[str], found: list[Item]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect(item, wanted, found)
    elif isinstance(value, dict):
        kinds = value.get("@type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if any(str(kind).removeprefix("schema:").rsplit("/", 1)[-1] in wanted for kind in kinds):
            found.append(value)
        for child in value.values():
            if isinstance(child, list | dict):
                _collect(child, wanted, found)


def lookup(node: Any, path: str) -> Any:
    """Dot path through a JSON-LD node. A list is read at its first element unless a number
    is given, because ``offers`` and ``image`` may be one object or a list of them. ``*``
    reads the rest of the path in every element and returns a list."""
    return _plain(_follow(node, path.split(".")))


def _follow(current: Any, parts: list[str]) -> Any:
    for index, part in enumerate(parts):
        if part == "*" and isinstance(current, list):
            values = [_plain(_follow(element, parts[index + 1 :])) for element in current]
            return [value for value in values if value is not None] or None
        if isinstance(current, list):
            if part.isdigit():
                current = current[int(part)] if int(part) < len(current) else None
            else:
                current = current[0] if current else None
                current = current.get(part) if isinstance(current, dict) else None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _plain(value: Any) -> Any:
    """JSON-LD values are often wrapped: ``{"@value": ...}`` or an entity with a ``name``."""
    if isinstance(value, dict):
        if "@value" in value:
            return value["@value"]
        if "url" in value and len(value) <= 3:
            return value["url"]
        if "name" in value:
            return value["name"]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def map_node(
    node: Item, schema: ProjectSchema, mapping: dict[str, str], defaults: dict[str, Any]
) -> dict[str, Any]:
    values: dict[str, Any] = dict(defaults)
    for name, spec in schema.fields.items():
        paths = [mapping[name]] if name in mapping else [name, *spec.aliases]
        for path in paths:
            value = lookup(node, path)
            if value is not None and value != "" and value != []:
                values[name] = value
                break
    return values
