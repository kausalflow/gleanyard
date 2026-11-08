# gleanyard

**An AI assistant writes the scraper once. Then it runs on a schedule without a model,
checks every record against your schema, watches itself, and opens a repair pull request
when a site changes.**

[Docs](https://kausalflow.github.io/gleanyard/) ·
[Use this template](https://github.com/kausalflow/gleanyard-template/generate) ·
[Source registry](https://kausalflow.github.io/gleanyard-registry/) ·
[Live example](https://github.com/kausalflow/berlin-family-events)

Gleanyard collects structured data from recurring web sources (calendars, feeds, JSON APIs,
web pages) into a schema you define, in a GitHub repo you own.

1. **Describe what you want.** `schema.yaml` lists the fields of one record; `research.md`
   says what to look for.
2. **Let an assistant set sources up.** It finds candidates, reads each site's terms,
   writes one small config file per source, tries the extraction modes in order and pins
   the first that passes. Every change arrives as a pull request you review.
3. **Scheduled runs need no model.** GitHub Actions collect, validate every record and
   commit the data, a rights-filtered export and `status.json`. A source that breaks or
   goes quiet opens an issue, and an opt-in workflow drafts the fix as a pull request.

[kausalflow/berlin-family-events](https://github.com/kausalflow/berlin-family-events) runs
this way: three Berlin family event calendars, collected daily into one schema.

## Try it without installing anything

- **Start a project:** [use the template](https://github.com/kausalflow/gleanyard-template/generate),
  then open it in [Claude Code on the web](https://claude.ai/code) or a Codespace and ask
  it to research sources.
- **Ask from any AI app:** add `https://gleanyard-connector.leima.workers.dev/mcp` as a
  custom connector in claude.ai, ChatGPT or another MCP client. It searches the source
  registry and reads public projects' records and health, read-only.
- **In your own workflows:** `uses: kausalflow/gleanyard@v0.14.0`.

See [Use without installing](https://kausalflow.github.io/gleanyard/concepts/no-install/).
With Python: `pip install gleanyard && gleanyard init my-project`; in Claude Code:
`/plugin install gleanyard --marketplace kausalflow/gleanyard`.

## Why it looks like this

- **AI at setup and repair only.** Feeds and pages are mapped once; scheduled runs are
  deterministic, cheap and reviewable.
- **One schema is the contract.** A record that fails validation goes to review, never to
  the output.
- **Rights are part of the config.** Each source records what you may store and publish,
  with the terms you read as evidence; the export re-checks it on every run.
- **Polite by default.** robots.txt, request delays, byte and request limits, conditional
  requests and a raw cache; no evasion of bot protection.

## A project repo

```text
gleanyard.yaml        # optional project settings
schema.yaml           # the output schema every record must meet
sources/<id>.yaml     # one file per source: delivery + extract
mappings/<id>.json    # crawl4ai selector schemas for selectors mode
```

See [`examples/events`](examples/events) for a worked example.

A source file keeps two settings apart: `delivery` says how data arrives (`html`,
`ical`, `json`, `rss`, `csv`, `pdf`, `sitemap`) and `extract` says how it maps to the
schema (`fields`, `jsonld`, `selectors`, `llm`). The extraction mode is pinned per
source; scheduled runs never switch it on their own.

## Schema validation

Every record is normalized and checked against `schema.yaml`:

- a missing or invalid **required** field rejects the record;
- a value outside a field's `scope` (dates before `today`, a postcode pattern, an
  `enum`) rejects the record, so wrong or irrelevant pages show up at once;
- an invalid **optional** field is dropped and reported as a warning;
- a key that is not in the schema rejects the record.

Rejected records go to review, never to the output.

## Commands

```sh
gleanyard init DIR --schema NAME        # a new project repo
gleanyard validate [PROJECT] [--json]   # check schema and sources, with file:line fixes
gleanyard json-schema source            # JSON Schema for editors and AI tools
gleanyard collect [PROJECT] [-s ID] [--from-cache] [--json]
gleanyard export [PROJECT] [--json]     # rights-filtered public export
gleanyard test [PROJECT] [--update]     # replay fixtures offline against their snapshots
gleanyard terms [PROJECT]               # have a source's terms or robots.txt changed?
gleanyard badge [PROJECT]               # README badges from status.json
gleanyard search [QUERY]                # recipes in the public source registry
gleanyard add RECIPE [PROJECT]          # install one, disabled and with rights pending
```

`collect` fetches each enabled source with the bounded fetcher (same hosts only,
robots.txt, request delay, request and byte limits), keeps raw bodies in a
content-addressed cache with conditional requests, and writes records and
rejections under `data/`. `--from-cache` re-extracts the last run with no network.
`export` checks the *current* source rights: a field is exported only if it is in
`public_fields` and its class is allowed (`fact`, `protected_text`, `image`), and
`personal` fields are never exported. The owner's rights review is a suggestion: a pending
source is collected and exported by its stated tier, and only `rejected` or `blocked` stops
it (`rights_policy.require_reviewed_permission: true` restores the strict rule). Set
`export_csv` for a CSV copy, or `export: null` for CSV only.

See the [command line reference](https://kausalflow.github.io/gleanyard/reference/cli/) for
every command.

## With an AI assistant

In Claude Code, install the gleanyard plugin. It brings the skill and the
`gleanyard mcp` server (started with [uv](https://docs.astral.sh/uv/)):

```text
/plugin install gleanyard --marketplace kausalflow/gleanyard
```

See [AI assistants](https://kausalflow.github.io/gleanyard/concepts/agents/) for other clients.

## Releasing

Publishing to PyPI is automatic through trusted publishing (no API token).

1. Bump `version` in `pyproject.toml` and merge to `main`.
2. Create a GitHub release with tag `v<version>`, e.g. `v0.3.0`.
3. The `Publish to PyPI` workflow builds the package and uploads it.
