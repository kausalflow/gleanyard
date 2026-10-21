"""``gleanyard skill``: the assistant's procedures, versioned with the package.

The procedures for research, setup, repair and retirement live in one skill file that ships
in the repo template. A project keeps a copy at ``.claude/skills/gleanyard/SKILL.md``, which
its ``CLAUDE.md`` imports, so upgrading gleanyard and running ``gleanyard skill`` brings the
procedures in line with the commands the new version has. The MCP server serves the same text.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from gleanyard.init import TEMPLATE

SKILL_PATH = Path(".claude/skills/gleanyard/SKILL.md")
PROCEDURE = re.compile(r"^## Procedure: (.+)$", re.MULTILINE)


class SkillState(BaseModel):
    path: str
    state: str  # current, outdated or missing


def skill_text() -> str:
    """The skill file of the installed gleanyard."""
    return (TEMPLATE / "dot-claude" / "skills" / "gleanyard" / "SKILL.md").read_text(
        encoding="utf-8"
    )


def skill_state(root: Path) -> SkillState:
    path = root / SKILL_PATH
    if not path.is_file():
        state = "missing"
    else:
        state = "current" if path.read_text(encoding="utf-8") == skill_text() else "outdated"
    return SkillState(path=str(path), state=state)


def write_skill(root: Path) -> Path:
    path = root / SKILL_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill_text(), encoding="utf-8")
    return path


def procedures() -> dict[str, str]:
    """Each ``## Procedure: <name>`` section of the skill, keyed by its lowercased name."""
    text = skill_text()
    heads = list(PROCEDURE.finditer(text))
    sections = {}
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        body = text[head.start() : end]
        # The checklist after the last procedure belongs to every procedure, not to one.
        body = body.split("\n## Pull request checklist", 1)[0]
        sections[head.group(1).strip().lower()] = body.strip() + "\n"
    return sections


def checklist() -> str:
    text = skill_text()
    _, _, tail = text.partition("## Pull request checklist")
    return ("## Pull request checklist" + tail).strip() + "\n" if tail else ""
