# Source file

One file per source at `sources/<id>.yaml`. The file name must match `id`. Print the JSON
Schema for editor completion with `gleanyard json-schema source`.

```yaml
id: aachen-city-events
name: Veranstaltungskalender der Stadt Aachen
schema: event
enabled: true
delivery: ical
url: https://www.aachen.de/kalender/veranstaltungskalender-alle-termine/event.ics
homepage: https://www.aachen.de/services/veranstaltungskalender/
reader:
  timezone: Europe/Berlin
extract:
  mode: fields
  map: {title: SUMMARY, start_date: DTSTART, source_url: URL}
  defaults: {city: Aachen}
fetch: {max_requests: 20, delay_seconds: 1}
expect_empty: never
coverage: {country: DE, city: Aachen, scope_type: municipality, scope_name: Aachen}
rights: {effective_tier: internal_only, allowed_actions: {private_store: true, normalize: true}}
retention: {raw_days: 30}
```

| Key | Meaning |
| --- | --- |
| `id` | Lower-case letters, digits and `-`. Must equal the file name. |
| `schema` | Must equal the project schema's `name`. |
| `enabled` | Default `false`. Only enabled sources run by default, and an enabled source must allow `private_store` and `normalize`. |
| `delivery` | `html`, `ical`, `json`, `rss`, `csv`, `pdf` or `sitemap`. |
| `url` | What to fetch. |
| `homepage` | Optional. Relative URLs resolve against it, and its host is also allowed for requests. |
| `reader` | Options for the delivery; see below. Unknown keys are errors. |
| `extract` | How to map onto the schema; see below. |
| `filter` | Optional. Check that a filter in the URL is still applied; see below. |
| `fetch` | Request limits. |
| `expect_empty` | `never` (default) or `seasonal`. A seasonal source with no records is healthy, not failing. |
| `coverage` | Where the source applies. Informational. |
| `rights` | See [Rights and export](../concepts/rights.md). |
| `retention` | `raw_days` (default 30) and `normalized_days` (default 730). |

## `reader` options

| Delivery | Option | Default |
| --- | --- | --- |
| `ical` | `timezone` | `UTC` |
| | `max_occurrences` | 5000 |
| | `past_days` | 7 |
| | `horizon_days` | 366 |
| `json` | `items_path` (dot path to the list) | the top level |
| `csv` | `delimiter` | `,` |
| | `encoding` | `utf-8` |
| `sitemap` | `include` (regular expression a URL must match) | all |
| | `max_pages` | 50 |
| `rss`, `html`, `pdf` | none | |

## `extract` options

`mode` picks the shape; each mode accepts `defaults`, constant values for schema fields the
source does not carry.

| Mode | Keys |
| --- | --- |
| `fields` | `map`: schema field to source key or dot path |
| `jsonld` | `types`: schema.org types to read; optional `map` |
| `selectors` | `selectors`: path to a crawl4ai JSON CSS schema, relative to the project |
| `llm` | `llm.model`, `llm.max_pages_per_run`, `llm.max_cost_per_run_eur`, `llm.only_if_changed`, `llm.input_price_per_mtok_eur`, `llm.output_price_per_mtok_eur`, `llm.max_input_chars`; optional `instructions` |

Each mode must fit the delivery: feeds (`ical`, `json`, `rss`, `csv`) use `fields`; `html` and
`sitemap` use `jsonld`, `selectors` or `llm`; `pdf` uses `llm`.

## `filter`

When the URL narrows the data (`?city=aachen`), a source can quietly stop honouring it and
return everything. `filter` states what should be inside and is checked on the extracted
records.

```yaml
filter: {field: city, one_of: [Aachen], max_outside_share: 0.2}
```

| Key | Meaning |
| --- | --- |
| `field` | Schema field the URL filter constrains. |
| `one_of` | Values that count as inside; strings ignore case. |
| `max_outside_share` | Default 0.2. When more of the records fall outside, the run fails with `filter_ignored`; fewer are moved to the review file. |

Records with no value for the field are not judged.

## `fetch` limits

| Key | Default |
| --- | --- |
| `max_requests` | 20 (includes `robots.txt`) |
| `max_response_bytes` | 5 MiB |
| `delay_seconds` | 1.0 |
| `timeout_seconds` | 30 |
