from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.init import init_project
from gleanyard.skill import SKILL_PATH, checklist, procedures, skill_state, skill_text

runner = CliRunner()


def test_claude_md_imports_the_skill_and_the_skill_holds_the_procedures(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "event")
    claude = (root / "CLAUDE.md").read_text()
    assert f"@{SKILL_PATH.as_posix()}" in claude
    assert "## Ground rules" in claude and "## Procedure:" not in claude
    assert (root / SKILL_PATH).read_text() == skill_text()
    assert skill_text().startswith("---\nname: gleanyard\n")
    assert set(procedures()) == {"research", "set up a source", "repair", "retire a source"}
    assert "gleanyard repair check" in procedures()["repair"]
    assert "Pull request checklist" not in procedures()["retire a source"]
    assert checklist().startswith("## Pull request checklist")


def test_skill_command_reports_and_refreshes(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "event")
    result = runner.invoke(app, ["skill", str(root), "--check"])
    assert result.exit_code == 0 and "current" in result.output

    (root / SKILL_PATH).write_text("old procedures")
    assert skill_state(root).state == "outdated"
    assert runner.invoke(app, ["skill", str(root), "--check"]).exit_code == 1
    assert (root / SKILL_PATH).read_text() == "old procedures"

    result = runner.invoke(app, ["skill", str(root), "--json"])
    assert result.exit_code == 0 and '"written": true' in result.output
    assert skill_state(root).state == "current"

    (root / SKILL_PATH).unlink()
    assert skill_state(root).state == "missing"
    assert runner.invoke(app, ["skill", str(root)]).exit_code == 0
    assert skill_state(root).state == "current"
