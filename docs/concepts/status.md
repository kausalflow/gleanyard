# Health and status.json

Every `gleanyard collect` that fetches writes `status.json` at the project root (change
the path with `status` in `gleanyard.yaml`). Commit it: the status hub reads it from each
project repo to build a dashboard and to raise alerts, and `gleanyard status` prints it.
A `--from-cache` run does not touch it, because nothing was checked against the live source.

## Health of a source

| Health | Meaning |
| --- | --- |
| `ok` | The run produced records, or produced none on a source marked `expect_empty: seasonal`. |
| `degraded` | The run worked, but found under half of the last good record count. |
| `failing` | The run raised an error (`transport`, `structure_changed`, `blocked`, ...) or found nothing on a source that should have records. |
| `disabled` | The source has `enabled: false`. |

A `degraded` source keeps comparing against the count from before the drop, so a source that
stays at a third of its old size does not turn `ok` by itself. Sources that were not part of
a run (`collect -s`) keep their previous entry.

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
      "error_kind": "transport",
      "error": "city-events: connection failed"
    }
  ]
}
```

`records` is the last good count while a source is failing. `field_fill_rates` shows the share
of records that carry each field, so a selector that silently stops matching shows as a rate
falling to zero. `last_success_at` and `consecutive_failures` let the hub alert on a source that
has been down for days, and a missed scheduled run shows as a `generated_at` that stops moving.
