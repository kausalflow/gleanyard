# The status hub

One small repo watches every collection repo, so nobody checks GitHub Actions by hand. It
lists the repos in `hub.yaml`, and a scheduled job runs `gleanyard hub run`:

1. reads each repo's `status.json` and its open pull requests labelled `setup` or `repair`
   through the GitHub API;
2. writes a snapshot (`data/state.json`) for the dashboard;
3. opens or closes one issue per active problem in the hub repo.

```yaml
# hub.yaml
repos: [kausalflow/my-events, kausalflow/my-markets]
missed_run_hours: 36      # a report older than this means a scheduled run was missed
pr_waiting_days: 7
digest_weekday: 0         # Monday; null turns the weekly digest off
```

## The dashboard

`gleanyard hub build` turns the snapshot into one static page (`site/index.html`, no
JavaScript): a row per repo with a red, amber or green marker, opening to its sources, their
last success and error, and the setup or repair pull requests waiting for review, with links
to each repo's Actions tab. A repo that needs attention starts open. The page follows the
reader's light or dark setting and works on a phone.

## Creating the hub

```bash
gleanyard hub init collection-status --repo kausalflow/my-events --repo kausalflow/my-markets
```

writes `hub.yaml`, a `hub` workflow that runs every three hours (read the repos, sync issues,
build the page, deploy to GitHub Pages) and a README with the three settings to change: the
repo list, Pages source set to GitHub Actions, and an optional `HUB_TOKEN` secret.

## Alerts fire on change

Every problem has a stable key, such as `kausalflow/my-events:city-events:failing`. A problem
that starts opens an issue, a problem that is still there changes nothing, and a problem
that ends closes its issue with a note. A source that stays down for a week is one issue,
not seven.

| Alert | Active while |
| --- | --- |
| Source failing | A source's health in `status.json` is `failing`. |
| Lost records | A source has a `record_drop` warning: over 25% fewer records than its last good run. |
| Fields stopped filling | A source has a `fill_rate_drop` warning, such as dates that stopped parsing. |
| More records rejected | A source has a `rejection_spike` warning. |
| Data stopped changing | A source has an `unchanged` warning (only with `expect_change_days`). |
| Missed run | The report's `generated_at` is older than `missed_run_hours`. |
| PR waiting | A `setup` or `repair` pull request has been open longer than `pr_waiting_days`. |
| No report | `status.json` is missing or invalid, or the repo cannot be read. |
| Weekly digest | Every `digest_weekday`, even when everything is green; one issue per week. |

## Running it

```bash
export GH_TOKEN=...            # read access to the watched repos; issues:write on the hub
gleanyard hub run --hub-repo kausalflow/collection-status --dry-run   # look first
gleanyard hub run --hub-repo kausalflow/collection-status
```

`--dry-run` still reads the repos and writes the snapshot, but touches no issues. The default
`GITHUB_TOKEN` of a workflow can only read its own repo, so watching other private repos needs
a fine-grained token (contents: read, pull requests: read) stored as a secret.
