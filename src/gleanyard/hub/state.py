"""The hub's snapshot of every watched project, written for the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from gleanyard.hub.config import HubConfig, Watched
from gleanyard.hub.github import GitHub, PullRequest
from gleanyard.status import ProjectStatus

Fetch = Callable[[str], bytes]


class RepoState(BaseModel):
    repo: str = Field(description="The project's name: owner/name, or the name given to a URL.")
    url: str | None = Field(default=None, description="Set when status.json came from a URL.")
    status: ProjectStatus | None = None
    problem: str | None = Field(default=None, description="Why no usable status.json was read.")
    pulls: list[PullRequest] = Field(default_factory=list)

    @property
    def on_github(self) -> bool:
        return self.url is None

    @property
    def level(self) -> str:
        """green, amber or red, for the dashboard."""
        if self.status is None:
            return "red"
        health = {source.health for source in self.status.sources}
        if "failing" in health:
            return "red"
        terms = any(source.terms and source.terms.changed for source in self.status.sources)
        return "amber" if "degraded" in health or terms else "green"


class HubState(BaseModel):
    version: int = 1
    generated_at: datetime
    repos: list[RepoState]
    problems: list[str] = Field(
        default_factory=list, description="Hub-level problems, such as a failed discovery."
    )


def fetch_url(url: str) -> bytes:
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.content


def watched_projects(config: HubConfig, github: GitHub | None) -> tuple[list[Watched], list[str]]:
    """The listed projects plus the discovered ones, and any problem finding them."""
    watched = config.listed()
    problems: list[str] = []
    if config.discover is not None and github is not None:
        owner, topic = config.discover.owner, config.discover.topic
        try:
            found = github.find_repos(owner, topic)
        except httpx.HTTPError as error:
            problems.append(f"could not search {owner}'s repos for the topic `{topic}`: {error}")
            found = []
        known = {item.repo for item in watched}
        watched += [Watched(name=repo, repo=repo) for repo in found if repo not in known]
    return watched, problems


def collect_state(
    config: HubConfig,
    github: GitHub | None,
    now: datetime | None = None,
    fetch: Fetch | None = None,
) -> HubState:
    now = now or datetime.now(UTC)
    fetch = fetch or fetch_url
    watched, problems = watched_projects(config, github)
    repos = [read_project(item, config, github, fetch) for item in watched]
    return HubState(generated_at=now, repos=repos, problems=problems)


def read_project(
    item: Watched, config: HubConfig, github: GitHub | None, fetch: Fetch = fetch_url
) -> RepoState:
    state = RepoState(repo=item.name, url=item.url)
    if item.url is not None:
        try:
            body: bytes | None = fetch(item.url)
        except httpx.HTTPError as error:
            state.problem = f"could not fetch {item.url}: {error}"
            return state
        where = item.url
    else:
        assert item.repo is not None and github is not None, "a repo needs the GitHub API"
        body = github.get_file(item.repo, config.status_path)
        where = config.status_path
        state.pulls = [
            pull
            for pull in github.open_pulls(item.repo)
            if set(pull.labels) & set(config.pr_labels)
        ]
    if body is None:
        state.problem = f"{where} not found, or the repo is not readable"
    else:
        try:
            state.status = ProjectStatus.model_validate_json(body)
        except ValueError as error:
            state.problem = f"{where} is not a valid report: {error}"
    return state
