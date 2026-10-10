"""The quickstart's files are real: they must validate and run as written."""

import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from gleanyard.project import load_project
from gleanyard.run import collect_source, export_project

QUICKSTART = Path(__file__).parents[1] / "docs" / "quickstart.md"


def quickstart_files() -> dict[str, str]:
    pattern = re.compile(r'```yaml title="([^"]+)"\n(.*?)```', re.S)
    return dict(pattern.findall(QUICKSTART.read_text()))


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    for name, text in quickstart_files().items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_quickstart_has_both_files():
    assert set(quickstart_files()) == {"schema.yaml", "sources/city-events.yaml"}


def test_quickstart_validates(project_dir):
    project = load_project(project_dir)
    assert project.ok, project.issues


def test_quickstart_error_example_is_real(project_dir):
    path = project_dir / "sources" / "city-events.yaml"
    path.write_text(path.read_text().replace("venue: place.name", "colour: place.name"))
    issue = str(load_project(project_dir).issues[0])
    example = re.search(
        r"```text\n(sources/city-events.yaml:\d+: .*?)\n```", QUICKSTART.read_text()
    )
    assert example and issue == example.group(1)


def test_quickstart_collects_and_exports(project_dir):
    # The quickstart keeps rights private; approve a copy for facts so export has output.
    path = project_dir / "sources" / "city-events.yaml"
    text = path.read_text()
    rights = text[text.index("rights:") :]
    path.write_text(
        text.replace(
            rights,
            "rights:\n"
            "  review_status: approved\n"
            "  effective_tier: facts_only\n"
            "  public_fields: [title, start_date, source_url]\n"
            "  allowed_actions: {private_store: true, normalize: true, redistribute_facts: true}\n",
        )
    )
    project = load_project(project_dir)
    assert project.ok, project.issues

    body = (
        b'{"data": {"events": [{"name": "Jazz", "starts_on": "2026-10-12",'
        b' "link": "https://example.org/e/1", "place": {"name": "Park"},'
        b' "offers": [{"price": "12,50 EUR"}]}]}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    source = project.sources[0]
    source = source.model_copy(
        update={"fetch": source.fetch.model_copy(update={"delay_seconds": 0})}
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    run = collect_source(project, source, client=client, today=date(2026, 10, 10))
    assert run.ok and run.records == 1, run
    path, count = export_project(project, today=date(2026, 10, 10))
    assert count == 1
    assert '"title": "Jazz"' in path.read_text()
