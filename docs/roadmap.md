# Roadmap

Gleanyard is built in phases. A phase starts once the previous one works.

## 1. Package core (done)

Project schema and normalizers, strict source config, `validate`, the bounded fetcher, the
content-addressed raw cache, feed readers (iCal, JSON, RSS/Atom, CSV, sitemap), `fields`
extraction, `collect`, `collect --from-cache` and the rights-filtered `export`.

## 2. Extraction modes (done)

Done: `jsonld` for pages with schema.org markup, `selectors` using crawl4ai-style JSON CSS
schemas (both on single pages and sitemaps), per-source health in `status.json`, and `llm`
with per-run cost and page caps and a page-hash cache, and PDF delivery through `llm`. Paged
sources are supported through `pagination`, with `pagination_changed` when paging misbehaves. A
source `filter` guards URL filters and raises `filter_ignored` when the source stops honouring them.

## 3. Project template (done)

Done: the commands that turn setup into checks (`probe`, `check`, `scaffold`, `try`) and
`gleanyard init`, which writes the repo template. 
A repo template whose `CLAUDE.md` tells an AI assistant how to research sources, set one up
(trying extraction modes in order and pinning the first that passes), and repair it when
health checks fail. Every change arrives as a pull request. `gleanyard map` drafts selector
mappings with crawl4ai at setup and repair only; crawl4ai never runs in scheduled collection.

## 4. Status hub (done)

Done: `gleanyard hub run` reads the projects and keeps one issue per active problem,
`hub build` renders the dashboard and a job summary, `hub init` writes the hub repo, and
`hub check` says what its setup is missing. Projects are listed, found by topic, or read from a
status.json URL with no token. 
A central repo that reads each project's `status.json`, publishes a dashboard on GitHub
Pages, and opens GitHub issues on changes: a source starts failing, record counts drop, a
scheduled run is missed, or a setup or repair PR waits too long.

## 5. Second project (done)

`examples/datasets` is a catalog of open datasets made with `gleanyard init`: a different
schema (publisher, licence, formats, size) and three deliveries (a JSON API, an RSS feed and a
page with schema.org `Dataset` data). `tests/test_second_project.py` collects, exports by
rights and reports status on the unchanged package. It found two gaps that were not about
events and are fixed: list projections (`resources.*.format`) and RSS/HTTP dates.

`examples/berlin-family` is the first real project seed: family-friendly events in Berlin. It
holds the schema (age range, free or priced, indoor or outdoor, Berlin postcodes only) and a
research brief. Its sources are found with the setup procedure on a machine with network
access, in a repo of its own.

## Since then

- Automatic repair (0.4.0): the template's opt-in `repair` workflow lets a coding agent draft
  a fix for a broken source, and `gleanyard repair check` decides without a model whether it
  becomes a pull request.
- AI assistants (0.5.0): the procedures moved from `CLAUDE.md` into a gleanyard skill that
  ships with the package (`gleanyard skill`), and `gleanyard mcp` serves the project's
  commands as read-only tools to any MCP client.
- Checks between runs (0.6.0): `gleanyard test` replays fixtures offline against snapshots on
  every pull request, `gleanyard terms` notices when a source's terms or robots.txt change,
  `gleanyard badge` draws README badges, and the hub shows field fill rates against the
  baseline. 0.6.1 adds `test --update --today` for old fixtures and one shared terms review
  for many sources (`evidence:`), with one grouped alert.
- What changed (0.7.0): each collect logs added, changed and removed records, and export
  publishes them as a rights-filtered JSON Feed; a schema's `merge` fields join the same item
  from several sources into one exported row; the hub can show each project's export as a
  searchable table; and the opt-in `research` workflow proposes new candidates, checked by
  `gleanyard research check`.
- Source registry (0.8.0): `gleanyard add` installs a known-good recipe from a public
  registry, disabled and with its rights pending, and `gleanyard search` finds one. A
  registry repo checks recipes by replaying fixtures offline (`gleanyard registry check`),
  builds a static site for Pages (`registry build`), and shows each recipe's health from the
  public `status.json` of the projects that use it, so it costs nothing to run.
- AI apps (0.9.0): a Claude Code plugin, installed from the gleanyard repo's own
  marketplace, brings the skill and the MCP server; `gleanyard mcp --allow-writes` adds
  write tools for clients without a shell, which never enable a source or approve rights.

- Grouped pages and CSV (0.10.0): `explode` turns each element of a list in an item into its
  own record, selector fields can `split` text into lists, and `export_csv` writes the export
  as CSV too, listed in `dataherb.json`. Found while moving DataHerb's dataset scrapers onto
  gleanyard.

- Statistics APIs (0.11.0): `cases` set fields from an item's codes, `rewrite` fixes a value's
  format, `unpivot` turns wide
  CSV and JSON rows into one item per column, and `fetch.accept` asks an API for a format.
  The first part of moving DataHerb's economic indicators onto gleanyard.

- Large statistics (0.12.0): `prefer.series` keeps each series from one source,
  `keep_removed` keeps history a source no longer publishes, `storage.records: compact`
  shrinks stored records, and `export_partition` splits the export into files per value.

- Lookup tables (0.13.0): `join` adds columns from a CSV file in the repo (country names by
  code), `where` leaves out items by a regex, a `map` entry can list fallback keys, and the
  partitioned CSV is also written whole, with its parts in `export_csv_parts`.
  `export_csv_columns` and `export_sort` shape the CSV, and `export: null` makes it the only
  export. Found while matching the old economic indicators data exactly.
- Rights review as a suggestion (0.13.0): a pending review no longer stops collecting or
  exporting; only `rejected`, the tier `blocked` and non-exportable tiers do.
  `rights_policy.require_reviewed_permission: true` keeps the old fail-closed rule.

## Decisions so far

- Recurring sources are the focus; one-off research is possible through `llm` mode but not
  optimized for.
- The extraction mode is chosen at setup and repair, then pinned. A scheduled run never
  switches modes.
- The model layer is provider-agnostic.
- Alerts are GitHub issues.
