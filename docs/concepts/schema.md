# Records and the schema

The schema is the contract between your sources and everything downstream. Each record
that leaves a source is normalized and checked against it.

## What happens to a record

1. Each field is **normalized** to its type: dates become ISO dates, `12,50 €` becomes
   `{"amount": "12.50", "currency": "EUR"}`, relative URLs become absolute ones, HTML tags
   and entities are removed from text.
2. Each value is checked against the field's **scope**: a date range, a pattern, or a list
   of allowed values.
3. The record gets a stable **id** built from the source and the schema's `identity`
   fields, compared without regard to case. The same event seen on two runs has the same id.

A record is **rejected** if:

- a required field is missing or cannot be normalized;
- any value is outside its scope, even on an optional field;
- it contains a key that is not a field in the schema.

An optional field that cannot be normalized is **dropped with a warning** and the record is
kept. Rejected records are written to `data/review/<source>.jsonl` with the reason for each
field, so you can see what a source is getting wrong.

!!! tip "Use scope to catch wrong pages"
    A source that suddenly returns records from the wrong region or in the past is usually
    broken or pointing at the wrong URL. A scope such as a postcode pattern or
    `min: today` makes that show up immediately as rejections instead of as bad data.

## Provenance

Every record written to `data/records/` carries where it came from:

```json
{
  "id": "rec_3f2a...",
  "values": {"title": "Jazz im Park", "start_date": "2026-10-12"},
  "provenance": {
    "source_id": "city-events",
    "url": "https://example.org/api/events",
    "retrieved_at": "2026-10-10T05:00:00Z",
    "raw_sha256": "9b1c...",
    "mode": "fields",
    "schema_version": 1
  }
}
```

`raw_sha256` points at the exact response in the [raw cache](raw-cache.md) the record was
extracted from.
