# Automatic research

The template's opt-in `research` workflow runs the Research procedure unattended: a coding
agent searches the web for pages that publish what `schema.yaml` describes and proposes them
as new candidates in a pull request. Nothing is set up or enabled; you mark the candidates
worth setting up, as with research you ask for by hand.

Like [automatic repair](auto-repair.md), the workflow pairs the agent (Claude Code, through
`anthropics/claude-code-action`) with a command that decides without a model.

## What happens

1. You start the workflow from the Actions tab, optionally with a focus for this run (a
   district, a kind of page) and the most candidates to propose (default 5). With the
   repository variable `GLEANYARD_RESEARCH` set to `on`, it also runs on the first of each
   month.
2. The agent follows the ground rules in `CLAUDE.md` and the Research procedure in the
   [gleanyard skill](agents.md): it reads `research.md` and `schema.yaml`, searches the web,
   and runs `gleanyard check` and `gleanyard probe` on what it finds. Its tools are reading
   files, writing files, web search and those `gleanyard` commands, with a read-only GitHub
   token.
3. A second job, with no model, applies the new files and runs `gleanyard research check`.
4. If the check passes, the job opens a pull request labelled `research` with the agent's
   summary and the check's report.

Only one research pull request is open at a time; the workflow skips while one waits.

## What the check enforces

`gleanyard research check` compares the working tree with the base commit:

| Rule | Why |
| --- | --- |
| Only new files under `candidates/` named `<host>.yaml`. | Research proposes; setting up and changing sources is a separate, reviewed step. |
| Each file has an http(s) `url` on the host it is named for, and a `why`. | The owner needs to see what the page is and why it fits. |
| No candidate for a host that is already a source or in `excluded_hosts`. | You said no to it already, or have it. |
| At most the run's limit of candidates. | A short list gets reviewed. |

## Turn it on

1. Add the repository secret `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`), the same one
   automatic repair uses.
2. Run the `research` workflow from the Actions tab, or set the repository variable
   `GLEANYARD_RESEARCH` to `on` for the monthly run.
3. Optional: the repository variable `GLEANYARD_RESEARCH_MODEL` picks the model, and the
   secret `GLEANYARD_REPAIR_TOKEN` lets the pull request start other workflows, as for repair.

A project made before 0.7.0 gets the workflow by copying `.github/workflows/research.yml` from
a fresh `gleanyard init`.
