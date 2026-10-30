# Source registry

A source registry is a public repo of source recipes: source files that work, each with one
saved page, the records that page gives, and the terms someone read. Any gleanyard project
can install one instead of setting the source up from scratch:

```bash
gleanyard search theater                 # find recipes, with their health
gleanyard add grips-theater              # install one into this project
```

The default registry is [kausalflow/gleanyard-registry](https://github.com/kausalflow/gleanyard-registry),
served at `https://kausalflow.github.io/gleanyard-registry/`. Point `--registry` (or the
`GLEANYARD_REGISTRY` variable) at another registry's site, or at a local checkout of one.

## What `add` does

`gleanyard add <recipe>` writes the recipe into the project under the project's own names:

| Recipe file | Written to |
| --- | --- |
| `source.yaml` | `sources/<id>.yaml` |
| `mapping.json` (selectors only) | `mappings/<id>.json` |
| `fixture.<ext>` | `tests/fixtures/<id>.<ext>` |
| `evidence/<date>-terms.yaml` | `evidence/<id>/<date>-terms.yaml` |

It sets the source's `id` (the recipe id, or `--id`), its `schema` (the project's), and a
`recipe` key that names the recipe and its digest. Then it validates the project and replays
the fixture against the project's schema to write `tests/fixtures/<id>.expected.json`. If
either fails, nothing is left behind; `--force` keeps the files so you can fix them by hand.
A recipe for a host the project already collects, or one in its `excluded_hosts`, is refused
unless you pass `--force`.

The source always arrives with `enabled: false` and `rights.review_status: pending`. **A
recipe's evidence is someone else's reading of the terms, not an approval.** Each evidence
file says so in a comment at its top. Read the pages, add your own evidence file, set the
rights for your project, run `gleanyard try <id>`, and only then enable the source.

A recipe written for another schema can still fit yours: `add` checks the mapping against
your schema, and a field your schema lacks shows up as a `validate` problem.

## Health across projects

Each project's `status.json` names, per source, the recipe it came from (`recipe` and
`recipe_digest`). The registry's daily `publish` workflow reads the `status.json` of the
projects listed in its `registry.yaml` and of every public repo with the topic `gleanyard`,
and counts, per recipe, how many projects run it and how many of those are `ok`, `degraded`
or `failing`:

| Level | Means |
| --- | --- |
| `ok` | every project that reported it in the last `fresh_days` (default 14) is ok |
| `degraded` | some are not ok |
| `failing` | all of them are failing |
| `unknown` | no fresh report |

Only counts are published, never which projects reported them. Disabled sources and stale
reports do not count. To have your project count, make its repo public with the topic
`gleanyard`, or ask the registry to list your `status.json` URL under `users`.

## What it costs

A registry is built to cost nothing to run:

- Its repo is public, so its Actions minutes are free.
- A pull request runs `gleanyard registry check`, which validates each recipe and replays its
  fixture offline. No request goes to any site, and no model is called.
- Health comes from status files the projects already publish. The registry never runs a
  source, so it sends no traffic to the sites.
- The site is static (`index.json`, the recipe files, an `index.html`) on GitHub Pages.

## Adding a recipe

In a checkout of the registry, with a project whose source works, has a fixture with a
snapshot (`gleanyard test -s <id> --update`) and a terms review in `evidence/`:

```bash
gleanyard registry pack <id> --project ../my-project \
  --maintainer <github-login> \
  --description "What the source publishes" \
  --fixture-basis "Why the fixture may be public, e.g. trimmed to two events, facts only"
gleanyard registry check
```

`pack` copies the source, mapping, fixture, snapshot and evidence into `recipes/<id>/`, and the
project's schema into `schemas/` if the registry does not have it. It writes the source
disabled, resets its rights review to pending, and drops the review notes and dates, since
those were one project's decision. Comments in the source file are kept.

`registry check` accepts a recipe only when:

- its tier lets records be shared at all (`open`, `open_attribution`, `licensed`,
  `facts_only`, `link_only`): an `internal_only`, `unknown` or `blocked` source stays in its
  own project, and `pack` refuses it;
- it is disabled, with `rights.review_status: pending`;
- it uses `fields`, `jsonld` or `selectors` (an `llm` source cannot be replayed offline);
- it has one fixture of 256 KB or less, a snapshot that the fixture still gives, at least one
  evidence file with the `pages` read, and nothing else; the whole recipe stays under 512 KB;
- `recipe.yaml` names the schema, the maintainers, and in `fixture_basis` why the fixture
  page may be public. A fixture in a registry is published, so trim it to what the replay
  needs and keep personal data out.

## Running a registry

```bash
gleanyard registry init my-registry --url https://<owner>.github.io/my-registry/
```

writes `registry.yaml`, a `README.md`, a `CLAUDE.md` for AI assistants, and two workflows:
`check` on pull requests that touch recipes or schemas, and `publish`, which runs
`gleanyard registry build` and deploys the site to Pages on each merge and once a day. Set
**Settings → Pages → Source** to **GitHub Actions**.

```yaml title="registry.yaml"
schema_version: 1
name: my-registry
url: https://<owner>.github.io/my-registry/
users: [owner/project, https://example.org/status.json]
discover:
  topic: gleanyard
fresh_days: 14
```

A recipe's layout:

```text
recipes/<id>/recipe.yaml                  # name, description, schema, tags, maintainers, fixture_basis
recipes/<id>/source.yaml                  # the source file
recipes/<id>/mapping.json                 # selectors only
recipes/<id>/fixture.<ext>                # one saved page
recipes/<id>/expected.json                # the records it gives (from `gleanyard test --update`)
recipes/<id>/evidence/<date>-terms.yaml   # the terms someone read
schemas/<name>.yaml                       # the schemas recipes are written for
```

[`examples/registry`](https://github.com/kausalflow/gleanyard/tree/main/examples/registry) is a
small registry made with `pack` from the three sources of the datasets example.
