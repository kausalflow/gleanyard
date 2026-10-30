# AI assistants

Gleanyard runs no agent. It gives the one you use two things: procedures that match the
installed version, and the project's commands as tools. Both keep the same rule as the
command line: a verdict comes from `try`, `validate` and `repair check`, not from the model.

## The gleanyard skill

The procedures for research, setting up a source, repair and retirement live in one file that
ships with the package. `gleanyard init` writes it to `.claude/skills/gleanyard/SKILL.md`, and
the project's `CLAUDE.md` imports it, so Claude Code loads it in every session. Other
assistants can be pointed at the same file.

`CLAUDE.md` keeps only what belongs to the project: its ground rules, which you may tighten.
The procedures change with gleanyard, so after upgrading:

```bash
pip install -U gleanyard
gleanyard skill            # rewrites the skill if it differs from this version
git commit -am "Update the gleanyard skill"
```

The template's `validate` workflow runs `gleanyard skill --check` and warns, without failing,
when the copy in the repo is out of date. The [automatic repair](auto-repair.md) workflow
follows the skill's Repair procedure.

A project made before 0.5.0 has the procedures inside `CLAUDE.md`. Run `gleanyard skill`,
then replace the procedure sections of `CLAUDE.md` with the line
`@.claude/skills/gleanyard/SKILL.md`.

## The MCP server

`gleanyard mcp` serves one project to any MCP client over stdio:

```bash
pip install 'gleanyard[mcp]'
claude mcp add gleanyard -- gleanyard mcp     # Claude Code, from the project folder
```

Other clients start the command `gleanyard mcp` (optionally with the project folder as its
argument).

| Tool | Same as | Network |
| --- | --- | --- |
| `validate` | `gleanyard validate --json` | no |
| `status` | `gleanyard status --json` | no |
| `check_url` | `gleanyard check URL --json` | no |
| `probe` | `gleanyard probe URL --json` | yes |
| `scaffold` | `gleanyard scaffold URL --id ID --json` (never writes) | yes |
| `try_source` | `gleanyard try ID --json`, plus the `--markdown` report | yes |
| `repair_plan` | `gleanyard repair plan --json` | no |
| `repair_check` | `gleanyard repair check --json` | yes |
| `json_schema` | `gleanyard json-schema KIND` | no |
| `search_records` | text search over `exports/records.jsonl` | no |
| `search_registry` | `gleanyard search --json`, recipes in the [source registry](registry.md) | yes |

Every tool only reads. The server never writes to the repo, never runs `collect` and never
runs `map`: the agent edits files with its own tools, and you review the pull request.
`search_records` reads the rights-filtered public export, never the stored records in
`data/`, so an assistant sees only fields you may publish. The config is re-read on every
call, so the tools see the agent's edits.

The procedures are also served as prompts (`research`, `set_up_source`, `repair`,
`retire_source`, `add_from_registry`) and the whole skill as the resource `gleanyard://skill`, so a client without
the skill file still follows the procedures of the installed version.
