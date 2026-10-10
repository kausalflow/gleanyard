# {{hub}}

Watches the [gleanyard](https://github.com/kausalflow/gleanyard) collection repos listed in
`hub.yaml`. Every few hours a workflow reads each repo's `status.json`, publishes a dashboard
on GitHub Pages, and keeps one issue per active problem here (label `hub-alert`).

Setup:

1. Edit `hub.yaml`.
2. In **Settings → Pages**, set the source to **GitHub Actions**.
3. Reading other private repos needs a fine-grained token saved as the secret `HUB_TOKEN`:
   contents and pull requests read on the watched repos, and issues write on this repo.
   Public repos work with the default token.
4. Run the `hub` workflow once by hand.
