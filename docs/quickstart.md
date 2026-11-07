# Quickstart

This builds a small project that collects events from a JSON feed. It uses a local file
so you can follow along without a real source; swap in a real URL when you have one.

## Install

```sh
pip install gleanyard
```

Gleanyard needs Python 3.11 or newer.

!!! tip "Two shortcuts"
    `gleanyard init my-events --schema event` writes a whole project repo, with scheduled
    workflows and instructions for an AI assistant; see [the repo template](concepts/repo-template.md).
    If a working source for your site is already in the public registry, `gleanyard add`
    installs it; see [the source registry](concepts/registry.md). This page builds the same
    pieces by hand, so you see what each one does.

## Lay out the project

A project is a folder, usually a git repo:

```text
my-events/
├── schema.yaml
└── sources/
    └── city-events.yaml
```

## Describe the records

```yaml title="schema.yaml"
name: event
identity: [title, start_date]   # what makes one record different from another
fields:
  title:
    type: string
    required: true
  start_date:
    type: date
    required: true
    scope: {min: today}         # past events are out of scope
  venue:
    type: string
  source_url:
    type: url
    required: true
  price:
    type: money
    currency: EUR
```

## Describe a source

```yaml title="sources/city-events.yaml"
id: city-events                 # must match the file name
schema: event
enabled: true
delivery: json                  # how the data arrives
url: https://example.org/api/events
reader:
  items_path: data.events       # where the list of records is in the JSON
extract:
  mode: fields                  # how it maps onto the schema
  map:
    title: name
    start_date: starts_on
    venue: place.name
    source_url: link
    price: offers.0.price
rights:
  effective_tier: internal_only
  allowed_actions:
    private_store: true
    normalize: true
```

`internal_only` keeps the records to yourself: they are collected but never exported. Read
the site's terms before you collect, and keep the source `enabled: false` until you have.
`gleanyard rights review` asks you for the rights source by source and writes them.

## Check it

```sh
gleanyard validate
```

Problems are reported with the file, the line and the fix, for example:

```text
sources/city-events.yaml:13: extract.map.colour: 'colour' is not a field in schema 'event'; fields are: title, start_date, venue, source_url, price
```

Add `--json` for output a script or an AI assistant can read.

## Collect

```sh
gleanyard collect
```

For each enabled source this fetches the URL, stores the raw response, extracts records and
writes them under `data/`:

```text
data/raw/               every response, stored once under its hash
data/records/<id>.jsonl records that passed the schema
data/review/<id>.jsonl  records that were rejected, with the reasons
```

Run `gleanyard collect --from-cache` to extract again from the last stored response with no
network, for example after you change the mapping.

## Export

```sh
gleanyard export
```

This writes `exports/records.jsonl`. Only what the source's rights allow goes out: its tier
must be exportable, and only fields listed in `public_fields` are written. With the
`internal_only` tier above the export stays empty; set `effective_tier: facts_only`, allow
`redistribute_facts` and list `public_fields` once the terms allow it. Set `export_csv` in
`gleanyard.yaml` for a CSV copy, or `export: null` to write only the CSV. See
[Rights and export](concepts/rights.md) and the [`gleanyard.yaml` keys](reference/cli.md#gleanyardyaml).

## Next steps

- Run it on a schedule: [the repo template](concepts/repo-template.md).
- Know when a source breaks: [health and status.json](concepts/status.md).
- Hand setup and repair to an AI assistant: [AI assistants](concepts/agents.md).
