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
| `sitemap` | lists page URLs for the document modes | Reader available |
| `html` | `jsonld`, `selectors`, `llm` | Planned |
| `pdf` | `llm` | Planned |

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

Dot paths reach into nested JSON: `offers.0.price` is the price of the first offer.

### What each reader gives you

- **iCal**: keys are the iCalendar property names (`SUMMARY`, `DTSTART`, `DTEND`,
  `LOCATION`, `URL`, `CATEGORIES`, ...). Times are converted to the reader's `timezone`,
  all-day `DTEND` is made inclusive, and `TIME` holds text such as `19:30–21:30`. Recurring
  events are expanded into one record per occurrence, from `past_days` ago to
  `horizon_days` ahead, and never silently cut off.
- **JSON**: the records at `items_path`, each an object.
- **CSV**: one record per row, keyed by the header.
- **RSS and Atom**: `title`, `link`, `description`, `category` and the other element names.

## The extraction mode is pinned

The mode in a source file is the one that was chosen when the source was set up. A
scheduled run never tries another mode, never falls back and never switches on its own, so
all records from one source come from one method and a breakage shows on the first bad run.
Changing the mode is a deliberate edit to the source file, made in a pull request.

Every record keeps the mode that produced it in its `provenance`.
