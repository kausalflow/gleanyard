import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.errors import ConfigError
from gleanyard.init import init_project
from gleanyard.project import load_project

runner = CliRunner()


def test_init_creates_a_valid_project(tmp_path):
    root = tmp_path / "my-events"
    result = runner.invoke(app, ["init", str(root), "--schema", "event"])
    assert result.exit_code == 0, result.output
    for name in (
        "gleanyard.yaml",
        "schema.yaml",
        "CLAUDE.md",
        "research.md",
        "NOTICE",
        ".gitignore",
        ".github/workflows/collect.yml",
        ".github/workflows/validate.yml",
        ".github/workflows/repair.yml",
        "sources/.gitkeep",
        "mappings/.gitkeep",
        "candidates/.gitkeep",
        "tests/fixtures/.gitkeep",
    ):
        assert (root / name).exists(), name
    project = load_project(root)
    assert project.ok, project.issues
    assert project.schema.name == "event"
    assert "my-events" in (root / "README.md").read_text()
    text = "".join(p.read_text() for p in root.rglob("*") if p.is_file())
    assert "{{project}}" not in text and "{{schema}}" not in text
    assert runner.invoke(app, ["validate", str(root)]).exit_code == 0


def test_workflows_are_valid_yaml_with_the_expected_jobs(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "record")
    collect = yaml.safe_load((root / ".github/workflows/collect.yml").read_text())
    assert "schedule" in collect[True] and "workflow_dispatch" in collect[True]
    steps = [step.get("run", "") for step in collect["jobs"]["collect"]["steps"]]
    assert "gleanyard collect" in steps and "gleanyard export" in steps
    validate = yaml.safe_load((root / ".github/workflows/validate.yml").read_text())
    assert "pull_request" in validate[True]


def test_init_refuses_a_non_empty_directory_and_bad_names(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "file").write_text("hi")
    with pytest.raises(ConfigError, match="not empty"):
        init_project(tmp_path / "x", "x", "record")
    with pytest.raises(ConfigError, match="project name"):
        init_project(tmp_path / "y", "Bad Name", "record")
    (tmp_path / "git-only").mkdir()
    (tmp_path / "git-only" / ".git").mkdir()
    init_project(tmp_path / "git-only", "git-only", "record")


def test_template_ships_in_the_built_wheel(tmp_path):
    """Dotfiles must survive packaging, or `init` would write an incomplete project."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    out = tmp_path / "dist"
    repo = Path(__file__).parents[1]
    subprocess.run(
        [uv, "build", "--wheel", "-o", str(out)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    names = zipfile.ZipFile(next(out.glob("*.whl"))).namelist()
    for expected in (
        "dot-github/workflows/collect.yml",
        "dot-github/workflows/repair.yml",
        "dot-gitignore",
        "CLAUDE.md",
    ):
        assert any(name.endswith(f"template/{expected}") for name in names), expected
