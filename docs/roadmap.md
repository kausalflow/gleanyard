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

## Decisions so far

- Recurring sources are the focus; one-off research is possible through `llm` mode but not
  optimized for.
- The extraction mode is chosen at setup and repair, then pinned. A scheduled run never
  switches modes.
- The model layer is provider-agnostic.
- Alerts are GitHub issues.
