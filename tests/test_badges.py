from xml.etree import ElementTree

from test_terms import write_project
from typer.testing import CliRunner

from gleanyard.badges import project_badge, render, source_badge
from gleanyard.cli import app
from gleanyard.status import ProjectStatus, SourceStatus
from gleanyard.terms import TermsState

runner = CliRunner()
CHECKED = "2026-10-21T05:00:00Z"


def report(*entries):
    return ProjectStatus(
        generated_at=CHECKED, schema_name="event", healthy=False, sources=list(entries)
    )


def test_project_badge_names_the_worst_health_first():
    status = report(
        SourceStatus(id="a", enabled=True, health="ok", checked_at=CHECKED),
        SourceStatus(id="b", enabled=True, health="failing", checked_at=CHECKED),
        SourceStatus(id="c", enabled=False, health="disabled"),
    )
    svg = project_badge(status)
    assert "collection: 1 failing, 1 ok" in svg and "#cf222e" in svg
    ElementTree.fromstring(svg)


def test_source_badge_shows_terms_changes_and_new_sources():
    terms = TermsState(changed=["https://site.example/impressum"])
    changed = SourceStatus(id="a", enabled=True, health="ok", checked_at=CHECKED, terms=terms)
    assert "a: terms changed" in source_badge(changed)
    fresh = SourceStatus(id="b", enabled=True, health="ok")
    assert "b: not collected yet" in source_badge(fresh)


def test_render_escapes_text():
    assert "&lt;x&gt;" in render("<x>", "ok", "#000")


def test_cli_writes_badges_and_removes_stale_ones(tmp_path):
    root = write_project(tmp_path / "p")
    stale = root / "badges" / "sources" / "gone.svg"
    stale.parent.mkdir(parents=True)
    stale.write_text("<svg/>")
    result = runner.invoke(app, ["badge", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "badges" / "project.svg").is_file()
    assert "s: not collected yet" in (root / "badges" / "sources" / "s.svg").read_text()
    assert not stale.exists()
