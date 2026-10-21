# Setting up a source

Setup is the one place where a person or an AI assistant makes choices; after that, scheduled
runs are deterministic. The commands below turn each choice into a check with a clear
pass or fail.

1. **Check** the host: `gleanyard check https://example.org/events`. Skip it if it is already
   a source, a candidate or excluded.
2. **Probe** it: `gleanyard probe https://example.org/events`. Take the first option that fits:
   a feed (`fields`, no model), then schema.org data (`jsonld`), then CSS selectors, then
   `llm`. A probe also lists the site's imprint and terms pages: read them before collecting,
   and set `rights` from what they say.
3. **Scaffold** a draft: `gleanyard scaffold https://example.org/events --id example-events --write`.
   Fill the gaps it reports (required fields it could not map).
4. **Selectors only**: write `mappings/<id>.json`. `gleanyard map example-events` drafts it
   with crawl4ai (install `gleanyard[map]` on your own machine, and set `send_to_model` in the
   source's rights, since the page goes to a model); gleanyard only runs the result and
   never needs crawl4ai installed.
5. **Try** it: `gleanyard try example-events`. Repeat until it prints `PASS`.
6. **Save a fixture**: one sample page at `tests/fixtures/example-events.html` (or `.json`,
   `.ics`, ...), then `gleanyard test -s example-events --update` writes its snapshot.
7. Open a pull request with `gleanyard try example-events --markdown` as its description, so
   the reviewer spot-checks sample records instead of reading YAML.

The mode that passes first is pinned in the source file. A scheduled run never switches modes
on its own; a repair that needs another mode is a pull request.

## Fixtures and snapshots

A fixture is one saved page per source, `tests/fixtures/<id>.<ext>`: small, public and free of
personal data. Its snapshot, `tests/fixtures/<id>.expected.json`, holds the records that page
gave when it was saved and the date it was judged on:

```json
{
  "today": "2026-10-21",
  "records": {
    "rec_3f2a9c1e5b7d4e8a9c21": {"title": "Puppet theatre", "start_date": "2026-10-25"}
  }
}
```

`gleanyard test` replays every fixture offline and compares. It needs no network and no
model, so the template's `validate` workflow runs it on every pull request: a mapping edit,
a schema change or a new gleanyard version that changes what a source gives shows up in review,
not in the next scheduled run. When a change is meant, refresh the snapshot in the same pull
request with `gleanyard test -s <id> --update`. A repair that fits a page's new layout
replaces the fixture with today's page and refreshes the snapshot; `repair check` requires it.
