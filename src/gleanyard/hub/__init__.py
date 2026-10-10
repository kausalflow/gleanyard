"""The status hub: one place that watches every collection repo.

A hub repo lists collection repos in ``hub.yaml``. A scheduled job reads each repo's
``status.json`` and open setup or repair pull requests, writes a snapshot for the dashboard,
and keeps one GitHub issue per active problem, opening it when the problem starts and closing
it when it ends, so alerts fire on change, not on every run.
"""
