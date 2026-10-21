---
name: gleanyard
description: Research, set up, repair, review the terms of, or retire a source in a gleanyard collection project. Use for any change to sources/, mappings/, candidates/ or evidence/.
---

# Working in a gleanyard project

These procedures come with the gleanyard package, so they match the installed version. Read
the repo's `CLAUDE.md` first: its ground rules come before anything here. After upgrading
gleanyard, `gleanyard skill` refreshes this file.

When the gleanyard MCP server is connected (`gleanyard mcp`), its tools give the same results
as the commands below, as JSON.

## Commands

```bash
gleanyard validate                         # config check, with file:line messages
gleanyard check URL                        # new, candidate, source or excluded?
gleanyard probe URL                        # feeds, schema.org data, sitemaps, legal pages
gleanyard scaffold URL --id ID --write     # draft sources/ID.yaml
gleanyard try ID [--markdown]              # live dry run: PASS or FAIL, writes nothing
gleanyard try ID --save-pages DIR          # ... and keep today's pages to read
gleanyard test [-s ID]                     # replay fixtures offline against their snapshots
gleanyard test -s ID --update              # write the snapshot once the fixture passes
gleanyard terms [-s ID] [--force]          # have the terms or robots.txt changed?
gleanyard collect -s ID                    # real run for one source
gleanyard collect --from-cache -s ID       # re-extract the last cached pages, no network
gleanyard status                           # health of every source
gleanyard repair plan                      # which broken sources a repair may attempt
gleanyard repair check                     # may the uncommitted edit become a repair PR?
gleanyard json-schema source               # exact shape of a source file
gleanyard skill                            # refresh this file after upgrading gleanyard
```

Every command takes `--json`. A verdict comes from `try` and `validate`, not from your own
judgement of the output. `test` must pass too: a change that alters what a fixture gives is
either a bug or, when the change is meant, a reason to refresh the snapshot in the same PR.

## Procedure: Research

Trigger: the owner asks for sources, or a scheduled research run.

1. Read `research.md` and `schema.yaml`. Search the web for pages that publish what the schema
   describes. Prefer a feed (`.ics`, RSS, JSON API) over an HTML page.
2. For each URL run `gleanyard check URL`; skip hosts that are sources, candidates or excluded.
3. Run `gleanyard probe URL`. Write `candidates/<host>.yaml` with `url`, `why`, the best probe
   option, and the legal pages the probe found.
4. Open one PR with the new candidates. Do not set anything up yet.

## Procedure: Set up a source

Trigger: a candidate the owner marked ready.

1. `gleanyard scaffold URL --id <id> --write`, then fill what it reports as missing
   (required fields, `defaults` for constants such as a city).
2. Try the modes in order and pin the first that passes `gleanyard try <id>`:
   `fields` (feeds) → `jsonld` → `selectors` → `llm`. Each switch of mode is a new draft; do
   not leave a half-working mapping.
3. For `selectors`, draft `mappings/<id>.json` with crawl4ai (setup-time only, never in CI):
   ```bash
   pip install 'gleanyard[map]' && crawl4ai-setup
   gleanyard map <id>
   ```
   `map` needs the source's `rights.allowed_actions.send_to_model` (the page goes to a model
   provider). Rights are the owner's decision, so if it is not set, ask the owner rather than
   setting it yourself. It fetches one sample page,
   writes the mapping if it validates and finds records, and lists fields that are not in the
   schema or required fields it left uncovered. Edit by hand until `try` passes; use
   `--force` to redraft an existing mapping. The mapping format is documented at
   https://kausalflow.github.io/gleanyard/.
4. Save one fixture page to `tests/fixtures/<id>.<ext>` (small, public, no personal data),
   then `gleanyard test -s <id> --update` to write `tests/fixtures/<id>.expected.json`.
   Commit both.
5. Set `expect_empty: seasonal` only if the source is empty out of season. Set
   `expect_change_days` (for example 7) on sources whose records should change often, such as
   event calendars, so a frozen feed is noticed.
6. Fill `rights` from the terms you read; leave `review_status: pending` and `enabled: false`.
7. Open a PR whose description is the output of `gleanyard try <id> --markdown`, plus the
   evidence file and your reading of the terms. The owner reviews, sets rights and enables.

## Procedure: Repair

Trigger: a `repair` issue, or `gleanyard status` shows a `failing` or `degraded` source.
When the `repair` workflow is turned on, it runs this procedure unattended and opens the PR.

1. `gleanyard status --json` and the issue say what failed: `error_kind` for a failing source,
   `warnings` (`record_drop`, `fill_rate_drop`, `rejection_spike`, `unchanged`) for a degraded
   one. Read the source file.
2. Reproduce with `gleanyard try <id>`. Compare with the last good page:
   `gleanyard collect --from-cache -s <id>` re-extracts it without network.
3. Fix the smallest thing: selectors for `structure_changed`, a key for a feed, the
   `include` regex for a sitemap. `blocked` or a terms change is not yours to fix: report it.
   When the page layout changed, replace the fixture with today's page (from `--save-pages`)
   and refresh its snapshot with `gleanyard test -s <id> --update`.
4. If the pinned mode cannot be fixed, re-run the setup procedure's mode order and propose the
   switch in the PR, with evidence for why.
5. A repair PR passes only when `gleanyard repair check` prints `ready`: `try` passes, record
   counts and fill rates match the last good run, the fixture replays to its snapshot,
   `validate` is clean, and nothing outside sources, mappings, fixtures and evidence changed.
   Put its `--markdown` output in the PR.

## Procedure: Review changed terms

Trigger: a `terms` issue, or `gleanyard terms` prints `CHANGED` for a source.

1. `gleanyard status --json` lists the changed pages under the source's `terms.changed`. Read
   each page and the last `evidence/<id>/<date>-terms.yaml`.
2. Write a new `evidence/<id>/<today>-terms.yaml` with the same shape: every page you read,
   the relevant quote as it stands now, and your reading of what changed.
3. If the new terms allow less than the source's `rights` say, propose the narrower `rights`
   (or `enabled: false`) in the same PR and say why; the owner decides. If nothing that
   matters changed, say so.
4. Open the PR. The next `gleanyard terms` run takes the new evidence file as the baseline
   and clears the warning.

## Procedure: Retire a source

If a source is gone for good or its rights are withdrawn: set `enabled: false`, note why in the
source file and in `evidence/`, and open a PR. Do not delete its stored records.

## Pull request checklist

- [ ] `gleanyard validate` clean
- [ ] `gleanyard test` passes, with a snapshot for every fixture you added or changed
- [ ] `gleanyard try <id>` PASS, output in the description
- [ ] evidence file for the terms you read
- [ ] `rights` left for the owner to decide; `enabled` unchanged
