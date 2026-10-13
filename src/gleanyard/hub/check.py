"""``gleanyard hub check``: say what is missing from a hub's setup, and how to fix it.

Like ``validate`` for a project, each line is a check that passed, or one that failed with the
step that fixes it. It reads the same things ``hub run`` reads and changes nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from gleanyard.errors import ConfigError
from gleanyard.hub.config import HubConfig, load_hub_config
from gleanyard.hub.github import GitHub
from gleanyard.hub.state import Fetch, fetch_url, read_project, watched_projects

WORKFLOW = Path(".github/workflows/hub.yml")


class CheckLine(BaseModel):
    ok: bool
    what: str
    fix: str | None = None


def check_hub(
    config_path: Path,
    github: GitHub | None,
    hub_repo: str | None = None,
    workflow: Path = WORKFLOW,
    fetch: Fetch = fetch_url,
    now: datetime | None = None,
    hub_github: GitHub | None = None,
) -> list[CheckLine]:
    """``github`` reads the projects; ``hub_github`` (default: the same) reads the hub repo."""
    now = now or datetime.now(UTC)
    try:
        config = load_hub_config(config_path)
    except ConfigError as error:
        return [CheckLine(ok=False, what=f"{config_path} is not valid", fix=str(error))]
    lines = [CheckLine(ok=True, what=f"{config_path} is valid")]
    if config.needs_github and github is None:
        lines.append(
            CheckLine(
                ok=False,
                what="no GitHub token, so repos and discovery cannot be read",
                fix=(
                    "set GH_TOKEN when running locally; in the workflow, save a fine-grained "
                    "token as the secret HUB_TOKEN, or list each project's status.json URL "
                    "in `repos` instead"
                ),
            )
        )
        config = config.model_copy(
            update={"discover": None, "repos": [r for r in config.repos if not _is_repo(r)]}
        )
    lines += _projects(config, github, fetch, now)
    hub_github = hub_github or github
    if hub_github is not None and hub_repo:
        lines.append(_pages(hub_github, hub_repo, workflow))
    return lines


def _is_repo(entry: object) -> bool:
    return isinstance(entry, str) and not entry.startswith(("http://", "https://"))


def _projects(
    config: HubConfig, github: GitHub | None, fetch: Fetch, now: datetime
) -> list[CheckLine]:
    lines: list[CheckLine] = []
    try:
        watched, problems = watched_projects(config, github)
    except httpx.HTTPError as error:
        return [CheckLine(ok=False, what="cannot read the projects", fix=str(error))]
    if config.discover is not None:
        owner, topic = config.discover.owner, config.discover.topic
        found = len(watched) - len(config.listed())
        if problems:
            lines += [CheckLine(ok=False, what=problem) for problem in problems]
        elif found == 0:
            lines.append(
                CheckLine(
                    ok=False,
                    what=f"no repos of {owner} have the topic `{topic}`",
                    fix=(
                        f"add the topic `{topic}` to each project repo (the repo page, About, "
                        "Topics), and give the token read access to those repos"
                    ),
                )
            )
        else:
            lines.append(CheckLine(ok=True, what=f"found {found} repo(s) of {owner} by topic"))
    if not watched and not lines:
        lines.append(CheckLine(ok=False, what="no projects to watch", fix="add one to `repos`"))
    for item in watched:
        try:
            state = read_project(item, config, github, fetch)
        except httpx.HTTPError as error:
            state = None
            problem = str(error)
        else:
            problem = state.problem
        if state is not None and state.status is not None:
            hours = (now - state.status.generated_at).total_seconds() / 3600
            lines.append(
                CheckLine(
                    ok=True,
                    what=(
                        f"{item.name}: {len(state.status.sources)} sources, "
                        f"last run {hours:.0f} hours ago"
                    ),
                )
            )
            continue
        fix = (
            "check that the URL is public and points at status.json"
            if item.url
            else "give the token read access to its contents and pull requests, run its "
            "`collect` workflow once, or list its status.json URL instead"
        )
        lines.append(CheckLine(ok=False, what=f"{item.name}: {problem}", fix=fix))
    return lines


def _pages(github: GitHub, hub_repo: str, workflow: Path) -> CheckLine:
    try:
        text = workflow.read_text(encoding="utf-8")
    except OSError:
        return CheckLine(
            ok=False,
            what=f"{workflow} not found",
            fix="run `gleanyard hub check` from the hub repo, or recreate it with `hub init`",
        )
    if "deploy-pages" not in text:
        return CheckLine(ok=True, what="alerts only: no public dashboard page")
    try:
        source = github.pages_source(hub_repo)
    except httpx.HTTPError as error:
        return CheckLine(ok=False, what=f"cannot read the Pages settings of {hub_repo}: {error}")
    if source == "workflow":
        return CheckLine(ok=True, what=f"{hub_repo} publishes the dashboard with GitHub Pages")
    return CheckLine(
        ok=False,
        what=f"GitHub Pages is not set to deploy from Actions in {hub_repo}",
        fix="Settings, Pages, Source: GitHub Actions. The page is public on most plans.",
    )
