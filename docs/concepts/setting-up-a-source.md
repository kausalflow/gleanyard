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
4. **Selectors only**: write `mappings/<id>.json`. crawl4ai can draft it at setup
   time; gleanyard only runs the result and never needs crawl4ai installed.
5. **Try** it: `gleanyard try example-events`. Repeat until it prints `PASS`.
6. Open a pull request with `gleanyard try example-events --markdown` as its description, so
   the reviewer spot-checks sample records instead of reading YAML.

The mode that passes first is pinned in the source file. A scheduled run never switches modes
on its own; a repair that needs another mode is a pull request.
