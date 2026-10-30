"""The Claude Code plugin ships from this repo: it must match the package it starts."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "gleanyard"


def test_marketplace_lists_the_plugin():
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    [entry] = marketplace["plugins"]
    assert entry["name"] == "gleanyard"
    assert (ROOT / entry["source"]).resolve() == PLUGIN.resolve()


def test_plugin_version_follows_the_package():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == package


def test_plugin_starts_the_mcp_server_with_writes():
    server = json.loads((PLUGIN / ".mcp.json").read_text())["mcpServers"]["gleanyard"]
    assert server["command"] == "uvx"
    assert server["args"][-3:] == ["gleanyard", "mcp", "--allow-writes"]
    assert server["args"][1].startswith("gleanyard[mcp]")


def test_plugin_skill_defers_to_the_project_skill():
    text = (PLUGIN / "skills" / "gleanyard" / "SKILL.md").read_text()
    assert text.startswith("---\nname: gleanyard\n")
    assert ".claude/skills/gleanyard/SKILL.md" in text and "CLAUDE.md" in text
