# Quickstart

This builds a small project that collects events from a JSON feed. It uses a local file
so you can follow along without a real source; swap in a real URL when you have one.

## Install

```sh
pip install gleanyard
```

Gleanyard needs Python 3.11 or newer.

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

An enabled source must allow `private_store` and `normalize`; until you have reviewed a
source's terms, keep it `enabled: false`.

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

This writes `exports/records.jsonl`. Nothing is exported until the source's rights allow it:
the source must be approved, its tier exportable, and each field listed in `public_fields`.
See [Rights and export](concepts/rights.md).
