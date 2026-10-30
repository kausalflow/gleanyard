# DataHerb catalog

[DataHerb](https://dataherb.github.io) is a static data catalog: one YAML file points it
at git repos and S3 buckets, and it builds a site where people can search datasets, preview and
query them in the browser, and see which jobs are failing or stale. A gleanyard project can
list itself there with no extra code. Add a `dataherb` block to `gleanyard.yaml`. The repo
template already has one.

```yaml
dataherb:
  id: berlin-family-events   # dataset and job id; defaults to the folder name
  expected_interval: P1D     # how often collect runs, as an ISO 8601 duration
  tags: [berlin, family, events]
```

Every key is optional: `name` and `description` default to the schema's, `license` to the
one licence all exportable sources share, `uri` to the GitHub repo when running in Actions,
`owner`, `max_duration` (when a running job counts as stuck), and the paths `metadata`
(`dataherb.json`) and `status_dir` (`_dataherb/status`).

## What gets written

`gleanyard export` writes **`dataherb.json`**, the dataset metadata (DataHerb dataset spec
v2, a [Frictionless Data Package](https://datapackage.org) underneath). It lists the export
file with its row count, size and a schema built from `schema.yaml`: field types map to
Table Schema types, `id` is the primary key, and `_source` carries provenance. Personal data
never appears, because export never writes it. When no enabled source may be exported (for
example all are `internal_only`), the metadata lists no files and says `internal`, so the
catalog shows the dataset without offering a download. With `export_csv` set in
`gleanyard.yaml`, the CSV copy of the export is listed too, so a DataHerb dataset can keep
the CSV path its users already read.

`gleanyard collect` writes a **job status** (`dataherb.status/v1`) next to `status.json`:
`_dataherb/status/<id>/latest.json`, plus one file per run in `runs/`. It reads:

| DataHerb | From gleanyard |
| --- | --- |
| `run.status` | `success` when every source worked, `partial` when some failed, `failed` when all did |
| `run.id`, `run.url`, `run.trigger` | the GitHub Actions run, when there is one |
| `last_success` | carried over from the previous `latest.json` |
| `checks` | one per enabled source: `pass` when `ok`, `fail` when `degraded` or `failing`, plus a `warn` per quiet-breakage warning (`record_drop`, `fill_rate_drop`, ...) |
| `metrics` | sources, failing and degraded counts, records, rejections, pages, model calls, cost |

DataHerb turns a failed check into a degraded job, and a missing successful run within
`expected_interval` into a stale one, so the catalog shows the same health as the
[status hub](status-hub.md). `--from-cache` runs write nothing, as with `status.json`.

## Listing the repo in a catalog

The template's `collect` workflow commits `dataherb.json` and `_dataherb/`, so a DataHerb
catalog reads both from git. In the catalog repo, add one entry file for the dataset and the
job to the status sources in `dataherb.config.yml` (`github` is a store of `type: git`):

```yaml title="catalog/berlin-family-events.md"
---
id: berlin-family-events
repo: kausalflow/berlin-family-events
---
```

```yaml title="dataherb.config.yml"
status:
  sources:
    - store: github
      prefix: kausalflow/berlin-family-events@main:_dataherb/status
      jobs: [berlin-family-events]
```

A git store cannot list files, so the catalog shows the latest run; the `runs/` history is
read when the status folder is synced to S3 or served from a local folder.

To turn it off, delete the `dataherb` block; nothing else depends on it.
