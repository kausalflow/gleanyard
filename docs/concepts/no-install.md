# Use without installing

You can run a gleanyard project without installing anything on your computer. The project
lives in a GitHub repo, GitHub Actions run the collection, and an assistant in the cloud
sets sources up. There are four ways in, and you can combine them.

| Way in | What you get | Needs |
| --- | --- | --- |
| [Template repo](#start-from-the-template-repo) | A project repo in one click | A GitHub account |
| [Claude Code on the web](#set-sources-up-in-the-cloud) | An assistant that researches, sets up and repairs sources | A Claude account |
| [Codespaces](#or-open-it-in-codespaces) | An editor in the browser with gleanyard and Claude Code installed | GitHub Codespaces (free hours each month) |
| [The connector](#the-read-only-connector) | Registry search and published records inside any AI app | Nothing, it only reads |
| [The GitHub Action](#the-github-action) | gleanyard commands in your own workflows | A workflow file |

## Start from the template repo

[kausalflow/gleanyard-template](https://github.com/kausalflow/gleanyard-template) holds
what `gleanyard init` writes. Click **Use this template**, name the repo and create it. Keep
it public if the data may be public: Actions minutes are free for public repos.

Then, on github.com:

1. In `gleanyard.yaml`, replace `my-collection` with your repo's name and `OWNER` with your
   GitHub name, so sites can see who is fetching.
2. Write what you want to collect in `research.md` and the fields of one record in
   `schema.yaml`. The [schema reference](../reference/schema.md) lists the field types.

The template has the same workflows as any project: `validate` checks pull requests and
`collect` runs daily once a source is enabled. [Repair](auto-repair.md) and
[research](auto-research.md) stay off until you add a model key.

## Set sources up in the cloud

Open [Claude Code on the web](https://claude.ai/code), pick the repo and ask:

- "Research sources for this project." It reads `research.md` and `schema.yaml`, searches,
  probes each site and opens a pull request with candidates.
- "Set up the candidate for example.org." It writes the source, tries the extraction modes,
  saves a fixture and the terms it read, and opens a pull request with the `try` report.

The repo's `CLAUDE.md` and the [gleanyard skill](agents.md#the-gleanyard-skill) give it the
procedures, and the session installs the pinned gleanyard on its own. You review each pull
request, decide the source's rights and set `enabled: true`. The Claude GitHub app works the
same way from an issue: mention `@claude` with the request.

## Or open it in Codespaces

The template has a devcontainer. **Code → Codespaces → Create codespace** opens the repo in
a browser editor with Python, gleanyard (with its MCP server registered for Claude Code) and
Claude Code ready. Run `claude` in the terminal and ask as above, or run commands yourself:

```bash
gleanyard probe https://example.org/events
gleanyard try example-events
```

crawl4ai, which only `gleanyard map` needs, is not preinstalled:
`pip install 'gleanyard[map]' && crawl4ai-setup`.

## The read-only connector

The gleanyard connector is a remote MCP server. Add its URL as a custom connector in
claude.ai, ChatGPT, Claude Desktop or any client that speaks MCP over HTTP:

```text
https://gleanyard-connector.leima.workers.dev/mcp
```

| Tool | What it reads |
| --- | --- |
| `get_started` | How to start a project, as on this page |
| `search_recipes` | The [source registry](registry.md): recipes, their schema, tier and health |
| `get_recipe` | One recipe's source file, notes and terms evidence |
| `get_schema` | A schema the registry's recipes are written for |
| `project_status` | `status.json` of a public project repo: health of each source |
| `search_records` | A public project's rights-filtered export, by text and source |
| `recent_changes` | A public project's change feed |

It is read-only by design. It cannot create, change or run sources, and it never fetches
the sites a project collects from: it reads only the registry's site and
`raw.githubusercontent.com`, so it sees exactly what a project has already published.
Private repos stay invisible to it. To set up sources, use one of the ways above.

Ask, for example: "Using gleanyard, what family events in kausalflow/berlin-family-events
mention theater?" or "Are any sources failing in kausalflow/berlin-family-events?"

### Run your own

The connector is one file with no dependencies,
[`connector/worker.js`](https://github.com/kausalflow/gleanyard/tree/main/connector), for
Cloudflare Workers. The free plan's 100,000 requests a day are plenty for it. To serve your
own registry, set `REGISTRY_URL` in `wrangler.toml`, then:

```bash
cd connector
npx wrangler deploy
```

`node connector/test.mjs` runs its tests offline.

In a fork or copy of the gleanyard repo, the **Deploy connector** workflow deploys it for you
whenever `connector/` changes on `main`: add the repository secrets `CLOUDFLARE_API_TOKEN` (a
token from the template "Edit Cloudflare Workers") and `CLOUDFLARE_ACCOUNT_ID`. Without them
the workflow does nothing.

## The GitHub Action

The gleanyard repo is also an Action. It installs the gleanyard version of the ref you use
and runs commands, one per line:

```yaml
- uses: actions/checkout@v4
- uses: kausalflow/gleanyard@v0.14.0
  with:
    run: |
      validate
      test
```

| Input | Default | Meaning |
| --- | --- | --- |
| `run` | `validate` | Commands without the leading `gleanyard`, one per line |
| `working-directory` | `.` | The project folder |
| `version` | the action's ref | A pip specifier instead, such as `~=0.14.0` |
| `extras` | none | Extras to install, such as `llm` |
| `python-version` | `3.12` | Passed to `setup-python` |

The template's own workflows install gleanyard with `pip` and do not need the Action; it is
for workflows you write yourself, such as checking a project folder inside another repo.
