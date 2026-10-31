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
    send_to_model: false           # allow `llm` mode to send page text to a model provider
    redistribute_facts: true       # export fact fields
    redistribute_text: false       # export protected text
    redistribute_images: false     # export images
  public_fields: [title, start_date, venue, source_url]
  attribution_required: false
  expires_at: 2027-10-01
  notes: Free-text evidence for the decision.
```

Everything defaults to the safe answer: unknown tier, nothing allowed, nothing public.
`gleanyard rights review` asks the owner for these values source by source, with suggestions
from the evidence and the tier, and writes them; see the [command line](../reference/cli.md).

## The review is a suggestion

The owner's review is recommended, not required. An enabled source whose review is still
`pending` is collected, and exported with the tier and `public_fields` its file states. Only an
explicit no stops a source: `review_status: rejected` or the tier `blocked` stops collecting
and exporting, and a tier outside `permitted_tiers` (by default `internal_only` and
`unknown`) is collected but never exported.

A project that wants the stricter rule sets it in `gleanyard.yaml`:

```yaml
rights_policy:
  require_reviewed_permission: true
```

Then a source is collected only when its rights allow `private_store` and `normalize`
(`validate` refuses to enable it otherwise), exported only once approved, and an open tier
also needs a `license_id` and `license_url`.

## Tiers

`open`, `open_attribution`, `licensed`, `facts_only`, `link_only`, `internal_only`,
`unknown` and `blocked`. By default the first five can be exported; the rest never are.
Open tiers should name a `license_id` and `license_url`, so the export can carry them. Change
the exportable tiers or the approval requirement under `rights_policy` in `gleanyard.yaml`.

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

- the source still exists, its review is not `rejected` (and is `approved`, in strict
  mode), it is in an exportable tier and its review has not expired;
- attribution text is set when attribution is required;
- every **required** schema field can be exported for that source.

Within an exported record, a field is included only if it is listed in `public_fields` and
its class is allowed. Each row carries its source, retrieval time, extraction mode and
licence or attribution in `_source`.

## Watching the terms

The rights of a source rest on the pages someone read at setup. Each review is saved as
`evidence/<id>/<date>-terms.yaml`:

```yaml
source: city-events
read_at: 2026-10-10
pages:
  - url: https://city.example/impressum
    quote: Die Inhalte dieser Website sind urheberrechtlich geschuetzt.
reading: Facts only; no descriptions or images in the export.
suggested_tier: facts_only
```

`gleanyard terms` fetches each page listed under `pages`, plus the source host's
`robots.txt`, and keeps a fingerprint of the visible text in `status.json` under the source's
`terms`. Markup, scripts, navigation and spacing do not count; the wording does. The
template's `collect` workflow runs it every day, and it checks each source at most once a week.

Sources that share one site and one review, such as many towns served by the same app, keep
that review once and name its folder in each source file with `evidence: <folder>`:
`evidence/<folder>/<date>-terms.yaml`. `gleanyard validate` checks that the folder exists, the
pages are read once per check, and a change raises one `terms` issue and one hub alert that
list each changed page with the sources that rely on it. The review's date is `read_at` (or
`checked_at`), or else the date in the file name.

When a page's text differs from the review, the source is flagged: `gleanyard terms` exits 1,
the `collect` workflow opens a `terms` issue, the source's badge reads `terms changed` and the
status hub raises a `terms_changed` alert. The flag stays, even if the page changes back,
until someone reads the pages again and adds a newer evidence file; that review becomes the
new baseline. A terms change never marks a source `degraded` and never starts an automatic
repair: what the new terms allow is the owner's decision. A `robots.txt` that appears where
there was none counts as a change; a page that cannot be read is reported, not flagged.

!!! warning "Rights notes are not legal advice"
    The checks make your decisions enforceable; they cannot make the decisions for you.
    Read a site's terms before you enable a source.
