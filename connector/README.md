# gleanyard connector

A read-only remote MCP server for [Cloudflare Workers](https://developers.cloudflare.com/workers/).
Any AI app that takes a custom MCP connector (claude.ai, ChatGPT, Claude Desktop, ...) can
then search the gleanyard source registry and read what public gleanyard projects publish,
with nothing installed.

- One file, `worker.js`, with no dependencies and no build step.
- Stateless MCP Streamable HTTP at `/mcp`, JSON responses, no sessions, no auth.
- Fetches only the registry's site (`REGISTRY_URL`) and `raw.githubusercontent.com`; never
  the sites projects collect from. Responses are cached for ten minutes.
- Tools: `get_started`, `search_recipes`, `get_recipe`, `get_schema`, `project_status`,
  `search_records`, `recent_changes`.

```bash
node test.mjs          # offline tests (fetch is mocked)
npx wrangler dev       # local server at http://localhost:8787/mcp
npx wrangler deploy    # deploy to your Cloudflare account
```

Docs: https://kausalflow.github.io/gleanyard/concepts/no-install/
