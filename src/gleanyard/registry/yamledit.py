"""Small edits to a source file that keep its comments.

A source file's comments often explain the source (why a URL has those parameters, what the
feed leaves out), so copying one between a project and a registry edits the lines it must
change and leaves the rest as written. Every edit is checked by parsing the result: if the
text edit does not give exactly the intended document (flow-style mappings, odd layouts), the
file is written out from the parsed document instead, without comments.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True)
class Edit:
    """Set ``path`` (one or two keys deep) to ``value``, or drop it when ``drop`` is set."""

    path: tuple[str, ...]
    value: Any = None
    drop: bool = False


def edit_yaml(text: str, edits: list[Edit]) -> str:
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ValueError("expected a YAML mapping at the top level")
    expected = copy.deepcopy(document)
    for item in edits:
        _apply(expected, item)
    lines = text.splitlines()
    try:
        for item in edits:
            lines = _edit_lines(lines, item)
        result = "\n".join(lines).rstrip("\n") + "\n"
        if yaml.safe_load(result) == expected:
            return result
    except (ValueError, yaml.YAMLError):
        pass
    return yaml.safe_dump(expected, sort_keys=False, allow_unicode=True)


def _apply(document: dict[str, Any], item: Edit) -> None:
    *parents, key = item.path
    target = document
    for parent in parents:
        if not isinstance(target.get(parent), dict):
            if item.drop:
                return
            target[parent] = {}
        target = target[parent]
    if item.drop:
        target.pop(key, None)
    else:
        target[key] = item.value


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10_000)
    return text.removesuffix("\n").removesuffix("\n...").strip()


def _key_line(key: str, indent: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(indent)}{re.escape(key)}:(\s|$)")


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """The line after the block that starts at ``start``: the next line, not blank, that is
    indented no deeper than ``indent``."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        end += 1
    # Blank lines at the end of a block separate it from the next one; keep them outside.
    while end - 1 > start and not lines[end - 1].strip():
        end -= 1
    return end


def _find(lines: list[str], key: str, indent: str, lo: int, hi: int) -> int | None:
    pattern = _key_line(key, indent)
    return next((index for index in range(lo, hi) if pattern.match(lines[index])), None)


def _render(key: str, value: Any, indent: str) -> list[str]:
    if isinstance(value, dict | list):
        block = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True)
        return [indent + line for line in block.rstrip("\n").splitlines()]
    return [f"{indent}{key}: {_scalar(value)}"]


def _edit_lines(lines: list[str], item: Edit) -> list[str]:
    if len(item.path) == 1:
        (key,) = item.path
        at = _find(lines, key, "", 0, len(lines))
        if at is not None:
            end = _block_end(lines, at, 0)
            return lines[:at] + ([] if item.drop else _render(key, item.value, "")) + lines[end:]
        if item.drop:
            return lines
        return [*lines, *_render(key, item.value, "")]
    if len(item.path) != 2:
        raise ValueError("edits reach two keys deep at most")
    parent, key = item.path
    head = _find(lines, parent, "", 0, len(lines))
    if head is None:
        if item.drop:
            return lines
        return [*lines, *_render(parent, {key: item.value}, "")]
    if lines[head].split(":", 1)[1].split("#", 1)[0].strip():
        raise ValueError(f"{parent} is written inline")
    end = _block_end(lines, head, 0)
    children = [
        line for line in lines[head + 1 : end] if line.strip() and not line.lstrip().startswith("#")
    ]
    indent = children[0][: len(children[0]) - len(children[0].lstrip())] if children else "  "
    at = _find(lines, key, indent, head + 1, end)
    if at is not None:
        stop = _block_end(lines, at, len(indent))
        stop = min(stop, end)
        replacement = [] if item.drop else _render(key, item.value, indent)
        return lines[:at] + replacement + lines[stop:]
    if item.drop:
        return lines
    return lines[: head + 1] + _render(key, item.value, indent) + lines[head + 1 :]
