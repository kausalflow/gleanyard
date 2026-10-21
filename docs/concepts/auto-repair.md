# Automatic repair

When a source breaks, the [repo template](repo-template.md) opens a `repair` issue. With
automatic repair turned on, it also proposes the fix: a pull request you review and merge.

Gleanyard itself runs no agent. The template's `repair` workflow pairs a coding agent (Claude
Code, through `anthropics/claude-code-action`) with two commands that decide without a model,
so the agent can draft a fix but cannot decide that it is one.

## What happens

1. The `collect` run fails, because a source is `failing` or `degraded`
   (see [Health](status.md)).
2. `gleanyard repair plan` picks the sources an edit can fix: every `degraded` source, and
   `failing` ones with `structure_changed`, `empty`, `pagination_changed` or
   `filter_ignored`. A `blocked` source, a rights problem, a network error or a reached limit is
   left to you; the `repair` issue already lists it.
3. The agent follows the ground rules in `CLAUDE.md` and the Repair procedure in the
   [gleanyard skill](agents.md) for those sources. It has a read-only
   GitHub token and only these tools: reading and editing files, and the `gleanyard` commands
   `try`, `validate`, `status`, `collect --from-cache`, `json-schema` and `repair`. It reads
   today's page with `gleanyard try ID --save-pages DIR` and the last good one from the raw
   cache.
4. A second job, with no model, applies the edit and runs `gleanyard repair check`.
5. If the check passes, the job opens a pull request labelled `repair` with the agent's
   summary and the check's report, and links it from the `repair` issue. If every changed
   source passes, the pull request is ready for review; otherwise it opens as a draft. If the
   edit cannot be proposed, the report goes to the issue instead.

Only one repair pull request is open at a time; the workflow skips while one waits for review.

## What the check enforces

`gleanyard repair check` compares the working tree with the base commit:

| Rule | Why |
| --- | --- |
| Only `sources/`, `mappings/`, `tests/fixtures/` and `evidence/` may change; records, exports and `status.json` are left out. | A repair changes how a source is read, nothing else. |
| No new or deleted files under `sources/`, no deleted files at all. | Adding or retiring a source is the owner's decision. |
| A source keeps its `id`, `schema`, `enabled`, `delivery`, `url`, `homepage`, `fetch`, `coverage`, `rights` and `retention`. | A new host needs a rights review; limits protect the site. |
| No switch to `llm` mode; other mode switches are allowed and noted. | `llm` costs money on every run. |
| `gleanyard validate` is clean. | |
| Each changed source passes `try`, and has at least 75% of the last good run's records and keeps its fill rates (the `degraded` rules). | A fix must restore the source, not only make it pass. |

## Turning it on

The workflow is in every project made with `gleanyard init`. It does nothing until the
repository has the secret `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`). Then:

- allow GitHub Actions to create pull requests in **Settings → Actions → General**;
- optionally add `GLEANYARD_REPAIR_TOKEN`, a fine-grained token with contents and pull requests
  write access. Pull requests opened with the workflow's own token do not start other
  workflows, so `validate` only runs on them with this token;
- optionally set the variable `GLEANYARD_REPAIR_MODEL` to pick the model.

A project created before 0.4.0 can copy the workflow from a fresh template:

```bash
gleanyard init /tmp/template && cp /tmp/template/.github/workflows/repair.yml .github/workflows/
```

Then bump the `gleanyard~=` pin in the other workflows to the same version.

## Other agents

The agent step is one step in `.github/workflows/repair.yml`. Any agent that edits files in
the checkout can replace it; the plan and the check stay the same.
