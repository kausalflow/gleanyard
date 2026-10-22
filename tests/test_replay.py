import json
import shutil
from datetime import date
from pathlib import Path

import yaml
from test_second_project import EXAMPLE, build_project
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.project import load_project
from gleanyard.replay import compare, replay_project, replay_source

runner = CliRunner()
TODAY = date(2026, 10, 21)


def project_with_fixtures(tmp_path: Path):
    root = build_project(tmp_path)
    shutil.copytree(
        EXAMPLE / "tests" / "fixtures",
        root / "tests" / "fixtures",
        ignore=shutil.ignore_patterns("*.expected.json"),
        dirs_exist_ok=True,
    )
    return root


def test_the_example_snapshots_are_current():
    project = load_project(EXAMPLE)
    report = replay_project(project)
    assert [item.verdict for item in report.results] == ["pass"] * 3, report.markdown()


def test_without_a_snapshot_the_source_is_skipped(tmp_path):
    project = load_project(project_with_fixtures(tmp_path))
    result = replay_source(project, project.sources[0])
    assert result.verdict == "skip" and "--update" in result.reasons[0]


def test_update_writes_a_snapshot_that_later_replays_match(tmp_path):
    root = project_with_fixtures(tmp_path)
    project = load_project(root)
    written = replay_project(project, update=True, today=TODAY)
    assert [item.verdict for item in written.results] == ["pass"] * 3
    snapshot = json.loads((root / "tests/fixtures/portal-api.expected.json").read_text())
    assert snapshot["today"] == "2026-10-21" and snapshot["records"]
    again = replay_project(load_project(root))
    assert again.passed
    assert all("match the snapshot" in item.reasons[0] for item in again.results)


def test_a_mapping_change_shows_as_a_difference(tmp_path):
    root = project_with_fixtures(tmp_path)
    replay_project(load_project(root), update=True, today=TODAY)
    path = root / "sources" / "portal-api.yaml"
    data = yaml.safe_load(path.read_text())
    del data["extract"]["map"]["license"]
    path.write_text(yaml.safe_dump(data))
    project = load_project(root)
    result = replay_source(project, next(s for s in project.sources if s.id == "portal-api"))
    assert result.verdict == "fail"
    assert any("license was" in reason and "now absent" in reason for reason in result.reasons)


def test_a_source_without_a_fixture_is_skipped(tmp_path):
    root = build_project(tmp_path)
    project = load_project(root)
    result = replay_source(project, project.sources[0])
    assert result.verdict == "skip" and "no fixture" in result.reasons[0]


def test_compare_counts_missing_new_and_changed():
    before = {"a": {"title": "A", "size": 1}, "b": {"title": "B"}}
    after = {"a": {"title": "A", "size": 2}, "c": {"title": "C"}}
    problems = compare(before, after, ["title"])
    assert problems == [
        "1 record(s) missing: B",
        "1 field(s) changed: A: size was 1, now 2",
        "1 record(s) new: C",
    ]


def test_cli_test_exits_1_on_a_difference(tmp_path):
    root = project_with_fixtures(tmp_path)
    assert runner.invoke(app, ["test", str(root), "--update"]).exit_code == 0
    fixture = root / "tests/fixtures/city-feed.rss"
    fixture.write_text(fixture.read_text().replace("Synthetic tree register", "Tree register"))
    result = runner.invoke(app, ["test", str(root), "--json"])
    assert result.exit_code == 1
    rows = {row["source_id"]: row for row in json.loads(result.stdout)}
    assert rows["portal-api"]["verdict"] == "pass"
    assert rows["city-feed"]["reasons"] == [
        "1 record(s) missing: Synthetic tree register / Synthetic City",
        "1 record(s) new: Tree register / Synthetic City",
    ]


def test_update_judges_the_fixture_as_of_the_given_today(tmp_path):
    root = project_with_fixtures(tmp_path)
    path = root / "schema.yaml"
    schema = yaml.safe_load(path.read_text())
    schema["fields"]["modified"]["scope"] = {"max": "today"}  # fixture dates: 2026-08/09
    path.write_text(yaml.safe_dump(schema))
    # A page saved now, judged as of 2000, has every record out of scope.
    late = ["test", str(root), "-s", "portal-api", "--update", "--today", "2000-01-01"]
    assert runner.invoke(app, late).exit_code == 1
    early = ["test", str(root), "-s", "portal-api", "--update", "--today", "2026-10-01"]
    assert runner.invoke(app, early).exit_code == 0
    snapshot = json.loads((root / "tests/fixtures/portal-api.expected.json").read_text())
    assert snapshot["today"] == "2026-10-01"
    assert runner.invoke(app, ["test", str(root), "-s", "portal-api"]).exit_code == 0


def test_today_without_update_is_refused(tmp_path):
    root = project_with_fixtures(tmp_path)
    result = runner.invoke(app, ["test", str(root), "--today", "2026-01-01"])
    assert result.exit_code == 2
