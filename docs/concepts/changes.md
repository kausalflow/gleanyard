# Changes and merged records

A collection is more useful when you can see what is new. Gleanyard keeps a log of what each
run added, changed or removed, publishes the part your rights allow as a feed, and can join
the same item found on two sites into one exported record.

## The change log

Every healthy `collect` compares a source's new records with the ones it stored last time,
by record id, and appends one line per difference to `data/changes/<date>.jsonl`:

```json
{"at": "2026-10-11T05:17:00Z", "source_id": "city-events", "change": "changed",
 "id": "rec_3f2a...", "fields": ["venue"], "record": {"values": {...}, "provenance": {...}}}
```

| `change` | Meaning |
| --- | --- |
| `added` | A record id that the last run did not have. |
| `changed` | The same record id with different values; `fields` names the ones that differ. |
| `removed` | A record id the last run had and this one does not. `record` is the last version. |

The record id comes from the schema's `identity` fields, so a change to one of those fields
shows as one record removed and another added.

Each stored record also carries `first_seen`, when a record with its id was first collected;
its latest `provenance.retrieved_at` is when it was last seen. The export includes both in
`_source`.

A run that fails is not a change. In particular, an empty run of a source that is not
`expect_empty: seasonal` keeps the last records instead of logging every record as removed.

Logs older than `storage.changes_days` (default 90) are deleted. The template's `collect`
workflow commits `data/changes/` with the records. List recent changes with
[`gleanyard changes`](../reference/cli.md).

## The public feed

`gleanyard export` also writes the last 30 days of changes as a
[JSON Feed](https://jsonfeed.org) to `exports/changes.json`, newest first and at most 500
items. Each item goes through the same [rights filter](rights.md) as the export, against the
current source config: it shows only exported fields, a source whose rights do not allow
export does not appear, and a change to fields that are not exported is left out. Set
`changes_feed: null` in `gleanyard.yaml` to turn it off, or another path to move it.

```json
{
  "id": "rec_3f2a...:changed:2026-10-11T05:17:00+00:00",
  "title": "Changed: Jazz im Park / 2026-10-12",
  "date_published": "2026-10-11T05:17:00+00:00",
  "url": "https://example.org/api/events",
  "_gleanyard": {"change": "changed", "fields": ["venue"], "record": {"id": "...", ...}}
}
```

Feed readers that understand JSON Feed can follow it directly. The `recent_changes` tool of
[`gleanyard mcp`](agents.md) reads the same feed.

## Merging records across sources

When two sites list the same event, the export would hold it twice. Name the fields that
identify the same item across sources with `merge` in `schema.yaml`:

```yaml
identity: [title, start_date]
merge: [title, start_date, venue]
```

At export, rows from different sources that agree on all `merge` fields, ignoring case and
extra spaces, become one row:

- Merging happens after each row went through its own source's rights filter, so a merged
  row only holds fields some contributing source may publish.
- Each field's value comes from the first source, in source order, that exports it.
- `_source` names that first source, and `_sources` lists every contributing source with its
  own record id.
- The merged row's id is built from the merge values, so it stays the same between exports.
- A row that lacks one of the `merge` fields is never merged.

Pick merge fields that are specific: a title alone joins unrelated events with the same name.
`merge` may not use `personal` fields, which never export. Stored records in `data/` stay one
per source; only the export is merged.

## One source per series

Statistics often come from several publishers: the same country's consumer prices from the
OECD and from the IMF. Mixing their rows month by month gives a series with breaks in it.
Name the fields that make a series with `prefer` in `schema.yaml`:

```yaml
identity: [indicator, country_code, period]
prefer:
  series: [indicator, country_code]
```

At export, before merging, each series is kept whole from the first source, in source order,
that exports any row of it; the other sources' rows of that series are dropped. A series only
one source has is kept from that source. A row that lacks one of the `series` fields is
always kept. Source order is the order of the files under `sources/`, so name the files to
put the preferred publisher first (`10-oecd-prices.yaml`, `20-imf-cpi.yaml`).

## Keeping history

Each collect replaces a source's stored records with the ones the run found, and logs the
ones it no longer finds as removed. A source that publishes only its latest values, such as
the last twelve months of a growing series, would lose its history that way. Set
`keep_removed: true` on that source and the records a run no longer finds stay stored and
exported; a run's record with the same id replaces the kept one, so a revised value wins.
The run's own counts, fill rates and health still describe only what it found.
