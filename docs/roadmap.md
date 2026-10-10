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
sources are supported through `pagination`, with `pagination_changed` when paging misbehaves.

## 3. Project template (in progress)

Done: the commands that turn setup into checks (`probe`, `check`, `scaffold`, `try`) and
`gleanyard init`, which writes the repo template. 
A repo template whose `CLAUDE.md` tells an AI assistant how to research sources, set one up
(trying extraction modes in order and pinning the first that passes), and repair it when
health checks fail. Every change arrives as a pull request. crawl4ai drafts selector
mappings at setup and repair only and never runs in scheduled collection.

## 4. Status hub (done)

Done: `gleanyard hub run` reads the repos and keeps one issue per active problem,
`hub build` renders the dashboard, and `hub init` writes the hub repo. 
A central repo that reads each project's `status.json`, publishes a dashboard on GitHub
Pages, and opens GitHub issues on changes: a source starts failing, record counts drop, a
scheduled run is missed, or a setup or repair PR waits too long.

## 5. Second project (done)

`examples/datasets` is a catalog of open datasets made with `gleanyard init`: a different
schema (publisher, licence, formats, size) and three deliveries (a JSON API, an RSS feed and a
page with schema.org `Dataset` data). `tests/test_second_project.py` collects, exports by
rights and reports status on the unchanged package. It found two gaps that were not about
events and are fixed: list projections (`resources.*.format`) and RSS/HTTP dates.

## Decisions so far

- Recurring sources are the focus; one-off research is possible through `llm` mode but not
  optimized for.
- The extraction mode is chosen at setup and repair, then pinned. A scheduled run never
  switches modes.
- The model layer is provider-agnostic.
- Alerts are GitHub issues.
