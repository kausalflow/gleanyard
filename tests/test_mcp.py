import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp.client import Client  # noqa: E402

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


def run(root, step):
    async def main():
        async with Client(build_server(root)) as client:
            return await step(client)

    return asyncio.run(main())


def call(root, name, **arguments):
    async def step(client):
        return await client.call_tool(name, arguments)

    result = run(root, step)
    return result


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
    }
    assert all(tool.annotations.read_only_hint for tool in tools.tools)
    assert {prompt.name for prompt in prompts.prompts} == {
        "research",
        "set_up_source",
        "repair",
        "review_terms",
        "retire_source",
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
