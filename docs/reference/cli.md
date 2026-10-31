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

Writes `data/records/<id>.jsonl` and `data/review/<id>.jsonl`, and logs what changed in
`data/changes/<date>.jsonl` (see [changes](../concepts/changes.md)). A source counts as
healthy if it produced records, or is marked `expect_empty: seasonal`. A healthy fresh run
becomes the source's last-good baseline; a failed empty run keeps the last records.

Exit code 0 if every selected source is healthy, 1 if any failed, 2 if the project does not
validate.

## `gleanyard changes [PROJECT] [-s ID]... [--since DATE] [--json]`

Lists the records that collects added, changed or removed since `DATE` (default: the last 7
days), from `data/changes/`. This is the private log, not filtered by rights; the public
version is the feed that `export` writes.

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
overview (`summary.md`, for the workflow's job summary) in `DIR` (default `site/`). With
`viewer: true` in `hub.yaml`, it also writes a searchable page of each project's public export
to `records/` (see [the record viewer](../concepts/status-hub.md#the-record-viewer)).

## `gleanyard hub init DIR [--owner OWNER] [--repo OWNER/NAME|URL]... [--pages] [--name NAME]`

Creates the status hub repo from the bundled template. `--owner` watches every repo of that
user or organization with the topic `gleanyard`; `--repo` lists projects. Without `--pages`
nothing is published and the overview goes to each run's summary page.

## `gleanyard search [QUERY] [--schema NAME] [--registry URL|DIR] [--json]`

Lists the recipes in a [source registry](../concepts/registry.md) whose id, name, description,
host or tags hold every word of `QUERY`, with their health across projects. The registry is
`--registry`, else `$GLEANYARD_REGISTRY`, else `https://kausalflow.github.io/gleanyard-registry/`;
it can be a built site or a local checkout.

## `gleanyard add RECIPE [PROJECT] [--id ID] [--registry URL|DIR] [--force] [--json]`

Installs a recipe as a source: `sources/<id>.yaml`, its mapping, fixture and evidence, with
`enabled: false`, `rights.review_status: pending` and a `recipe` key. It validates the project
and replays the fixture to write the snapshot; if either fails it removes what it wrote and
exits 1. `--force` keeps the files, and allows a host the project already collects or
excludes. Exit 2 when the registry or the recipe cannot be read.

## `gleanyard registry check [ROOT] [--json]`

Checks a registry checkout offline: `registry.yaml`, and for each recipe its files, that it is
disabled with pending rights and a shareable tier, and that its fixture still gives
`expected.json`. Exit 1 on any problem.

## `gleanyard registry build [ROOT] [--out DIR] [--health/--no-health]`

Runs the check, then writes the static site to `DIR` (default `site/`): `index.json`, the
recipe files and schemas, and `index.html`. With `--health` (the default) it reads the
`status.json` of the projects in `registry.yaml` and, with `discover`, of public repos with the
topic; `GITHUB_TOKEN` is used for the topic search when set.

## `gleanyard registry pack SOURCE --project DIR --maintainer LOGIN --description TEXT --fixture-basis TEXT [--tag TAG]... [ROOT]`

Copies a working source from a project into the registry at `ROOT` as `recipes/<id>/`, and the
project's schema into `schemas/` if it is missing there. Refuses `internal_only`, `unknown` and
`blocked` sources, `llm` sources, and sources without a fixture, snapshot or evidence.

## `gleanyard registry init DIR [--url URL] [--name NAME]`

Creates a registry repo from the bundled template: `registry.yaml`, the `check` and `publish`
workflows, a README and a `CLAUDE.md`.

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

## `gleanyard test [PROJECT] [-s ID]... [--update [--today DATE]] [--json] [--markdown]`

Replays each source's fixture, `tests/fixtures/<id>.<ext>`, offline: no network and no model.
It extracts the page with the source's pinned mode and compares the records with the snapshot
`tests/fixtures/<id>.expected.json`, which also pins the date the page was judged on, so a
`min: today` bound does not reject an old fixture. A difference names the records that went
missing, appeared or changed, by their `identity` fields. Sources without a fixture or a
snapshot, and `llm` sources, are skipped. `--update` writes the snapshot from today's replay,
only if it passes the same thresholds as `try`. For a fixture saved before its events passed,
`--update --today 2026-10-10` judges it as of the day it was saved, and the snapshot keeps that
date. Exits 1 on any difference. The template's
`validate` workflow runs it on every pull request, and `repair check` runs it for each
repaired source. See [fixtures and snapshots](../concepts/setting-up-a-source.md#fixtures-and-snapshots).

## `gleanyard terms [PROJECT] [-s ID]... [--force] [--json]`

Fetches the pages listed in each enabled source's newest `evidence/<id>/<date>-terms.yaml`
(under `pages`, each with a `url`; the folder is the source's `evidence` key when set) and the source host's `robots.txt`, and compares a
fingerprint of their visible text with the one taken at that review. Each source is checked at
most once every 7 days unless `--force`, and a page that several sources list is read once. Results go to `terms` in `status.json`. A changed page
stays flagged until a newer evidence file is added, and exits 1 while any source is flagged.
See [watching the terms](../concepts/rights.md#watching-the-terms).

## `gleanyard badge [PROJECT] [--out DIR] [--json]`

Writes README badges from `status.json`: `badges/project.svg` (for example `1 failing, 3 ok`)
and `badges/sources/<id>.svg` with each source's health, or `terms changed`. Badges of removed
sources are deleted. The template's `collect` workflow refreshes and commits them.

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
run by the same rules that mark a run `degraded`; its fixture must still replay to its snapshot
(`gleanyard test`). Exits 0 when ready for review, 1 when it can
only be a draft, 2 when it cannot be proposed. `--json` adds `files` (what to commit) and the
Markdown report.

## `gleanyard research check [PROJECT] [--base REF] [--max N] [--json] [--markdown]`

Judges the working tree's changes against `REF` (default `HEAD`) as an unattended research
run, without a model: only new `candidates/<host>.yaml` files, each with an http(s) `url` on
that host and a `why`, none for a host that is a source or excluded, and at most `N` (default
10). Prints `ready` or `not proposable` and exits 0 or 1. The template's opt-in `research`
workflow runs it; see [automatic research](../concepts/auto-research.md).

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

## `gleanyard mcp [PROJECT] [--allow-writes]`

Runs an MCP server over stdio for the project, with read-only tools that return the same JSON
as `--json`: `validate`, `status`, `check_url`, `probe`, `scaffold` (returns the draft, writes
nothing), `try_source`, `test_fixtures`, `repair_plan`, `repair_check`, `json_schema`,
`search_records` (the public export only), `recent_changes` (the public change feed) and
`search_registry` (recipes in the source registry), and `read_file` (config and review
folders, never `data/`). `--allow-writes` adds `write_file`, `save_fixture`,
`update_snapshot`, `add_recipe` and `collect` (enabled sources only); none of them changes a
source's `enabled` or `rights.review_status`. The procedures are served as prompts and
the skill as the resource `gleanyard://skill`. Needs the optional extra `pip install 'gleanyard[mcp]'`.

## `gleanyard rights review [PROJECT] [-s ID]...`

The owner's rights decision, asked interactively. It walks the sources whose
`review_status` is `pending` (or the ones named with `-s`, even if decided), one evidence
folder at a time, so sources that share a review are answered once. For each it shows the
URLs, the latest `evidence/<folder>/<date>-terms.yaml` (page quotes, `reading`) and asks:

- the tier, defaulting to the evidence file's `suggested_tier`;
- whether to keep the `allowed_actions` and `public_fields` suggested for that tier and the
  schema's field classes (every required field for `link_only`, every `fact` field for
  `facts_only`, every field but `personal` ones for open and licensed tiers, none for
  `internal_only`, `unknown` and `blocked`), or to type them;
- the licence id and URL for open tiers, and the attribution text when one is required;
- approve, reject or skip; whether to enable the sources (approved ones only); notes.

Only the `rights:` block and the `enabled:` line of each source file are rewritten, with
`reviewed_at` set to today. It ends with `validate` and exits 1 if the project does not pass.

## `gleanyard export [PROJECT] [--json]`

Writes the rights-filtered public export, by default `exports/records.jsonl` (set `export`
in `gleanyard.yaml`), joining rows that agree on the schema's `merge` fields, and the
[change feed](../concepts/changes.md#the-public-feed) `exports/changes.json`. It reads stored
records, so run `collect` first.

With `export_csv` set, it also writes the same rows as CSV: `id`, one column per schema field
(personal fields never), then `_source`, `_source_url`, `_attribution` and `_license`. List
values are joined with `; ` and objects such as money are written as JSON.

With `export_partition` set to a schema field, the export is split into one file per value
of that field, in a folder named after the export path: `exports/records/<value>.jsonl`, and
for CSV `dataset/records/<value>.csv`. A file name is the value in lower case with other
characters as `-` (`_none` for rows without a value). Files for values that no longer
occur are deleted. Use it when one file would be too large for git hosting (GitHub refuses
files over 100 MB). `dataherb.json` lists the JSON lines parts as one resource. The CSV is
compact enough to stay whole as well: `export_csv` gets every row, and the parts go to
`export_csv_parts`, by default a folder named after the CSV.

## `gleanyard json-schema {source|schema|project}`

Prints the JSON Schema for a config file, for editor completion and AI tools.

## `gleanyard --version`

## `gleanyard.yaml`

Optional, at the project root. All keys have defaults.

```yaml
schema: schema.yaml
sources: sources
user_agent: "my-project (+https://example.org/contact)"
storage: {root: data, changes_days: 90, records: full}   # change logs older than this are deleted
export: exports/records.jsonl
export_csv: null                    # also write the export as CSV here, e.g. dataset/records.csv
export_partition: null              # split the export into one file per value of this field
export_csv_parts: null              # folder for the CSV parts, e.g. data/by_indicator
changes_feed: exports/changes.json        # JSON Feed of recent changes; null turns it off
status: status.json                 # health report, read by the status hub
excluded_hosts: []                  # hosts `check` reports as excluded
rights_policy:
  permitted_tiers: [open, open_attribution, licensed, facts_only, link_only]
  require_reviewed_permission: true
```

Set `user_agent` to something that lets a site owner reach you.

`storage.records: compact` stores each source's records in `data/records/<source>.jsonl`
sorted by id, with each distinct provenance written once in the first line instead of on
every record. Use it for sources with many thousands of records: the files are much smaller
and a commit's diff holds only the records that changed. Both layouts read back the same, so
you can switch at any time; the next collect rewrites the file.
