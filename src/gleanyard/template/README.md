# {{project}}

![collection status](badges/project.svg)

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
badges/                README badges from status.json (`gleanyard badge`)
exports/records.jsonl  the rights-filtered public export
CLAUDE.md              ground rules for an AI assistant
.claude/skills/        its procedures: research, set up, repair, terms, retire (`gleanyard skill`)
```

```bash
pip install gleanyard
gleanyard validate
gleanyard collect
gleanyard status
```

The `collect` workflow runs daily, commits `status.json`, the badges and the records, and
opens a `repair` issue when a source fails or is degraded. Once a week per source it also
reads the terms pages named in `evidence/` and robots.txt, and opens a `terms` issue when
their text changed. Changes to sources arrive as pull requests; the `validate` workflow checks
each one with `gleanyard validate` and replays the fixtures with `gleanyard test`.

A source's badge, for a table of sources: `![](badges/sources/<id>.svg)`.

## Automatic repair (opt-in)

The `repair` workflow runs after a `collect` run that failed. A coding agent (Claude Code)
follows the Repair procedure in the gleanyard skill and edits the broken source; then
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

## Working with an AI assistant

`CLAUDE.md` holds this repo's ground rules and imports the gleanyard skill
(`.claude/skills/gleanyard/SKILL.md`) with the procedures. The skill ships with the package:
after upgrading gleanyard, run `gleanyard skill` and commit the result. Assistants that speak
MCP can also use the project's commands as tools:

```bash
pip install 'gleanyard[mcp]'
claude mcp add gleanyard -- gleanyard mcp     # or any MCP client: command `gleanyard mcp`
```

To watch this project together with others, add the topic `gleanyard` to the repo on GitHub:
a [status hub](https://kausalflow.github.io/gleanyard/concepts/status-hub/) set up with
`gleanyard hub init --owner <you>` finds it by that topic.
