# AI assistants

Gleanyard runs no agent. It gives the one you use two things: procedures that match the
installed version, and the project's commands as tools. Both keep the same rule as the
command line: a verdict comes from `try`, `validate` and `repair check`, not from the model.

## The plugin

The quickest way in for Claude Code is the gleanyard plugin. It comes from
the marketplace in the gleanyard repo and brings a short skill and the MCP server below:

```text
/plugin install gleanyard --marketplace kausalflow/gleanyard
```

On older Claude Code versions, run `/plugin marketplace add kausalflow/gleanyard`, then
`/plugin install gleanyard@gleanyard`.

The server starts in the folder you work in, through `uvx`, so it needs
[uv](https://docs.astral.sh/uv/) but no install of gleanyard. It runs with `--allow-writes`,
and the client asks you before each write tool runs. The plugin's skill holds no procedures
of its own: it sends the assistant to the project's `CLAUDE.md` and its versioned skill, so
the procedures always match the gleanyard version the project pins. In a folder without a
project, it offers `gleanyard init`.

Nothing here runs on a server of ours. The plugin is a few files in the repo, the server runs
on your computer, and pages are fetched from there.

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
argument). Claude Desktop, for example, takes this in its `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gleanyard": {
      "command": "uvx",
      "args": ["--from", "gleanyard[mcp]", "gleanyard", "mcp", "/path/to/project", "--allow-writes"]
    }
  }
}
```

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
| `read_file` | reads `schema.yaml`, `gleanyard.yaml`, `research.md`, `CLAUDE.md` and the review folders | no |

By default every tool only reads. The server never writes to the repo and never runs `map`:
an agent with a shell edits files with its own tools, and you review the pull request.
`read_file` cannot read `data/`. `search_records` reads the rights-filtered public export, never the stored records in
`data/`, so an assistant sees only fields you may publish. The config is re-read on every
call, so the tools see the agent's edits.

### Write tools

A client with no shell or file tools of its own, such as Claude Desktop, can still
follow the whole set-up and repair procedure when the server runs with `--allow-writes`:

| Tool | Same as | Network |
| --- | --- | --- |
| `write_file` | editing a file under `sources/`, `mappings/`, `candidates/`, `evidence/` or `tests/fixtures/`, then `validate` | no |
| `save_fixture` | `gleanyard try ID --save-pages`, keeping the first page as `tests/fixtures/<id>.<ext>` | yes |
| `update_snapshot` | `gleanyard test -s ID --update [--today DATE]` | no |
| `add_recipe` | `gleanyard add RECIPE [--id ID]` | yes |
| `collect` | `gleanyard collect -s ID`, for an enabled source only | yes |

What stays with you:

- `write_file` refuses a source file that changes `enabled` or `rights.review_status`, and a
  new source must be disabled and pending. The assistant may fill the rest of `rights` from
  the terms it read, as the procedures say.
- Nothing writes `data/`, `exports/` or `status.json` except `collect`, and `collect`
  refuses a disabled source, so nothing is stored before you have reviewed the rights.
- The tools change files in the folder; nothing commits, pushes or opens a pull request.

The procedures are also served as prompts (`research`, `set_up_source`, `repair`,
`retire_source`, `add_from_registry`) and the whole skill as the resource `gleanyard://skill`, so a client without
the skill file still follows the procedures of the installed version.
