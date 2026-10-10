# Raw cache

Every response a source returns is kept, so extraction can be rerun, repaired and tested
without fetching again.

## How it is stored

```text
data/raw/objects/ab/abcd…        body, named by its SHA-256
data/raw/runs/<source>/<run>.json  what one run fetched, and the hash of each response
data/raw/validators/<source>.json  ETag and Last-Modified per URL
data/raw/good/<source>.json        the last healthy run
```

Identical pages are stored once. A run's manifest lists each URL it fetched and which body
it got.

## Conditional requests

The fetcher remembers each URL's `ETag` and `Last-Modified` and sends them back as
`If-None-Match` and `If-Modified-Since`. A `304 Not Modified` costs almost nothing and the
stored body is reused.

## Re-extracting from the cache

```sh
gleanyard collect --from-cache
```

reads the newest stored response for each source and extracts again with the current
schema and mapping. No request is made. Use it to test a new mapping or a schema change
against real pages before you commit it.

## Retention

Each source has `retention.raw_days` (default 30). Runs older than that are removed, and
then any body no remaining run refers to. The **last-good run is always kept**, however old,
so there is always a healthy page to compare against when a source breaks.

Raw bodies are stored only when a source's rights allow `private_store`. Otherwise the
fetcher works but keeps nothing.

## Fetch limits

The fetcher only requests the hosts of the source's `url` and `homepage`, including across
redirects. It reads `robots.txt`, waits between requests, retries server errors, and stops
at the limits in the source's `fetch` block (`max_requests`, `max_response_bytes`,
`delay_seconds`, `timeout_seconds`). See [Errors](../reference/errors.md) for what each
failure looks like.
