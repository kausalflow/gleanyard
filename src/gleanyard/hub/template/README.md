# {{hub}}

Watches [gleanyard](https://github.com/kausalflow/gleanyard) collection projects. Every three
hours the `hub` workflow reads each project's `status.json`, keeps one issue per active
problem here (label `hub-alert`), and posts a weekly digest issue.

## Setup

1. Check `hub.yaml`: list projects under `repos`, or let `discover` find every repo of an
   owner that has the topic `gleanyard`.
2. Give the hub read access to the projects. Public repos and status.json URLs need nothing.
   For private repos, create a fine-grained token with **Contents: read** and **Pull
   requests: read** on them, and save it here as the Actions secret `HUB_TOKEN`. If you would
   rather not create a token, list each project's status.json URL instead of its repo.
3. Run the `hub` workflow once by hand (Actions tab), and read its first step: it runs
   `gleanyard hub check` and says what is still missing.

## The dashboard

{{dashboard}}

Alerts:

| Alert | Opens when |
| --- | --- |
| Source failing | a source's run raised an error |
| Lost records, fields stopped filling, more records rejected, data stopped changing | a source ran but is `degraded` |
| Missed run | a project's status.json is older than `missed_run_hours` |
| PR waiting | a `setup` or `repair` pull request waited longer than `pr_waiting_days` |
| No report | a project's status.json is missing or cannot be read |

Each issue closes by itself when the problem is gone.
