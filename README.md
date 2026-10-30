# gleanyard

**Documentation: https://kausalflow.github.io/gleanyard/**

Gleanyard collects structured data from recurring web sources into a schema you define.

Gleanyard is early and under active development. The design is in the v2 design doc;
the package holds only what every collection repo must get right the same way, and
everything that varies per project lives in that project's own repo.

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
`personal` fields are never exported.

## Releasing

Publishing to PyPI is automatic through trusted publishing (no API token).

1. Bump `version` in `pyproject.toml` and merge to `main`.
2. Create a GitHub release with tag `v<version>`, e.g. `v0.3.0`.
3. The `Publish to PyPI` workflow builds the package and uploads it.
