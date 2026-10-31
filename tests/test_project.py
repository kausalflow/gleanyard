import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.project import load_project

EXAMPLE = Path(__file__).parents[1] / "examples" / "events"
runner = CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(EXAMPLE, tmp_path / "events")
    return tmp_path / "events"


def write_source(project: Path, name: str, text: str) -> None:
    (project / "sources" / f"{name}.yaml").write_text(text, encoding="utf-8")


def issues(project: Path) -> list[str]:
    return [str(issue) for issue in load_project(project).issues]


def test_example_project_is_valid():
    project = load_project(EXAMPLE)
    assert project.ok, project.issues
    assert [source.id for source in project.sources] == [
        "aachen-city-events",
        "gut-hixholz-farm-events",
    ]


def test_unknown_key_names_file_and_line(project):
    path = project / "sources" / "aachen-city-events.yaml"
    path.write_text(path.read_text().replace("enabled: false", "enabld: false"))
    assert issues(project) == [
        "sources/aachen-city-events.yaml:4: enabld: unknown key; remove it or check the spelling"
    ]


def test_mode_must_fit_delivery(project):
    path = project / "sources" / "aachen-city-events.yaml"
    path.write_text(path.read_text().replace("mode: fields", "mode: llm"))
    found = issues(project)
    assert any("extract.llm: required key is missing" in issue for issue in found), found


def test_fields_mode_rejected_for_html(project):
    path = project / "sources" / "gut-hixholz-farm-events.yaml"
    text = path.read_text().replace(
        "mode: selectors                        # pinned at setup; changed only by a repair PR\n"
        "  selectors: mappings/gut-hixholz-farm-events.json",
        "mode: fields\n  map: {title: h1}",
    )
    path.write_text(text)
    assert any("does not fit delivery 'html'" in issue for issue in issues(project))


def test_cross_checks(project):
    write_source(
        project,
        "wrong-name",
        """
id: other-id
schema: venue
delivery: json
url: https://example.org/events.json
reader: {items_path: data}
extract:
  mode: fields
  map: {title: name, colour: c}
  defaults: {mood: happy}
enabled: true
rights: {public_fields: [secret]}
""",
    )
    found = "\n".join(issues(project))
    assert "id 'other-id' must match the file name 'wrong-name'" in found
    assert "schema is 'venue' but the project schema is 'event'" in found
    assert "extract.map.colour: 'colour' is not a field" in found
    assert "extract.defaults.mood" in found
    assert "required fields not mapped: start_date, source_url" in found
    assert "an enabled source needs" not in found
    assert "rights.public_fields: unknown fields: secret" in found


def test_strict_policy_needs_reviewed_actions(project):
    (project / "gleanyard.yaml").write_text(
        "rights_policy: {require_reviewed_permission: true}\n", encoding="utf-8"
    )
    path = project / "sources" / "aachen-city-events.yaml"
    text = path.read_text().replace("enabled: false", "enabled: true")
    path.write_text(text.replace("private_store: true", "private_store: false"))
    assert any("an enabled source needs" in issue for issue in issues(project))
    (project / "gleanyard.yaml").unlink()
    assert not any("an enabled source needs" in issue for issue in issues(project))


def test_reader_options_are_strict(project):
    write_source(
        project,
        "feed",
        """
id: feed
schema: event
delivery: json
url: https://example.org/events.json
reader: {item_path: data}
extract: {mode: fields, map: {title: t, start_date: d, source_url: u}}
rights: {}
""",
    )
    assert any("item_path" in issue for issue in issues(project))


def test_missing_selector_file(project):
    (project / "mappings" / "gut-hixholz-farm-events.json").unlink()
    assert any("selector file" in issue for issue in issues(project))


def test_invalid_yaml_reports_line(project):
    write_source(project, "broken", "id: broken\nschema: [event\n")
    found = issues(project)
    assert found[0].startswith("sources/broken.yaml:")
    assert "invalid YAML" in found[0]


def test_cli_validate_json(project):
    result = runner.invoke(app, ["validate", str(project), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True

    write_source(project, "bad", "id: bad\n")
    result = runner.invoke(app, ["validate", str(project), "--json"])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["ok"] is False
    assert {issue["path"] for issue in report["issues"]} >= {"schema", "delivery", "url"}


def test_cli_json_schema():
    result = runner.invoke(app, ["json-schema", "source"])
    assert result.exit_code == 0
    schema = json.loads(result.output)
    assert "delivery" in schema["properties"]
    assert "schema" in schema["required"]
