# Command line

Every command takes the project folder as an optional first argument (default: the current
folder). Commands that print results accept `--json`.

## `gleanyard validate [PROJECT] [--json]`

Checks `gleanyard.yaml`, `schema.yaml` and every source file. For each problem it reports
the file, the line, the key and what to change. It also checks across files:

- the source's `schema` matches the project schema;
- every key in `extract.map` and `extract.defaults` is a schema field;
- a `fields` source maps (or defaults) every required field;
- selector files exist;
- source ids match file names and are unique;
- an enabled source allows `private_store` and `normalize`;
- `rights.public_fields` names real fields.

Exit code 0 if everything is valid, 1 otherwise.

## `gleanyard collect [PROJECT] [-s ID]... [--from-cache] [--json]`

Fetches and extracts sources, by default every enabled one. `-s` selects specific sources,
enabled or not. `--from-cache` re-extracts the newest stored response with no network.

Writes `data/records/<id>.jsonl` and `data/review/<id>.jsonl`. A source counts as healthy
if it produced records, or is marked `expect_empty: seasonal`. A healthy fresh run becomes
the source's last-good baseline.

Exit code 0 if every selected source is healthy, 1 if any failed, 2 if the project does not
validate.

## `gleanyard status [PROJECT] [--json]`

Shows the health report written by the last `collect` (see [Health and status.json](../concepts/status.md)). Exits 1 if any source is `degraded` or `failing`, or if no report exists yet.

## `gleanyard init DIR [--schema NAME] [--name NAME]`

Creates a collection project in an empty (or missing) directory from the bundled template; see
[the repo template](../concepts/repo-template.md).

## `gleanyard hub check [--config hub.yaml] [--hub-repo OWNER/NAME] [--json]`

Checks a hub's setup without changing anything: `hub.yaml`, the token, discovery, every
project's `status.json`, and Pages when the workflow publishes there. Prints `ok` or `FAIL`
per check, with the step that fixes each failure, and exits 1 if anything failed.

## `gleanyard hub run [--config hub.yaml] [--hub-repo OWNER/NAME] [--state PATH] [--dry-run] [--json]`

Reads every project in `hub.yaml` (listed, discovered by topic, or given as a status.json URL),
writes the dashboard snapshot, and opens or closes alert issues in the hub repo (see
[the status hub](../concepts/status-hub.md)). Reads with `GH_TOKEN` and writes issues with
`GITHUB_TOKEN` when both are set. A hub that only lists URLs can `--dry-run` with no token.

## `gleanyard hub build [--state PATH] [--out DIR] [--title TEXT]`

Renders the snapshot from `hub run` as a static dashboard (`index.html`) and a Markdown
overview (`summary.md`, for the workflow's job summary) in `DIR` (default `site/`).

## `gleanyard hub init DIR [--owner OWNER] [--repo OWNER/NAME|URL]... [--pages] [--name NAME]`

Creates the status hub repo from the bundled template. `--owner` watches every repo of that
user or organization with the topic `gleanyard`; `--repo` lists projects. Without `--pages`
nothing is published and the overview goes to each run's summary page.

## `gleanyard probe URL [--root DIR] [--json]`

Looks at a site without a model: reads `robots.txt` and the page (two requests), finds
`.ics`, RSS/Atom and JSON links, schema.org types, sitemaps named by robots.txt and
imprint/terms pages, and prints the ways to collect ranked: feeds first, then `jsonld`, then
`selectors` and `llm`. Exits 1 if robots.txt disallows the URL.

## `gleanyard check URL [PROJECT] [--json]`

Says whether the URL's host is already a `source`, a `candidate` (a file in
`candidates/<host>.yaml`), `excluded` (listed in `excluded_hosts` of `gleanyard.yaml`) or
`new`. Exits 1 unless it is new, so research can skip hosts it has already handled.

## `gleanyard scaffold URL --id ID [PROJECT] [--option N] [--write] [--json]`

Drafts `sources/<id>.yaml` from the probe's option N (default 1). For feeds the schema fields
are mapped to the feed's keys by field name and schema `aliases` (`DTSTART` to `start_date`);
required fields it cannot find are listed as notes. Drafts are `enabled: false` with rights
pending. `--write` saves the file and refuses to overwrite one.

## `gleanyard try SOURCE [PROJECT] [--json] [--markdown] [--save-pages DIR]`

A live dry run that stores nothing: fetches the source, extracts with its pinned mode, and
prints sample records, how often each field is filled, why records were rejected and a
`PASS` or `FAIL`. It fails when there are no records (unless `expect_empty: seasonal`), when
over 20% of candidates were rejected, or when over half of the records lost a field to a
warning. Exits 1 on `FAIL`. `--markdown` prints the report as a pull request description.
`--save-pages DIR` also writes the fetched pages there (`01.html`, ...), so a repair can read
the page the source returns today.

## `gleanyard repair plan [PROJECT] [--json]`

Reads `status.json` and lists the enabled sources an automated repair may attempt: every
`degraded` source, and `failing` ones whose `error_kind` an edit can fix (`structure_changed`,
`empty`, `pagination_changed`, `filter_ignored`). The rest are listed as skipped with the
reason, for example `blocked` (the owner reviews the terms) or `transport` (it usually clears
on the next run). See [automatic repair](../concepts/auto-repair.md).

## `gleanyard repair check [PROJECT] [--base REF] [--json] [--markdown]`

Judges the uncommitted changes against `REF` (default `HEAD`) as a repair, without a model.
Generated files (records, exports, `status.json`) are left out. It is not proposable when a
file outside `sources/`, `mappings/`, `tests/fixtures/` and `evidence/` changed, a file was
deleted, a source was added, a source's `id`, `schema`, `enabled`, `delivery`, `url`,
`homepage`, `fetch`, `coverage`, `rights` or `retention` changed, a source was switched to
`llm`, or the project does not validate. Every changed source, and every source whose mapping
changed, then runs `try`, and its record count and fill rates are compared with the last good
run by the same rules that mark a run `degraded`. Exits 0 when ready for review, 1 when it can
only be a draft, 2 when it cannot be proposed. `--json` adds `files` (what to commit) and the
Markdown report.

## `gleanyard map SOURCE [PROJECT] [--model ID] [--force] [--dry-run] [--json]`

Drafts (or repairs) the selector mapping of a `selectors` source with crawl4ai's
`generate_schema`. It fetches one sample page (the first page of a sitemap), asks the model
for a mapping that names the schema's fields, checks that the draft validates and finds
records on that page, and only then writes `extract.selectors`. The report lists how often
each mapped field is filled, fields that are not in the schema and required fields left
uncovered; run `gleanyard try` next to judge it with the full schema.

Needs the optional extra `pip install 'gleanyard[map]'`, the source's `send_to_model` right
(the page is sent to a model provider), and the provider's API key in the environment. `--model`
takes a provider/model id such as `anthropic/<model>`. An existing mapping is replaced only with
`--force`; `--dry-run` writes nothing. Exits 1 when the draft is unusable or finds no records.

## `gleanyard skill [PROJECT] [--check] [--json]`

Writes the assistant's procedures for the installed version to
`.claude/skills/gleanyard/SKILL.md`, if that file is missing or differs. `--check` only reports
`current`, `outdated` or `missing`, and exits 1 unless it is current. See
[AI assistants](../concepts/agents.md).

## `gleanyard mcp [PROJECT]`

Runs an MCP server over stdio for the project, with read-only tools that return the same JSON
as `--json`: `validate`, `status`, `check_url`, `probe`, `scaffold` (returns the draft, writes
nothing), `try_source`, `repair_plan`, `repair_check`, `json_schema` and `search_records`
(the public export only). The procedures are served as prompts and the skill as the resource
`gleanyard://skill`. Needs the optional extra `pip install 'gleanyard[mcp]'`.

## `gleanyard export [PROJECT] [--json]`

Writes the rights-filtered public export, by default `exports/records.jsonl` (set `export`
in `gleanyard.yaml`). It reads stored records, so run `collect` first.

## `gleanyard json-schema {source|schema|project}`

Prints the JSON Schema for a config file, for editor completion and AI tools.

## `gleanyard --version`

## `gleanyard.yaml`

Optional, at the project root. All keys have defaults.

```yaml
schema: schema.yaml
sources: sources
user_agent: "my-project (+https://example.org/contact)"
storage: {root: data}
export: exports/records.jsonl
status: status.json                 # health report, read by the status hub
excluded_hosts: []                  # hosts `check` reports as excluded
rights_policy:
  permitted_tiers: [open, open_attribution, licensed, facts_only, link_only]
  require_reviewed_permission: true
```

Set `user_agent` to something that lets a site owner reach you.
