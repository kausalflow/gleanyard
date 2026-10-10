# Delivery and extraction

Every source file has two independent settings.

`delivery`
:   How the data arrives: `html`, `ical`, `json`, `rss`, `csv`, `pdf` or `sitemap`.
    This decides how the response is read.

`extract`
:   How what was read maps onto your schema: `fields`, `jsonld`, `selectors` or `llm`.

Keeping them apart means a feed and a web page for the same kind of record differ only in
their source file; the schema, validation and export are shared.

## Which modes fit which deliveries

| Delivery | Extraction modes | Status |
| --- | --- | --- |
| `ical`, `json`, `rss`, `csv` | `fields` | Available |
| `sitemap` | `jsonld`, `selectors`, `llm`: each listed page is fetched and extracted | Available |
| `html` | `jsonld`, `selectors`, `llm` | Available |
| `pdf` | `llm` | Available |

`gleanyard validate` rejects a combination that does not fit.

## `fields`: feeds, no model

Feeds already carry named values, so each schema field is mapped to a key or a dot path:

```yaml
delivery: ical
extract:
  mode: fields
  map:
    title: SUMMARY
    start_date: DTSTART
    venue: LOCATION
    source_url: URL
  defaults:
    city: Aachen            # constant values the source does not carry
```

Dot paths reach into nested JSON: `offers.0.price` is the price of the first offer, and `*` reads
every element of a list: `resources.*.format` is the list of formats of all resources, for a
field with `many: true`. `jsonld` mappings take the same paths.

### What each reader gives you

- **iCal**: keys are the iCalendar property names (`SUMMARY`, `DTSTART`, `DTEND`,
  `LOCATION`, `URL`, `CATEGORIES`, ...). Times are converted to the reader's `timezone`,
  all-day `DTEND` is made inclusive, and `TIME` holds text such as `19:30–21:30`. Recurring
  events are expanded into one record per occurrence, from `past_days` ago to
  `horizon_days` ahead, and never silently cut off.
- **JSON**: the records at `items_path`, each an object.
- **CSV**: one record per row, keyed by the header.
- **RSS and Atom**: `title`, `link`, `description`, `category` and the other element names.

## `jsonld`: pages with schema.org markup

Many event, product and organisation pages publish `<script type="application/ld+json">`
for search engines. List the `@type`s to read; nodes are found inside arrays, `@graph` and
nested objects, and a broken block is skipped without hiding the others.

```yaml
delivery: html
extract:
  mode: jsonld
  types: [Event]
  map:
    price: offers.price      # optional: only for fields whose name differs
```

A schema field with no `map` entry is looked up by its own name and then its `aliases`, so
`aliases: [startDate]` on `start_date` is enough. A list is read at its first element unless
you give an index, and `{"@value": ...}`, `{"name": ...}` and `{"url": ...}` wrappers are
unwrapped.

## `selectors`: CSS selectors, no model

`extract.selectors` points to a crawl4ai-style JSON CSS schema in `mappings/`. The same file
can be drafted by crawl4ai at setup and then runs here with plain HTML parsing.

```json
{
  "baseSelector": "table.table tr",
  "fields": [
    {"name": "title", "selector": "td:nth-child(4)"},
    {"name": "source_url", "selector": "a", "type": "attribute", "attribute": "href"}
  ]
}
```

`baseSelector` matches one element per candidate record. Field types are `text` (default),
`attribute`, `html`, `regex` (`pattern`, first group), `list`, `nested` and `nested_list`;
a field may have `default` and `transform` (`lowercase`, `uppercase`, `strip`). Header rows
and other rows that fail the schema land in the review file, not in the records.

## `llm`: a model reads the page

For pages with no markup worth selecting, a language model reads the page text and returns
records in your schema. Use it when `jsonld` and `selectors` do not fit; it costs money and
is the least predictable mode, so every guard below is on by default.

```yaml
delivery: html
extract:
  mode: llm
  llm:
    model: anthropic:claude-sonnet-5-5   # provider:model, anything pydantic-ai supports
    max_pages_per_run: 20                # model calls per run
    max_cost_per_run_eur: 0.5            # estimated from token usage and the prices below
    only_if_changed: true                # a page whose text did not change is not sent again
    input_price_per_mtok_eur: 5          # your provider's prices, to estimate cost
    output_price_per_mtok_eur: 25
  instructions: Only workshops, not guided tours.
  defaults: {city: Velbert}
rights:
  allowed_actions: {private_store: true, normalize: true, send_to_model: true}
```

- **PDF**: with `delivery: pdf` the file's text layer is sent to the model (`pip install
  "gleanyard[pdf]"`). A scanned PDF without a text layer fails with `structure_changed`;
  there is no OCR.
- **Install**: `pip install "gleanyard[llm]"` and set the provider's API key (for example
  `ANTHROPIC_API_KEY`) in the environment of the scheduled run.
- **Rights**: page text leaves your repo, so the source must allow `send_to_model`.
- **Validation**: the model's items go through the same schema checks as every other mode;
  items that fail land in the review file. The prompt tells the model to copy text as written,
  never to guess and to ignore instructions inside the page.
- **Caps**: reaching `max_pages_per_run` or the cost cap stops the run with `bounds_exceeded`
  instead of returning part of the data. Cost is estimated before each call.
- **Cache**: model output is stored in `data/llm/` keyed by the page text, model, schema fields
  and instructions. `only_if_changed` reuses it; `collect --from-cache` always does and never
  calls a model.

## Sitemaps

With `delivery: sitemap` the source URL is a sitemap. Gleanyard fetches each listed page
(filtered by `reader.include`, capped by `reader.max_pages` and the fetch limits) and
extracts it with `jsonld` or `selectors`. Relative links resolve against the page's own URL,
and a record found on several pages is kept once.

## The extraction mode is pinned

The mode in a source file is the one that was chosen when the source was set up. A
scheduled run never tries another mode, never falls back and never switches on its own, so
all records from one source come from one method and a breakage shows on the first bad run.
Changing the mode is a deliberate edit to the source file, made in a pull request.

Every record keeps the mode that produced it in its `provenance`.
