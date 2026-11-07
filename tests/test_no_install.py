"""The pieces for using gleanyard with nothing installed: the Action, the devcontainer and the
read-only connector. They are not Python, but they must follow the package version."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from gleanyard import __version__
from gleanyard.init import init_project

ROOT = Path(__file__).parents[1]
MINOR = ".".join(__version__.split(".")[:2]) + ".0"


def test_action_installs_gleanyard_and_runs_each_command():
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {
        "run",
        "working-directory",
        "version",
        "extras",
        "python-version",
    }
    install, run = action["runs"]["steps"][1:]
    assert "file://${ACTION_PATH}" in install["run"]
    assert "gleanyard $line" in run["run"] and "set -f" in run["run"]
    # inputs reach the scripts through env, never pasted into the script text
    for step in (install, run):
        assert "${{ inputs." not in step["run"]


def test_devcontainer_pins_this_minor_version(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "record")
    config = json.loads((root / ".devcontainer" / "devcontainer.json").read_text())
    assert config["name"] == "p"
    assert re.findall(r"gleanyard\[mcp\]~=([\d.]+)", config["postCreateCommand"]) == [MINOR]
    assert any("claude-code" in feature for feature in config["features"])


def test_connector_version_follows_the_package():
    text = (ROOT / "connector" / "worker.js").read_text()
    assert f'const VERSION = "{__version__}";' in text


def test_connector_fetches_only_the_registry_and_raw_github():
    text = (ROOT / "connector" / "worker.js").read_text()
    hosts = set(re.findall(r"https://([a-z0-9.-]+)/", text))
    assert hosts <= {
        "kausalflow.github.io",
        "raw.githubusercontent.com",
        "github.com",
        "claude.ai",
    }, hosts


def test_connector_offline_tests_pass():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, str(ROOT / "connector" / "test.mjs")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr + result.stdout
