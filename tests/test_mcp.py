import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402
from test_html import FARM_MAPPING, FIXTURES, TODAY, make_project  # noqa: E402

from gleanyard.init import init_project  # noqa: E402
from gleanyard.mcp_server import build_server  # noqa: E402
from gleanyard.skill import procedures, skill_text  # noqa: E402

ROWS = [
    {"id": "a", "title": "Puppet theatre", "_source": {"source_id": "theatre"}},
    {"id": "b", "title": "Farm day", "_source": {"source_id": "farm"}},
    {"id": "c", "title": "Puppet workshop", "_source": {"source_id": "farm"}},
]


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "p"
    init_project(root, "p", "event")
    export = root / "exports" / "records.jsonl"
    export.parent.mkdir()
    export.write_text("".join(json.dumps(row) + "\n" for row in ROWS))
    return root


def run(root, step, writes=False):
    async def main():
        async with Client(build_server(root, writes=writes)) as client:
            return await step(client)

    return asyncio.run(main())


def call(root, name, writes=False, **arguments):
    async def step(client):
        return await client.call_tool(name, arguments)

    result = run(root, step, writes)
    return result


def write(root, name, **arguments):
    return call(root, name, writes=True, **arguments)


def payload(result):
    assert not result.is_error, result.content
    return result.structured_content


def test_server_lists_read_only_tools_prompts_and_the_skill(root):
    async def step(client):
        return (
            await client.list_tools(),
            await client.list_prompts(),
            await client.read_resource("gleanyard://skill"),
        )

    tools, prompts, skill = run(root, step)
    names = {tool.name for tool in tools.tools}
    assert names == {
        "validate",
        "status",
        "check_url",
        "probe",
        "scaffold",
        "try_source",
        "test_fixtures",
        "repair_plan",
        "repair_check",
        "json_schema",
        "search_records",
        "recent_changes",
        "search_registry",
        "read_file",
    }
    assert all(tool.annotations.read_only_hint for tool in tools.tools)
    assert {prompt.name for prompt in prompts.prompts} == {
        "research",
        "set_up_source",
        "repair",
        "review_terms",
        "retire_source",
        "add_from_registry",
    }
    assert skill.contents[0].text == skill_text()


def test_tools_return_the_cli_json(root):
    validated = payload(call(root, "validate"))
    assert validated["ok"] and validated["schema"] == "event"
    checked = payload(call(root, "check_url", url="https://Example.org/x"))
    assert checked == {"host": "example.org", "status": "new", "sources": [], "candidate": False}
    schema = payload(call(root, "json_schema", kind="source"))
    assert "properties" in schema
    assert call(root, "json_schema", kind="nope").is_error
    missing = call(root, "status")
    assert missing.is_error and "no status yet" in missing.content[0].text


def test_search_reads_only_the_public_export(root):
    found = payload(call(root, "search_records", text="puppet"))
    assert found["total"] == 2 and [row["id"] for row in found["records"]] == ["a", "c"]
    farm = payload(call(root, "search_records", text="puppet", source_id="farm"))
    assert [row["id"] for row in farm["records"]] == ["c"]
    capped = payload(call(root, "search_records", limit=1))
    assert capped["total"] == 3 and len(capped["records"]) == 1
    (root / "exports" / "records.jsonl").unlink()
    assert payload(call(root, "search_records"))["total"] == 0


def test_recent_changes_read_the_public_feed(root):
    assert payload(call(root, "recent_changes"))["total"] == 0
    items = [
        {"id": "a:added", "_gleanyard": {"change": "added", "record": ROWS[0]}},
        {"id": "b:removed", "_gleanyard": {"change": "removed", "record": ROWS[1]}},
    ]
    (root / "exports" / "changes.json").write_text(json.dumps({"items": items}))
    assert payload(call(root, "recent_changes"))["total"] == 2
    farm = payload(call(root, "recent_changes", source_id="farm"))
    assert [item["id"] for item in farm["items"]] == ["b:removed"]


def test_prompts_carry_the_procedure_of_the_installed_version(root):
    async def step(client):
        return await client.get_prompt("repair", {"source_id": "farm"})

    text = run(root, step).messages[0].content.text
    assert "CLAUDE.md" in text and "`farm`" in text
    assert procedures()["repair"] in text and "Pull request checklist" in text


def test_a_project_that_does_not_validate_is_reported(root):
    (root / "schema.yaml").write_text("not: [valid")
    result = call(root, "repair_plan")
    assert result.is_error and "does not validate" in result.content[0].text


def test_search_registry_reads_the_configured_registry(root, monkeypatch):
    registry = Path(__file__).parents[1] / "examples" / "registry"
    monkeypatch.setenv("GLEANYARD_REGISTRY", str(registry))
    found = payload(call(root, "search_registry", query="feed"))
    assert [entry["id"] for entry in found["recipes"]] == ["city-feed"]
    monkeypatch.setenv("GLEANYARD_REGISTRY", str(root))
    assert call(root, "search_registry").is_error


WRITE_TOOLS = {"write_file", "save_fixture", "update_snapshot", "add_recipe", "collect"}


@pytest.fixture
def farm(tmp_path, monkeypatch):
    import gleanyard.fetch as fetch_module

    root = make_project(
        tmp_path / "farm",
        {
            "delivery": "html",
            "url": "https://farm.example/events",
            "extract": {"mode": "selectors", "selectors": "mappings/m.json"},
        },
        FARM_MAPPING,
    )
    page = b"\n".join(  # the farm page without its header row and its row with a bad date
        line
        for line in (FIXTURES / "farm.html").read_bytes().splitlines()
        if b"<th>" not in line and b"nicht bekannt" not in line
    )

    def handler(request):
        if request.url.path == "/events":
            return httpx.Response(200, content=page, headers={"content-type": "text/html"})
        return httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr(
        fetch_module.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    return root


def test_write_tools_are_served_only_when_allowed(root):
    async def step(client):
        return await client.list_tools()

    read_only = {tool.name for tool in run(root, step).tools}
    tools = run(root, step, writes=True).tools
    assert not read_only & WRITE_TOOLS
    assert {tool.name for tool in tools} == read_only | WRITE_TOOLS
    written = [tool for tool in tools if tool.name in WRITE_TOOLS]
    assert not any(tool.annotations.read_only_hint for tool in written)
    assert payload(write(root, "validate"))["ok"]


def test_read_file_reads_config_but_not_stored_records(root):
    assert (
        payload(call(root, "read_file", path="schema.yaml"))["text"]
        == (root / "schema.yaml").read_text()
    )
    assert "files" in payload(call(root, "read_file", path="sources"))
    (root / "data").mkdir()
    (root / "data" / "x.json").write_text("{}")
    for path in ("data/x.json", "../outside", "exports/records.jsonl", "status.json"):
        assert call(root, "read_file", path=path).is_error, path


def test_write_file_stays_in_the_review_folders_and_validates(root):
    text = "url: https://example.org\nwhy: a test\n"
    done = payload(write(root, "write_file", path="candidates/example.org.yaml", text=text))
    assert done["created"] and done["path"] == "candidates/example.org.yaml"
    assert (root / "candidates" / "example.org.yaml").read_text() == text
    for path in ("data/x.json", "status.json", "exports/records.jsonl", "../x", "schema.yaml"):
        assert write(root, "write_file", path=path, text="x").is_error, path
    assert not (root / "status.json").exists()


def test_write_file_keeps_enabled_and_review_status_for_the_owner(farm):
    path = farm / "sources" / "s.yaml"
    source = yaml.safe_load(path.read_text())
    edited = {**source, "fetch": {"delay_seconds": 1}}
    done = payload(write(farm, "write_file", path="sources/s.yaml", text=yaml.safe_dump(edited)))
    assert done["valid"], done["issues"]
    for change in ({"enabled": False}, {"rights": {**source["rights"], "review_status": "x"}}):
        refused = write(
            farm, "write_file", path="sources/s.yaml", text=yaml.safe_dump({**source, **change})
        )
        assert refused.is_error and "owner" in refused.content[0].text
    new = {**source, "id": "t", "enabled": True}
    assert write(farm, "write_file", path="sources/t.yaml", text=yaml.safe_dump(new)).is_error
    assert not (farm / "sources" / "t.yaml").exists()


def test_save_fixture_then_update_snapshot(farm):
    saved = payload(write(farm, "save_fixture", source_id="s"))
    assert saved["path"] == "tests/fixtures/s.html" and saved["bytes"] > 0
    assert write(farm, "save_fixture", source_id="s").is_error  # exists, no replace
    assert payload(write(farm, "save_fixture", source_id="s", replace=True))["bytes"]
    snapshot = payload(write(farm, "update_snapshot", source_id="s", today=TODAY.isoformat()))
    assert snapshot["passed"], snapshot
    assert (farm / "tests" / "fixtures" / "s.expected.json").is_file()
    assert payload(call(farm, "test_fixtures", source_id="s"))["passed"]


def test_collect_runs_only_an_enabled_source(farm):
    run_ = payload(write(farm, "collect", source_id="s"))
    assert run_["ok"] and run_["records"] > 0
    assert (farm / "status.json").is_file() and (farm / "data").is_dir()
    path = farm / "sources" / "s.yaml"
    path.write_text(path.read_text().replace("enabled: true", "enabled: false"))
    refused = write(farm, "collect", source_id="s")
    assert refused.is_error and "disabled" in refused.content[0].text


def test_add_recipe_installs_disabled_and_pending(tmp_path, monkeypatch):
    registry = Path(__file__).parents[1] / "examples" / "registry"
    monkeypatch.setenv("GLEANYARD_REGISTRY", str(registry))
    root = tmp_path / "catalog"
    init_project(root, "catalog", "dataset")
    (root / "schema.yaml").write_text((registry.parent / "datasets" / "schema.yaml").read_text())
    result = payload(write(root, "add_recipe", recipe="portal-api"))
    assert result["ok"], result
    source = yaml.safe_load((root / "sources" / "portal-api.yaml").read_text())
    assert source["enabled"] is False and source["rights"]["review_status"] == "pending"
