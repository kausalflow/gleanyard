# gleanyard

Gleanyard collects structured data from recurring web sources into a schema you define.

You describe the records you want once, in `schema.yaml`. Each source, such as a calendar
feed, a JSON API or a web page, gets one small config file that says how its data arrives
and how it maps onto your schema. Scheduled runs then fetch the source politely, extract
records, check every one against the schema and write only the records that pass.

!!! note "Early release"
    Gleanyard is young and changes quickly; pin a minor version (`gleanyard~=0.13.0`) in your
    project. Everything below is released; see the [roadmap](roadmap.md) for what came when.

## What it does

- **Collect** from calendar feeds (iCal), JSON APIs, RSS/Atom, CSV, sitemaps, HTML pages and
  PDFs, with four extraction modes: `fields` for feeds, `jsonld` for schema.org markup,
  `selectors` for CSS selectors and `llm` when nothing else works. Feeds need no model, and
  a lookup table in the repo can fill in columns such as country names by code.
- **Check** every record against your schema, and every source against its last good run: a
  `status.json` health report, README badges, offline fixture replays on each pull request,
  and a watch on each site's terms and `robots.txt`.
- **Publish** a rights-filtered export as JSON lines or CSV (columns chosen, sorted, split per
  value), a change feed, and [DataHerb](concepts/dataherb.md) metadata.
- **Maintain** each project in its own repo made with `gleanyard init`: scheduled GitHub
  workflows, an opt-in [automatic repair](concepts/auto-repair.md) and
  [research](concepts/auto-research.md), and a [status hub](concepts/status-hub.md) that
  watches many projects.
- **Share and reuse** working sources through the public
  [source registry](concepts/registry.md): `gleanyard add` installs one in a single command.
- **Work with an AI assistant**: a Claude Code plugin, a versioned skill and an MCP server
  give the assistant the procedures and the commands; see
  [AI assistants](concepts/agents.md).

## Why it looks like this

- **Recurring sources, not one-off sweeps.** A model, when one is needed at all, is used
  once per source at setup and again only if the source breaks. Scheduled runs use the
  pinned extraction and need no model for feeds.
- **One schema is the contract.** Every record is normalized and checked against
  `schema.yaml`. A record that fails goes to a review file, never to the output.
- **A lean package, one repo per project.** The package holds what every project must get
  right the same way: safety limits, validation, extraction, caching and rights. Everything
  that varies lives in your project repo, where changes arrive as ordinary pull requests.
- **Rights are part of the data.** Each source declares what you may store and
  redistribute, and export re-checks that against the current config.

## Where to start

- [Quickstart](quickstart.md): a project with one source, from empty folder to export.
- [The repo template](concepts/repo-template.md): `gleanyard init` and the scheduled
  workflows, for a project that runs on its own.
- [Delivery and extraction](concepts/delivery-and-extraction.md): the two settings every
  source has.
- [AI assistants](concepts/agents.md): set up sources by asking Claude, without learning the
  config first.
- [Command line](reference/cli.md): every command.
