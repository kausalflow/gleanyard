// Offline tests for the connector: node connector/test.mjs
// fetch is replaced by a table of canned responses, so nothing goes to the network.
import assert from "node:assert/strict";
import worker, { parseCsv } from "./worker.js";

const REGISTRY = "https://kausalflow.github.io/gleanyard-registry/";
const RAW = "https://raw.githubusercontent.com/acme/events/HEAD/";
const pages = {
  [REGISTRY + "index.json"]: JSON.stringify({
    name: "gleanyard-registry",
    recipes: [
      {
        id: "grips-theater",
        name: "GRIPS Theater",
        description: "Children's theatre",
        schema: "family-event",
        tags: ["berlin", "theater"],
        host: "www.grips-theater.de",
        files: ["source.yaml", "recipe.yaml", "evidence/2026-10-10-terms.yaml", "fixture.html"],
        health: { level: "ok", users: 2 },
      },
      { id: "other", name: "Other", schema: "job", tags: [], files: [] },
    ],
  }),
  [REGISTRY + "recipes/grips-theater/source.yaml"]: "id: grips-theater\n",
  [REGISTRY + "recipes/grips-theater/recipe.yaml"]: "maintainers: [x]\n",
  [REGISTRY + "recipes/grips-theater/evidence/2026-10-10-terms.yaml"]: "quote: ok\n",
  [REGISTRY + "schemas/family-event.yaml"]: "name: family-event\n",
  [RAW + "status.json"]: JSON.stringify({
    schema_name: "family-event",
    healthy: false,
    sources: [
      { id: "a", enabled: true, health: "ok", records: 3 },
      { id: "b", enabled: true, health: "failing", records: 0, error_kind: "blocked" },
    ],
  }),
  [RAW + "exports/records.jsonl"]: [
    { title: "Puppet show", _source: { source_id: "a" } },
    { title: "Concert", _source: { source_id: "a" } },
    { title: "Puppet workshop", _source: { source_id: "b" } },
  ]
    .map((r) => JSON.stringify(r))
    .join("\n"),
  [RAW + "exports/changes.json"]: JSON.stringify({
    title: "changes",
    items: [
      { id: "1", _gleanyard: { record: { _source: { source_id: "a" } } } },
      { id: "2", _gleanyard: { record: { _source: { source_id: "b" } } } },
    ],
  }),
  "https://raw.githubusercontent.com/acme/csvonly/HEAD/dataherb.json": JSON.stringify({
    datapackage: { resources: [{ path: "exports/records.csv" }] },
  }),
  "https://raw.githubusercontent.com/acme/csvonly/HEAD/exports/records.csv":
    'title,source_id\n"Puppet, show",a\nConcert,b\n',
};
const fetched = [];
globalThis.fetch = async (url) => {
  fetched.push(String(url));
  const body = pages[String(url)];
  return body === undefined ? new Response("no", { status: 404 }) : new Response(body);
};

let next = 1;
async function rpc(method, params) {
  const request = new Request("https://connector.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: next++, method, params }),
  });
  const response = await worker.fetch(request, {}, {});
  assert.equal(response.status, 200);
  return (await response.json()).result;
}
async function call(name, args = {}) {
  const r = await rpc("tools/call", { name, arguments: args });
  return r.isError ? { error: r.content[0].text } : r.structuredContent ?? r.content[0].text;
}

const init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "t" } });
assert.equal(init.protocolVersion, "2025-06-18");
assert.equal(init.serverInfo.name, "gleanyard");
const unknown = await rpc("initialize", { protocolVersion: "1999-01-01" });
assert.equal(unknown.protocolVersion, "2025-11-25");

const tools = (await rpc("tools/list")).tools.map((t) => t.name);
assert.deepEqual(tools, [
  "get_started", "search_recipes", "get_recipe", "get_schema",
  "project_status", "search_records", "recent_changes",
]);
for (const t of (await rpc("tools/list")).tools) assert.equal(t.annotations.readOnlyHint, true);

assert.match(await call("get_started"), /Use this template/);

let r = await call("search_recipes", { query: "berlin" });
assert.equal(r.total, 1);
assert.equal(r.recipes[0].install, "gleanyard add grips-theater");
assert.equal((await call("search_recipes", { schema: "job" })).total, 1);
assert.equal((await call("search_recipes")).total, 2);

r = await call("get_recipe", { recipe: "grips-theater" });
assert.deepEqual(Object.keys(r.files).sort(), ["evidence/2026-10-10-terms.yaml", "recipe.yaml", "source.yaml"]);
assert.match((await call("get_recipe", { recipe: "nope" })).error, /no recipe/);
assert.match((await call("get_recipe", { recipe: "../x" })).error, /recipe id/);
assert.equal((await call("get_schema", { name: "family-event" })).schema_yaml, "name: family-event\n");

r = await call("project_status", { repo: "acme/events" });
assert.deepEqual(r.counts, { ok: 1, failing: 1 });
assert.equal(r.sources[1].error_kind, "blocked");

r = await call("search_records", { repo: "acme/events", text: "puppet" });
assert.equal(r.total, 2);
r = await call("search_records", { repo: "acme/events", text: "puppet", source_id: "b", limit: 1 });
assert.equal(r.total, 1);
assert.equal(r.records[0].title, "Puppet workshop");
r = await call("search_records", { repo: "acme/csvonly", text: "puppet" });
assert.deepEqual(r.records, [{ title: "Puppet, show", source_id: "a" }]);

r = await call("recent_changes", { repo: "acme/events", source_id: "b" });
assert.deepEqual(r.items.map((i) => i.id), ["2"]);

// only the registry and raw.githubusercontent.com are ever fetched
for (const bad of ["evil.com/x", "acme/events/../x", "acme", "https://evil.com/a/b"]) {
  assert.match((await call("project_status", { repo: bad })).error, /owner\/name/);
}
assert.match((await call("search_records", { repo: "acme/events", path: "../../x" })).error, /relative file path/);
assert.match((await call("search_records", { repo: "acme/events", ref: "a/../../b" })).error, /ref/);
for (const url of fetched) {
  assert.ok(url.startsWith(REGISTRY) || url.startsWith("https://raw.githubusercontent.com/"), url);
}

// protocol edges
assert.equal((await rpc("ping")).constructor, Object);
let res = await worker.fetch(new Request("https://connector.test/mcp", {
  method: "POST", body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
}), {}, {});
assert.equal(res.status, 202);
res = await worker.fetch(new Request("https://connector.test/mcp"), {}, {});
assert.equal(res.status, 405);
res = await worker.fetch(new Request("https://connector.test/mcp", { method: "POST", body: "{" }), {}, {});
assert.equal(res.status, 400);
res = await worker.fetch(new Request("https://connector.test/mcp", {
  method: "POST", body: JSON.stringify({ jsonrpc: "2.0", id: 9, method: "nope" }),
}), {}, {});
assert.equal((await res.json()).error.code, -32601);
res = await worker.fetch(new Request("https://connector.test/"), {}, {});
assert.match(await res.text(), /https:\/\/connector\.test\/mcp/);

assert.deepEqual(parseCsv('a,b\r\n"x ""y""",2\r\n'), [{ a: 'x "y"', b: "2" }]);

console.log("connector: all tests passed");
