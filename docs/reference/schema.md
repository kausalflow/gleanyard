# Schema file

`schema.yaml` at the project root. Print the JSON Schema for editor completion with
`gleanyard json-schema schema`.

```yaml
name: event               # lower-case letters, digits, - and _
version: 1                # recorded in each record's provenance
description: One dated public event.
identity: [title, start_date]
merge: [title, start_date, venue]   # optional: join the same item from several sources
fields:
  title: {type: string, required: true}
```

| Key | Meaning |
| --- | --- |
| `name` | The schema's name. Every source's `schema` must equal it. |
| `version` | Integer, default 1. Bump it when the meaning of fields changes. |
| `identity` | Fields that together identify one record in a source. At least one must be required. |
| `fields` | Field name (lower_snake_case) to a field definition. |
| `merge` | Optional. Fields that identify the same item across sources; export joins rows that agree on all of them. See [merging records](../concepts/changes.md#merging-records-across-sources). |
| `prefer` | Optional. `series:` lists the fields that name a series; export keeps each series from the first source that has it. See [one source per series](../concepts/changes.md#one-source-per-series). |

## Field definition

| Key | Default | Meaning |
| --- | --- | --- |
| `type` | `string` | One of the types below. |
| `required` | `false` | A missing or invalid value rejects the record. |
| `many` | `false` | The field holds a list; duplicates are removed. |
| `class` | `fact` | `fact`, `protected_text`, `image` or `personal`. See [Rights](../concepts/rights.md). |
| `max_length` | none | Longest allowed text. |
| `country` | none | `DE` enables German postcode rules. For `postcode`. |
| `currency` | none | Currency used when the value has none. For `money`. |
| `scope` | none | Bounds the value must fall inside. |
| `aliases` | none | Names this field goes by in sources; used by tooling to pre-fill mappings. |
| `description` | none | Free text for people and AI assistants. |

## Types

| Type | Accepts | Becomes |
| --- | --- | --- |
| `string` | any text | one line, tags and entities removed |
| `text` | any text | paragraphs kept |
| `date` | ISO dates and datetimes, RSS and HTTP dates, `12.10.2026`, `1.2.26` | `2026-10-12` |
| `datetime` | ISO datetimes and RSS or HTTP dates (`Tue, 01 Sep 2026 10:00:00 GMT`) | ISO datetime, offset kept; naive stays naive |
| `time_text` | `19:30`, `ab 18 Uhr` | text as written, 80 characters at most |
| `postcode` | `50667`, `D-50667` | digits only for `DE` |
| `url` | absolute or relative | absolute `http(s)` URL, resolved against the source page |
| `money` | `12,50 €`, `$1,234.50`, `Eintritt frei` | `{"amount": "12.50", "currency": "EUR"}`; the lowest amount of a range |
| `int`, `float` | numbers | numbers |
| `bool` | `true`, `ja`, `nein`, `0` | boolean |

## Scope

```yaml
start_date:
  type: date
  scope: {min: today-1, max: today+400}
postal_code:
  type: postcode
  country: DE
  scope: {pattern: "^(4[0-9]|5[0-3])"}
category:
  type: string
  scope: {enum: [music, art, sport]}
```

`min` and `max` apply to `date`, `datetime`, `int`, `float` and `money`. For dates they
accept an ISO date, `today`, or `today+N` / `today-N` in days. `pattern` is a regular
expression the value must contain a match for; `enum` lists the allowed values. A value
outside its scope rejects the record, even on an optional field.
