# Health and status.json

Every `gleanyard collect` that fetches writes `status.json` at the project root (change
the path with `status` in `gleanyard.yaml`). Commit it: the status hub reads it from each
project repo to build a dashboard and to raise alerts, and `gleanyard status` prints it.
A `--from-cache` run does not touch it, because nothing was checked against the live source.

## Health of a source

| Health | Meaning |
| --- | --- |
| `ok` | The run produced records, or produced none on a source marked `expect_empty: seasonal`. |
| `degraded` | The run worked, but one of the checks below found it worse than the last good run. |
| `failing` | The run raised an error (`transport`, `structure_changed`, `blocked`, ...) or found nothing on a source that should have records. |
| `disabled` | The source has `enabled: false`. |

Sources that were not part of a run (`collect -s`) keep their previous entry.

## Checks on a run that worked

Sites rarely break with an error. More often a redesign leaves the page loading fine while a
selector matches nothing, or a date format changes and every date fails to parse. Each run
that works is compared with the source's baseline, the last run without warnings, and every
check that trips adds an entry to `warnings` and makes the source `degraded`:

| Check | Trips when |
| --- | --- |
| `record_drop` | The run found over 25% fewer records than the baseline. |
| `fill_rate_drop` | A field that at least half of the records carried now fills half as often or less, or is gone. |
| `rejection_spike` | The share of rejected records rose by 20 percentage points or more (and at least 3 were rejected). |
| `unchanged` | The records have not changed for `expect_change_days` days. Off unless the source sets it. |

The record and fill-rate checks only apply to sources with at least 10 records, since a
source with three events swings too much from day to day to judge. A `degraded` source keeps
its old baseline, so a source that stays broken does not turn `ok`
by itself; it turns `ok` again once a run passes every check, and that run becomes the new
baseline. Set `expect_change_days` on sources that should change often, such as an event
calendar: a feed that returns the same records for weeks is usually frozen, not quiet.

## What is in the file

```json
{
  "version": 1,
  "generated_at": "2026-10-10T06:00:00Z",
  "schema_name": "event",
  "healthy": false,
  "sources": [
    {
      "id": "city-events",
      "enabled": true,
      "health": "failing",
      "checked_at": "2026-10-10T06:00:00Z",
      "last_success_at": "2026-10-09T06:00:00Z",
      "consecutive_failures": 1,
      "records": 120,
      "rejections": 0,
      "field_fill_rates": {},
      "warnings": [],
      "baseline_fill_rates": {"title": 1.0, "start_date": 0.98},
      "baseline_rejection_rate": 0.0,
      "records_changed_at": "2026-10-09T06:00:00Z",
      "error_kind": "transport",
      "error": "city-events: connection failed"
    }
  ]
}
```

`terms`, written by `gleanyard terms`, holds the fingerprints of the source's terms pages and
`robots.txt` as of its last review, and lists under `changed` the pages whose text differs
since ([watching the terms](rights.md#watching-the-terms)). `gleanyard badge` draws README
badges from this file.

`records` is the last good count while a source is failing. Each warning has a `kind`, a
readable `message` and, for `fill_rate_drop`, the `field`. `field_fill_rates` shows the share
of records that carry each field, so a selector that silently stops matching shows as a rate
falling to zero. `last_success_at` and `consecutive_failures` let the hub alert on a source that
has been down for days, and a missed scheduled run shows as a `generated_at` that stops moving.
