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

To watch this project together with others, add the topic `gleanyard` to the repo on GitHub:
a [status hub](https://kausalflow.github.io/gleanyard/concepts/status-hub/) set up with
`gleanyard hub init --owner <you>` finds it by that topic.
