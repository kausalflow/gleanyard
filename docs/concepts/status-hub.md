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

## Alerts fire on change

Every problem has a stable key, such as `kausalflow/my-events:city-events:failing`. A problem
that starts opens an issue, a problem that is still there changes nothing, and a problem
that ends closes its issue with a note. A source that stays down for a week is one issue,
not seven.

| Alert | Active while |
| --- | --- |
| Source failing | A source's health in `status.json` is `failing`. |
| Lost records | A source is `degraded`: over 25% fewer records than its last good run. |
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
