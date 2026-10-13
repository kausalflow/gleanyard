# {{project}}

A [gleanyard](https://github.com/kausalflow/gleanyard) collection project: recurring sources
turned into records that follow `schema.yaml`.

```
schema.yaml            the contract every record meets
research.md            the idea and scope
sources/<id>.yaml      one file per source (delivery + extract + rights)
mappings/<id>.json     CSS selector mappings, for sources that need them
candidates/            sources found by research, not set up yet
evidence/              rights and terms evidence per source
tests/fixtures/        saved pages that guard a source against regressions
status.json            health report, written by every scheduled run
exports/records.jsonl  the rights-filtered public export
CLAUDE.md              how an AI assistant researches, sets up and repairs sources
```

```bash
pip install gleanyard
gleanyard validate
gleanyard collect
gleanyard status
```

The `collect` workflow runs daily, commits `status.json` and the records, and opens a
`repair` issue when a source fails or is degraded. Changes to sources arrive as pull requests.

## Automatic repair (opt-in)

The `repair` workflow runs after a `collect` run that failed. A coding agent (Claude Code)
follows the Repair procedure in `CLAUDE.md` and edits the broken source; then
`gleanyard repair check`, which runs no model, decides whether the edit becomes a pull request.
It must pass `try`, match the last good run, and leave each source's url, rights, limits and
`enabled` alone. A fix that does not fully pass opens as a draft; a source that is `blocked`
or failing for rights is left to you.

It does nothing until you turn it on:

1. Add the repository secret `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`).
2. In **Settings → Actions → General**, allow GitHub Actions to create pull requests.
3. Optional: pull requests opened with the workflow's own token do not start the `validate`
   workflow. To have them checked, add a fine-grained token with contents and pull requests
   write access as `GLEANYARD_REPAIR_TOKEN`.
4. Optional: set the repository variable `GLEANYARD_REPAIR_MODEL` to choose the model.

Each run costs model usage. Only one repair pull request is open at a time.

To watch this project together with others, add the topic `gleanyard` to the repo on GitHub:
a [status hub](https://kausalflow.github.io/gleanyard/concepts/status-hub/) set up with
`gleanyard hub init --owner <you>` finds it by that topic.
