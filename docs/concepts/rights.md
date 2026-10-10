# Rights and export

Collecting data and republishing it are different permissions. Each source declares both,
and export checks the **current** source file, not what was true when a record was
collected. Withdrawing a permission removes the records from the next export.

## Declaring rights

```yaml
rights:
  review_status: approved          # pending | approved | rejected
  effective_tier: facts_only
  allowed_actions:
    private_store: true            # keep raw pages and records
    normalize: true                # turn them into records
    redistribute_facts: true       # export fact fields
    redistribute_text: false       # export protected text
    redistribute_images: false     # export images
  public_fields: [title, start_date, venue, source_url]
  attribution_required: false
  expires_at: 2027-10-01
  notes: Free-text evidence for the decision.
```

Everything defaults to the safe answer: unknown tier, nothing allowed, nothing public.

## Tiers

`open`, `open_attribution`, `licensed`, `facts_only`, `link_only`, `internal_only`,
`unknown` and `blocked`. By default the first five can be exported; the rest never are.
Open tiers also need a `license_id` and `license_url`. Change the exportable tiers or the
approval requirement under `rights_policy` in `gleanyard.yaml`.

## Field classes

Each schema field has a class that decides which permission governs it:

| Class | Needs | Examples |
| --- | --- | --- |
| `fact` (default) | `redistribute_facts` | title, date, venue |
| `protected_text` | `redistribute_text` | descriptions |
| `image` | `redistribute_images` | image URLs |
| `personal` | never exported | contact emails |

## What export does

`gleanyard export` writes a record only if all of these hold:

- the source still exists, is approved, is in an exportable tier and its review has not
  expired;
- attribution text is set when attribution is required;
- every **required** schema field can be exported for that source.

Within an exported record, a field is included only if it is listed in `public_fields` and
its class is allowed. Each row carries its source, retrieval time, extraction mode and
licence or attribution in `_source`.

!!! warning "Rights notes are not legal advice"
    The checks make your decisions enforceable; they cannot make the decisions for you.
    Read a site's terms before you enable a source.
