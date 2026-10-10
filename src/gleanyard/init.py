"""``gleanyard init``: create a collection project from the bundled template.

The template is the repo layout, the starter schema, the scheduled workflows and the
``CLAUDE.md`` that tells an AI assistant how to research, set up and repair sources. Files
named ``dot-*`` are written with a leading dot, so they survive packaging.
"""

from __future__ import annotations

import re
from pathlib import Path

from gleanyard.errors import ConfigError

TEMPLATE = Path(__file__).parent / "template"
EMPTY_DIRS = ("sources", "mappings", "candidates", "evidence", "tests/fixtures")
NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def init_project(root: Path, project: str, schema: str) -> list[Path]:
    """Write the template into ``root`` (created if needed) and return the files written."""
    if not NAME.match(project):
        raise ConfigError(f"project name {project!r} must be lowercase letters, digits and dashes")
    if not NAME.match(schema):
        raise ConfigError(f"schema name {schema!r} must be lowercase letters, digits and dashes")
    root.mkdir(parents=True, exist_ok=True)
    existing = [path.name for path in root.iterdir() if path.name != ".git"]
    if existing:
        raise ConfigError(f"{root} is not empty ({', '.join(sorted(existing)[:3])}...)")
    written: list[Path] = []
    for source in sorted(TEMPLATE.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(TEMPLATE)
        target = root.joinpath(*(_undot(part) for part in relative.parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        target.write_text(
            text.replace("{{project}}", project).replace("{{schema}}", schema), encoding="utf-8"
        )
        written.append(target)
    for name in EMPTY_DIRS:
        keep = root / name / ".gitkeep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.touch()
        written.append(keep)
    return written


def _undot(part: str) -> str:
    return "." + part.removeprefix("dot-") if part.startswith("dot-") else part
