# The repo template

Each collection project is its own repository. `gleanyard init` writes the starting point:

```bash
pip install gleanyard
gleanyard init my-events --schema event
cd my-events && git init && gleanyard validate
```

| File | Purpose |
| --- | --- |
| `schema.yaml` | A starter schema (title, source URL, description) for you to replace. |
| `research.md` | The idea and scope; the assistant reads it before proposing sources. |
| `gleanyard.yaml` | Project settings: user agent, excluded hosts, rights policy, [DataHerb](dataherb.md) listing. |
| `sources/`, `mappings/`, `candidates/`, `evidence/`, `tests/fixtures/` | Where the assistant's pull requests put their work. |
| `CLAUDE.md` | Ground rules for an AI assistant; imports the gleanyard skill. |
| `.claude/skills/gleanyard/SKILL.md` | The procedures: research, set up, repair, retire. Versioned with the package; see [AI assistants](agents.md). |
| `.github/workflows/collect.yml` | Daily collect and export; commits `status.json`, the badges and the records; checks the terms weekly. |
| `.github/workflows/validate.yml` | Runs `gleanyard validate` and `gleanyard test` on every pull request. |
| `.github/workflows/repair.yml` | Opt-in: drafts a fix for a broken source as a pull request ([automatic repair](auto-repair.md)). |
| `.github/workflows/research.yml` | Opt-in: proposes new candidates in a pull request ([automatic research](auto-research.md)). |
| `NOTICE` | Credits crawl4ai, which the setup procedure may use. |

## The assistant works through pull requests

`CLAUDE.md` and the gleanyard skill give the assistant procedures, each ending in a pull request that you
review, and a few rules that hold throughout: it never enables a source or approves rights, it
reads a site's terms before setup and saves what it found as evidence, it does not evade
robots.txt or bot protection, and it never edits generated data.

- **Research** finds candidate sources with `check` and `probe` and writes `candidates/`. The
  `research` workflow can run it unattended; see [automatic research](auto-research.md).
- **Set up a source** drafts the file with `scaffold`, tries the modes in order, pins the first
  one that makes `try` print `PASS`, and puts the `try --markdown` report in the pull request.
- **Repair** starts from a `repair` issue, reproduces with `try` and `collect --from-cache`,
  and fixes the smallest thing; switching the pinned mode is proposed, never automatic. The
  `repair` workflow can run it unattended; see [automatic repair](auto-repair.md).

## Scheduled runs

The `collect` workflow validates the config, collects, exports, commits `status.json`,
`data/records`, `data/review`, `data/changes` and `exports/`, and keeps the raw cache between runs with the
Actions cache. If any source is failing or `degraded` (see
[Health](status.md#checks-on-a-run-that-worked)) it opens (or comments on) one issue labelled
`repair` that lists those sources, then fails the run so it shows red. With
[automatic repair](auto-repair.md) turned on, the `repair` workflow then proposes a fix.
Sources that use `llm` need the provider's API key as a repository secret, passed to the
`collect` step as an environment variable; other repos need no secret.

GitHub disables scheduled workflows in a public repo after 60 days without commits. The
daily commit of `status.json` keeps the schedule alive, and a stalled `generated_at` in that
file is what the status hub watches for a missed run.

## crawl4ai is setup-only

The template never installs crawl4ai in a workflow. The assistant uses it on its own machine
through `gleanyard map` to draft a `mappings/<id>.json` selector file; the result is plain JSON that gleanyard runs
with lxml.
