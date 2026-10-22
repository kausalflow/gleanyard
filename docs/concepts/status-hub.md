# The status hub

Each project already watches itself: its `collect` workflow opens a `repair` issue when a
source fails or is degraded. A hub watches many projects together. It is one small repo
whose scheduled workflow reads every project's `status.json`, keeps one issue per active
problem, posts a weekly digest, and shows an overview of all projects.

## Set one up in five minutes

1. Create the hub. Either let it find your projects by topic, or list them:

    ```bash
    gleanyard hub init my-hub --owner my-org            # every repo of my-org with the topic gleanyard
    gleanyard hub init my-hub --repo my-org/events      # or name them
    gleanyard hub init my-hub --repo https://example.org/markets/status.json   # or give a URL
    ```

2. Push `my-hub` to GitHub as a new repo. Private is fine.
3. If any watched repo is private, give the hub read access (see [Access](#access)).
4. Run the `hub` workflow once from the Actions tab. Its first step runs `gleanyard hub check`,
   which lists what works and what is missing, each with the step that fixes it:

    ```text
    ok    hub.yaml is valid
    ok    found 2 repo(s) of my-org by topic
    ok    my-org/events: 3 sources, last run 5 hours ago
    FAIL  my-org/markets: status.json not found, or the repo is not readable
          fix: give the token read access to its contents and pull requests, run its
          `collect` workflow once, or list its status.json URL instead
    ```

Run `gleanyard hub check` locally at any time, with `GH_TOKEN` set if repos are private.

## Which projects it watches

```yaml
# hub.yaml
repos:
  - my-org/events                                   # a GitHub repo: read through the API
  - https://example.org/markets/status.json         # a URL: fetched with no GitHub token
  - {name: Markets, url: https://example.org/markets/status.json}   # a URL with a name
discover:                    # optional: also every repo of an owner with a topic
  owner: my-org
  topic: gleanyard
missed_run_hours: 36         # a report older than this means a scheduled run was missed
pr_waiting_days: 7
digest_weekday: 0            # Monday; null turns the weekly digest off
viewer: false                # true adds a searchable table of each project's export
```

`gleanyard init` reminds you to add the topic `gleanyard` to a new project's repo, which is
all a discovering hub needs. Listed repos and discovered ones are merged.

## Access

| Project | What the hub needs |
| --- | --- |
| Public repo | Nothing: the workflow's own token reads it. |
| Private repo | A fine-grained token with **Contents: read** and **Pull requests: read** on it, saved in the hub as the Actions secret `HUB_TOKEN`. |
| status.json URL | Nothing. The URL must be reachable without signing in. Pull request alerts are not available. |

The hub writes issues only in its own repo, with the workflow's own token, so `HUB_TOKEN` never
needs write access. If you would rather not create a token at all, publish each project's
`status.json` somewhere readable (a raw file of a public repo, the project's own Pages site,
any static host) and list the URLs. The file holds health only: source names, counts, fill
rates and errors, never records.

## The overview

By default nothing is published. Every run writes the overview to the workflow's summary
page, which only people with access to the hub repo see, and attaches the full dashboard
page to the run. `gleanyard hub init --pages` publishes the dashboard on GitHub Pages
instead. On most GitHub plans a Pages site is public, so choose it only when it is fine for
anyone to see the source names, record counts and error messages.

The dashboard is one static page (no JavaScript): a row per project with a red, amber or
green marker, opening to its sources, their last success and error or warning, and the
setup or repair pull requests waiting for review. Under each source a folded table shows how
often each field is filled now and in the baseline run; a field below 80% of its baseline is
marked amber and the table starts open, before the 50% `fill_rate_drop` warning fires. A
project that needs attention starts open. The page follows the reader's light or dark
setting and works on a phone.

## The record viewer

With `viewer: true` in `hub.yaml`, the hub also reads each project's public export
(`exports/records.jsonl`, or `export_path`) and `gleanyard hub build` writes a page per project
under `records/`, linked from the dashboard as "Browse N records". The page is a table with a
search box that filters in the browser; it needs no server. A project listed by URL shows a
viewer when it names its export:

```yaml
repos:
  - {name: Markets, url: https://example.org/markets/status.json,
     export_url: https://example.org/markets/records.jsonl}
viewer: true
viewer_rows: 2000            # rows per project in the viewer; the rest are counted
```

The export holds only fields each source's rights allow, so the viewer shows nothing the
project does not already publish. Once the dashboard is on a public Pages site, though,
anyone can read the export of every watched project, private repos included. Leave `viewer`
off unless that is what you want. The hub needs no extra access: it reads the export with
the same token as `status.json`.

## Alerts fire on change

Every problem has a stable key, such as `my-org/events:city-events:failing`. A problem that
starts opens an issue, a problem that is still there changes nothing, and a problem that
ends closes its issue with a note. A source that stays down for a week is one issue, not
seven.

| Alert | Active while |
| --- | --- |
| Source failing | A source's health in `status.json` is `failing`. |
| Lost records | A source has a `record_drop` warning: over 25% fewer records than its last good run. |
| Fields stopped filling | A source has a `fill_rate_drop` warning, such as dates that stopped parsing. |
| More records rejected | A source has a `rejection_spike` warning. |
| Data stopped changing | A source has an `unchanged` warning (only with `expect_change_days`). |
| Terms changed | Terms pages or robots.txt changed since their last review ([watching the terms](rights.md#watching-the-terms)). One alert per repo, listing each changed page with the sources that rely on it. |
| Missed run | The report's `generated_at` is older than `missed_run_hours`. |
| PR waiting | A `setup` or `repair` pull request has been open longer than `pr_waiting_days`. |
| No report | `status.json` is missing or invalid, or the repo cannot be read. |
| Hub problem | Discovery failed, for example because the token cannot search the owner. |
| Weekly digest | Every `digest_weekday`, even when everything is green; one issue per week. |

The warnings behind the degraded alerts are explained in [Health](status.md).

## Running it by hand

```bash
export GH_TOKEN=...      # read access to the watched repos; also issues:write on the hub
gleanyard hub check --hub-repo my-org/my-hub
gleanyard hub run --hub-repo my-org/my-hub --dry-run    # look first: reads, opens nothing
gleanyard hub run --hub-repo my-org/my-hub
gleanyard hub build                                     # site/index.html and site/summary.md
```

A hub that lists only status.json URLs can `--dry-run` with no token at all.
