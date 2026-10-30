# {{registry}}

A registry of [gleanyard](https://github.com/kausalflow/gleanyard) source recipes: source
files that work, each with one saved page, the records that page gives, and the terms someone
read. Any gleanyard project can install one:

```bash
gleanyard search museum{{registry_flag}}
gleanyard add <recipe-id>{{registry_flag}}
```

`add` writes the source disabled and with its rights pending. The recipe's evidence is someone
else's reading of the terms, not an approval: read it, decide the rights for your project, run
`gleanyard try <id>`, then enable the source.

## How it stays free to run

- Pull requests run `gleanyard registry check`: it validates each recipe and replays its
  fixture offline. No request goes to any site, and no model is called.
- The `publish` workflow builds a static site for GitHub Pages: `index.json`, the recipe
  files, and a page to browse. A recipe's health comes from the `status.json` of public
  projects that installed it (listed in `registry.yaml`, or found by the `gleanyard` topic).
  The registry never runs a source itself, and it publishes counts, never project names.
- The repo is public, so its Actions minutes cost nothing.

## Setup

1. Set **Settings → Pages → Source** to **GitHub Actions**.
2. Add a schema to `schemas/` (a project's `schema.yaml`, under its own name).
3. Run the `publish` workflow once by hand.

## Adding a recipe

In a checkout of this repo, with a project whose source works and has a fixture, a snapshot
(`gleanyard test --update`) and a terms review in `evidence/`:

```bash
gleanyard registry pack <source-id> --project ../my-project \
  --maintainer <github-login> \
  --description "What the source publishes" \
  --fixture-basis "Why the fixture page may be public, e.g. trimmed to facts"
gleanyard registry check
```

Then open a pull request. A recipe is accepted only when:

- its tier allows sharing at all: `internal_only`, `unknown` and `blocked` sources stay in
  their own project;
- it is `enabled: false` with `rights.review_status: pending`;
- its fixture is small (256 KB at most) and `fixture_basis` says why it may be public;
- it uses `fields`, `jsonld` or `selectors` (an `llm` source cannot be replayed offline);
- `gleanyard registry check` passes.
