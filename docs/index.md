# gleanyard

Gleanyard collects structured data from recurring web sources into a schema you define.

You describe the records you want once, in `schema.yaml`. Each source, such as a calendar
feed, a JSON API or a web page, gets one small config file that says how its data arrives
and how it maps onto your schema. Scheduled runs then fetch the source politely, extract
records, check every one against the schema and write only the records that pass.

!!! note "Early release"
    The package core is in place: schema validation, a bounded fetcher, a raw-page cache,
    feed readers (iCal, JSON, RSS/Atom, CSV, sitemap), all four extraction modes (`fields`,
    `jsonld`, `selectors`, `llm`), HTML, sitemap and PDF delivery, a `status.json` health report
    and a rights-filtered export. The repo template and status hub come next; see the
    [roadmap](roadmap.md).

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
- [Delivery and extraction](concepts/delivery-and-extraction.md): the two settings every
  source has.
- [Command line](reference/cli.md): every command.
