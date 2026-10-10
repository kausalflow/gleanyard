# Roadmap

Gleanyard is built in phases. A phase starts once the previous one works.

## 1. Package core (done)

Project schema and normalizers, strict source config, `validate`, the bounded fetcher, the
content-addressed raw cache, feed readers (iCal, JSON, RSS/Atom, CSV, sitemap), `fields`
extraction, `collect`, `collect --from-cache` and the rights-filtered `export`.

## 2. Extraction modes

`jsonld` for pages with schema.org markup, `selectors` using crawl4ai-style JSON CSS
schemas, `llm` with per-run cost and page caps, and PDF delivery. Per-source health
(`status.json`) with expected-empty handling. Pages that need paging or filters bring
`pagination_changed` and `filter_ignored` errors.

## 3. Project template

A repo template whose `CLAUDE.md` tells an AI assistant how to research sources, set one up
(trying extraction modes in order and pinning the first that passes), and repair it when
health checks fail. Every change arrives as a pull request. crawl4ai drafts selector
mappings at setup and repair only and never runs in scheduled collection.

## 4. Status hub

A central repo that reads each project's `status.json`, publishes a dashboard on GitHub
Pages, and opens GitHub issues on changes: a source starts failing, record counts drop, a
scheduled run is missed, or a setup or repair PR waits too long.

## 5. Second project

A new project made from the template with its own schema, to check that nothing in the
package is specific to the first one.

## Decisions so far

- Recurring sources are the focus; one-off research is possible through `llm` mode but not
  optimized for.
- The extraction mode is chosen at setup and repair, then pinned. A scheduled run never
  switches modes.
- The model layer is provider-agnostic.
- Alerts are GitHub issues.
