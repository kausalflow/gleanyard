import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from test_html import FARM_MAPPING, FIXTURES, TODAY, make_project, site
from typer.testing import CliRunner

from gleanyard import __version__
from gleanyard.cli import app
from gleanyard.init import init_project
from gleanyard.project import load_project
from gleanyard.repair import check_repair, compare_with_baseline, plan_repair
from gleanyard.status import Check, ProjectStatus, SourceStatus
from gleanyard.tryout import TryReport

runner = CliRunner()
FARM = (FIXTURES / "farm.html").read_bytes()
CLEAN = b"\n".join(
    line for line in FARM.splitlines() if b"<th>" not in line and b"nicht bekannt" not in line
)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A committed project whose selector mapping no longer matches the page."""
    broken = {**FARM_MAPPING, "baseSelector": "table.old tr"}
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "html",
            "url": "https://farm.example/events",
            "extract": {"mode": "selectors", "selectors": "mappings/m.json"},
        },
        broken,
    )
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return root


def check(root: Path, page: bytes = CLEAN):
    return check_repair(root, client=site({"/events": page}), today=TODAY)


def fix_mapping(root: Path) -> None:
    (root / "mappings" / "m.json").write_text(json.dumps(FARM_MAPPING))


def edit_source(root: Path, **changes) -> None:
    path = root / "sources" / "s.yaml"
    data = yaml.safe_load(path.read_text())
    data.update(changes)
    path.write_text(yaml.safe_dump(data))


def write_status(root: Path, *entries: SourceStatus) -> None:
    report = ProjectStatus(
        generated_at="2026-10-10T05:00:00Z", schema_name="event", healthy=False, sources=entries
    )
    (root / "status.json").write_text(report.model_dump_json())


def test_a_mapping_fix_that_passes_try_is_ready(repo):
    fix_mapping(repo)
    result = check(repo)
    assert result.ready, result.markdown()
    assert result.files == ["mappings/m.json"]
    assert [item.id for item in result.sources] == ["s"]
    assert "ready for review" in result.markdown()


def test_a_fix_that_still_fails_try_is_only_a_draft(repo):
    fix_mapping(repo)
    result = check(repo, FARM)  # the noisy page: header and bad-date rows are rejected
    assert result.proposable and not result.ready
    assert any("rejected" in problem for problem in result.sources[0].problems)


def test_nothing_changed_is_not_proposable(repo):
    result = check(repo)
    assert not result.proposable and result.problems == ["nothing changed"]


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://other.example/events"},
        {"enabled": False},
        {"fetch": {"delay_seconds": 0, "max_requests": 50}},
        {"rights": {"review_status": "pending"}},
    ],
)
def test_protected_source_keys_stop_the_proposal(repo, changes):
    fix_mapping(repo)
    edit_source(repo, **changes)
    result = check(repo)
    assert not result.proposable
    assert any(f"`{next(iter(changes))}` changed" in problem for problem in result.problems)
    assert result.sources == []  # nothing is fetched for a change that cannot be proposed


def test_switching_to_llm_is_refused_but_other_switches_are_noted(repo):
    edit_source(repo, extract={"mode": "llm", "llm": {"model": "x"}})
    assert any("llm" in problem for problem in check(repo).problems)
    edit_source(repo, extract={"mode": "jsonld", "types": ["Event"]})
    result = check(repo)
    assert not result.problems
    assert any("from `selectors` to `jsonld`" in note for note in result.notes)


def test_files_outside_the_repair_area_stop_the_proposal(repo):
    fix_mapping(repo)
    (repo / "schema.yaml").write_text((repo / "schema.yaml").read_text() + "\n# edited\n")
    (repo / "sources" / "new.yaml").write_text("id: new\n")
    (repo / "mappings" / "m.json").unlink()
    problems = " ".join(check(repo).problems)
    assert "schema.yaml: a repair may change" in problems
    assert "sources/new.yaml: a new source" in problems
    assert "mappings/m.json: deleting" in problems


def test_generated_files_are_left_out(repo):
    (repo / "data" / "records").mkdir(parents=True)
    (repo / "data" / "records" / "s.jsonl").write_text("{}\n")
    (repo / "evidence").mkdir()
    (repo / "evidence" / "note.yaml").write_text("why: page moved\n")
    write_status(repo)
    result = check(repo)
    assert result.ignored == ["data/records/s.jsonl", "status.json"]
    assert result.files == ["evidence/note.yaml"]
    assert result.proposable and not result.ready  # no source changed, so nothing was tried


def test_baseline_comparison_uses_the_degraded_rules():
    entry = SourceStatus(
        id="s",
        enabled=True,
        health="failing",
        records=40,
        baseline_fill_rates={"title": 1.0, "venue": 0.9, "time_text": 0.3},
    )
    report = TryReport(
        source_id="s",
        verdict="pass",
        mode="selectors",
        records=20,
        field_fill_rates={"title": 1.0, "venue": 0.4, "time_text": 0.0},
    )
    problems = compare_with_baseline(report, entry)
    assert problems == [
        "20 records, the last good run had 40",
        "field venue fills 40%, the last good run 90%",
    ]
    report.records, report.field_fill_rates["venue"] = 35, 0.8
    assert compare_with_baseline(report, entry) == []


def test_plan_picks_what_an_edit_can_fix(repo):
    base = yaml.safe_load((repo / "sources" / "s.yaml").read_text())
    for name in ("blocked", "flaky", "fine", "worse", "off"):
        source = {**base, "id": name, "enabled": name != "off"}
        (repo / "sources" / f"{name}.yaml").write_text(yaml.safe_dump(source))
    write_status(
        repo,
        SourceStatus(id="s", enabled=True, health="failing", error_kind="structure_changed"),
        SourceStatus(id="blocked", enabled=True, health="failing", error_kind="blocked"),
        SourceStatus(id="flaky", enabled=True, health="failing", error_kind="transport"),
        SourceStatus(id="fine", enabled=True, health="ok"),
        SourceStatus(
            id="worse",
            enabled=True,
            health="degraded",
            warnings=[Check(kind="record_drop", message="fewer")],
            note="fewer",
        ),
        SourceStatus(id="off", enabled=False, health="failing", error_kind="empty"),
    )
    plan = plan_repair(load_project(repo))
    assert [(t.id, t.reasons) for t in plan.targets] == [
        ("s", ["structure_changed"]),
        ("worse", ["record_drop"]),
    ]
    assert [s.id for s in plan.skipped] == ["blocked", "flaky"]
    assert "owner reviews" in plan.skipped[0].reason
    result = runner.invoke(app, ["repair", "plan", str(repo), "--json"])
    assert result.exit_code == 0 and json.loads(result.output)["targets"][0]["id"] == "s"


def test_cli_check_exit_codes(repo):
    assert runner.invoke(app, ["repair", "check", str(repo)]).exit_code == 2
    (repo / "evidence").mkdir()
    (repo / "evidence" / "note.yaml").write_text("why: x\n")
    result = runner.invoke(app, ["repair", "check", str(repo), "--json"])
    assert result.exit_code == 1
    summary = json.loads(result.output)
    assert summary["proposable"] and not summary["ready"] and "markdown" in summary


def test_try_can_save_the_fetched_pages(repo, tmp_path):
    from gleanyard.tryout import try_source

    project = load_project(repo)
    report = try_source(
        project,
        project.sources[0],
        client=site({"/events": CLEAN}),
        today=TODAY,
        save_pages=tmp_path / "pages",
    )
    assert [Path(p).name for p in report.saved_pages] == ["01.txt"]
    assert (tmp_path / "pages" / "01.txt").read_bytes() == CLEAN


def test_template_repair_workflow_limits_the_agent(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "record")
    workflow = yaml.safe_load((root / ".github/workflows/repair.yml").read_text())
    assert workflow[True]["workflow_run"]["workflows"] == ["collect"]
    fix, propose = workflow["jobs"]["fix"], workflow["jobs"]["propose"]
    assert fix["permissions"] == {"contents": "read", "pull-requests": "read"}
    agent = next(step for step in fix["steps"] if step.get("id") == "agent")
    tools = re.search(r'--allowedTools "([^"]+)"', agent["with"]["claude_args"]).group(1)
    assert all(tool.startswith("Bash(gleanyard ") for tool in tools.split(",") if "Bash" in tool)
    assert "repair check" in " ".join(step.get("run", "") for step in propose["steps"])


def test_template_workflows_pin_this_minor_version(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "record")
    major, minor = __version__.split(".")[:2]
    for path in (root / ".github/workflows").glob("*.yml"):
        pins = re.findall(r"gleanyard~=([\d.]+)", path.read_text())
        assert pins and all(pin == f"{major}.{minor}.0" for pin in pins), path.name
