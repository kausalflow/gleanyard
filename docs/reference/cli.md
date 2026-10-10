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
rights_policy:
  permitted_tiers: [open, open_attribution, licensed, facts_only, link_only]
  require_reviewed_permission: true
```

Set `user_agent` to something that lets a site owner reach you.
