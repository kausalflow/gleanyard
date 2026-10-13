"""``gleanyard init``: create a collection project from the bundled template.

The template is the repo layout, the starter schema, the scheduled workflows and the
``CLAUDE.md`` that tells an AI assistant how to research, set up and repair sources. Files
named ``dot-*`` are written with a leading dot, so they survive packaging.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import ValidationError

from gleanyard.errors import ConfigError
from gleanyard.hub.config import HubConfig

TEMPLATE = Path(__file__).parent / "template"
EMPTY_DIRS = ("sources", "mappings", "candidates", "evidence", "tests/fixtures")
NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def init_project(root: Path, project: str, schema: str) -> list[Path]:
    """Write the template into ``root`` (created if needed) and return the files written."""
    if not NAME.match(project):
        raise ConfigError(f"project name {project!r} must be lowercase letters, digits and dashes")
    if not NAME.match(schema):
        raise ConfigError(f"schema name {schema!r} must be lowercase letters, digits and dashes")
    written = write_template(TEMPLATE, root, {"{{project}}": project, "{{schema}}": schema})
    for name in EMPTY_DIRS:
        keep = root / name / ".gitkeep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.touch()
        written.append(keep)
    return written


def write_template(template: Path, root: Path, replacements: dict[str, str]) -> list[Path]:
    """Copy a template directory into an empty ``root``, filling ``{{placeholders}}``."""
    root.mkdir(parents=True, exist_ok=True)
    existing = [path.name for path in root.iterdir() if path.name != ".git"]
    if existing:
        raise ConfigError(f"{root} is not empty ({', '.join(sorted(existing)[:3])}...)")
    written: list[Path] = []
    for source in sorted(template.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(template)
        target = root.joinpath(*(_undot(part) for part in relative.parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def _undot(part: str) -> str:
    return "." + part.removeprefix("dot-") if part.startswith("dot-") else part


HUB_TEMPLATE = Path(__file__).parent / "hub" / "template"


def init_hub(
    root: Path, name: str, repos: list[str], owner: str | None = None, pages: bool = False
) -> list[Path]:
    """Write the status hub template.

    ``repos`` (owner/name or status.json URLs) seeds ``hub.yaml``, ``owner`` turns on discovery
    by topic, and ``pages`` publishes the dashboard on GitHub Pages instead of only in the
    workflow's job summary.
    """
    if not NAME.match(name):
        raise ConfigError(f"hub name {name!r} must be lowercase letters, digits and dashes")
    config = {"repos": repos, "discover": {"owner": owner} if owner else None}
    try:
        HubConfig.model_validate(config)
    except ValidationError as error:
        raise ConfigError(f"hub.yaml would not be valid: {error}") from error
    listing = "\n".join(f"  - {repo}" for repo in repos)
    replacements = {
        "{{hub}}": name,
        "{{repos}}": f"repos:\n{listing}" if repos else "repos: []",
        "{{discover}}": (
            f"discover:\n  owner: {owner}\n  topic: gleanyard"
            if owner
            else "# discover:            # also watch every repo of an owner with a topic\n"
            "#   owner: my-org\n#   topic: gleanyard"
        ),
        **(HUB_PAGES if pages else HUB_ALERTS_ONLY),
    }
    return write_template(HUB_TEMPLATE, root, replacements)


HUB_PAGES = {
    "{{pages_permissions}}": "  pages: write\n  id-token: write\n",
    "{{environment}}": (
        "    environment:\n      name: github-pages\n"
        "      url: ${{ steps.deploy.outputs.page_url }}\n"
    ),
    "{{publish}}": (
        "      - uses: actions/upload-pages-artifact@v3\n        with:\n          path: site\n"
        "      - id: deploy\n        uses: actions/deploy-pages@v4\n"
    ),
    "{{dashboard}}": (
        "The dashboard is published on GitHub Pages. On most GitHub plans that page is\n"
        "public: it shows source names, record counts and error messages, never records.\n"
        "Set **Settings → Pages → Source** to **GitHub Actions**."
    ),
}
HUB_ALERTS_ONLY = {
    "{{pages_permissions}}": "",
    "{{environment}}": "",
    "{{publish}}": (
        "      - uses: actions/upload-artifact@v4\n        with:\n          name: dashboard\n"
        "          path: site\n"
    ),
    "{{dashboard}}": (
        "Nothing is published. Each run's summary page (the `hub` workflow in the Actions\n"
        "tab) shows the overview, and the dashboard page is attached to the run. To publish\n"
        "it on GitHub Pages instead, recreate the hub with `gleanyard hub init --pages`."
    ),
}
