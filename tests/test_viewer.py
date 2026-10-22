import json

from test_hub import NOW, config, source, status
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.hub.config import HubConfig
from gleanyard.hub.dashboard import render_dashboard
from gleanyard.hub.state import collect_state
from gleanyard.hub.viewer import render_viewer, table_rows

runner = CliRunner()
ROWS = [
    {"id": "1", "title": "Puppets", "url": "https://x.example/1", "_source": {"source_id": "s"}},
    {
        "id": "2",
        "title": "</script><b>Farm",
        "venue": "Hof",
        "_source": {"source_id": "t"},
        "_sources": [{"source_id": "t"}, {"source_id": "u"}],
    },
    {"id": "3", "title": "Late", "_source": {"source_id": "s"}},
]
EXPORT = "".join(json.dumps(row) + "\n" for row in ROWS).encode()


class Files:
    """A GitHub stand-in that serves status.json and the export by path."""

    def __init__(self, files):
        self.files = files

    def get_file(self, repo, path):
        return self.files.get((repo, path))

    def open_pulls(self, repo):
        return []


def state(**kw):
    github = Files(
        {
            ("o/a", "status.json"): status(source("s")),
            ("o/a", "exports/records.jsonl"): EXPORT,
            ("o/b", "status.json"): status(source("y")),
        }
    )
    return collect_state(config(**kw), github, NOW)


def test_the_viewer_is_off_unless_asked():
    assert all(repo.export is None for repo in state().repos)


def test_the_hub_reads_each_export_up_to_the_row_cap():
    a, b = state(viewer=True, viewer_rows=2).repos
    assert a.export.total == 3 and [row["id"] for row in a.export.rows] == ["1", "2"]
    assert b.export.problem == "exports/records.jsonl not found"
    page = render_dashboard(collect_state(config(viewer=True), Files({}), NOW))
    assert "Browse" not in page


def test_rows_become_text_cells_with_every_source():
    columns, cells = table_rows(ROWS)
    assert columns == ["title", "url", "venue", "source"]
    assert cells[1]["source"] == "t, u" and cells[2] == {"title": "Late", "source": "s"}


def test_the_page_embeds_rows_safely_and_the_dashboard_links_it():
    a = state(viewer=True, viewer_rows=2).repos[0]
    page = render_viewer(a)
    assert "<\\/script><b>Farm" in page and "</script><b>" not in page
    assert "The first 2 of 3 rows are here." in page
    dashboard = render_dashboard(state(viewer=True))
    assert "<a href='records/o--a.html'>Browse 3 records</a>" in dashboard
    assert "records: exports/records.jsonl not found" in dashboard


def test_a_status_url_project_can_name_its_export(tmp_path):
    hub = HubConfig.model_validate(
        {
            "viewer": True,
            "repos": [
                {
                    "name": "x",
                    "url": "https://h.example/status.json",
                    "export_url": "https://h.example/e",
                },
                "https://h.example/other/status.json",
            ],
        }
    )
    served = {
        "https://h.example/status.json": status(source("s")),
        "https://h.example/other/status.json": status(source("t")),
        "https://h.example/e": EXPORT,
    }
    x, other = collect_state(hub, None, NOW, fetch=served.__getitem__).repos
    assert x.export.total == 3
    assert other.export is None  # a bare status.json URL names no export


def test_hub_build_writes_a_viewer_page(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(state(viewer=True).model_dump_json())
    result = runner.invoke(
        app, ["hub", "build", "--state", str(path), "--out", str(tmp_path / "site")]
    )
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "site" / "records" / "o--a.html").is_file()
    assert not (tmp_path / "site" / "records" / "o--b.html").exists()
