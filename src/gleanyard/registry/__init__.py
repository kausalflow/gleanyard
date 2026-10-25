"""The public source registry: known-good source recipes that any project can install.

A registry is a repo of recipes. Each recipe is one source file with everything needed to
trust it without fetching the site: its mapping, one fixture page with the records it gives
(``expected.json``), and the terms someone read (``evidence/``). ``gleanyard registry check``
replays every fixture offline, so a pull request to the registry costs no requests to any
site and no model calls. ``gleanyard registry build`` writes a static site (``index.json``
plus the recipe files) for GitHub Pages, with each recipe's health read from the public
``status.json`` files of the projects that installed it.

``gleanyard add`` installs a recipe into a project, disabled and with its rights pending: a
recipe carries someone's reading of the terms as evidence, never an approval.
"""
