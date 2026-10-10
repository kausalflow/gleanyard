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

## `gleanyard try SOURCE [PROJECT] [--json] [--markdown]`

A live dry run that writes nothing: fetches the source, extracts with its pinned mode, and
prints sample records, how often each field is filled, why records were rejected and a
`PASS` or `FAIL`. It fails when there are no records (unless `expect_empty: seasonal`), when
over 20% of candidates were rejected, or when over half of the records lost a field to a
warning. Exits 1 on `FAIL`. `--markdown` prints the report as a pull request description.

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
