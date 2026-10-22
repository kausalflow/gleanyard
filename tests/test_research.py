import json

import pytest
import yaml
from test_html import make_project
from test_repair import git
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.research import check_research

runner = CliRunner()


@pytest.fixture
def repo(tmp_path):
    root = make_project(
        tmp_path / "p",
        {
            "delivery": "json",
            "url": "https://known.example/api",
            "extract": {
                "mode": "fields",
                "map": {"title": "name", "start_date": "date", "source_url": "url"},
            },
        },
    )
    (root / "gleanyard.yaml").write_text("excluded_hosts: [spam.example]\n")
    (root / "candidates").mkdir()
    (root / "candidates" / "old.example.yaml").write_text("url: https://old.example/\nwhy: x\n")
    git(root, "init", "-q")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return root


def candidate(root, host, url=None, why="Lists family events with dates.", **extra):
    path = root / "candidates" / f"{host}.yaml"
    path.write_text(yaml.safe_dump({"url": url or f"https://{host}/events", "why": why, **extra}))
    return path


def test_new_candidates_are_proposable(repo):
    candidate(repo, "new.example", option="ical feed")
    candidate(repo, "other.example")
    result = check_research(repo)
    assert result.proposable, result.markdown()
    assert result.files == ["candidates/new.example.yaml", "candidates/other.example.yaml"]


def test_each_candidate_is_checked(repo):
    candidate(repo, "known.example")
    candidate(repo, "spam.example")
    candidate(repo, "a.example", url="https://b.example/x")
    candidate(repo, "c.example", why="")
    candidate(repo, "d.example", url="ftp://d.example/")
    problems = {item.path: item.problems for item in check_research(repo).candidates}
    assert problems == {
        "candidates/a.example.yaml": ["the file is named for a.example but url is on b.example"],
        "candidates/c.example.yaml": ["`why` must say why the page fits the schema"],
        "candidates/d.example.yaml": ["`url` must be an http(s) URL"],
        "candidates/known.example.yaml": ["known.example is already a source: s"],
        "candidates/spam.example.yaml": ["spam.example is in excluded_hosts"],
    }


def test_research_may_only_add_candidates(repo):
    (repo / "candidates" / "old.example.yaml").write_text("url: https://old.example/2\nwhy: y\n")
    (repo / "sources" / "s.yaml").write_text("id: s\n")
    candidate(repo, "new.example")
    result = check_research(repo)
    assert not result.proposable
    assert result.problems == [
        "candidates/old.example.yaml: research may add candidates, not change them",
        "sources/s.yaml: research may add candidates/<host>.yaml only",
    ]


def test_a_run_has_a_limit_and_nothing_is_not_a_proposal(repo):
    assert check_research(repo).problems == ["nothing changed"]
    for n in range(3):
        candidate(repo, f"n{n}.example")
    assert check_research(repo, max_candidates=2).problems == [
        "3 candidates; a research run may propose 2"
    ]


def test_cli_exit_codes_and_json(repo):
    assert runner.invoke(app, ["research", "check", str(repo)]).exit_code == 1
    candidate(repo, "new.example")
    result = runner.invoke(app, ["research", "check", str(repo), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["proposable"] and data["files"] == ["candidates/new.example.yaml"]
    assert "Research check: **ready for review**" in data["markdown"]
