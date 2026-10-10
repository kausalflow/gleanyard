"""The hub's snapshot of every watched repo, written for the dashboard."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from gleanyard.hub.config import HubConfig
from gleanyard.hub.github import GitHub, PullRequest
from gleanyard.status import ProjectStatus


class RepoState(BaseModel):
    repo: str
    status: ProjectStatus | None = None
    problem: str | None = Field(default=None, description="Why no usable status.json was read.")
    pulls: list[PullRequest] = Field(default_factory=list)

    @property
    def level(self) -> str:
        """green, amber or red, for the dashboard."""
        if self.status is None:
            return "red"
        health = {source.health for source in self.status.sources}
        if "failing" in health:
            return "red"
        return "amber" if "degraded" in health else "green"


class HubState(BaseModel):
    version: int = 1
    generated_at: datetime
    repos: list[RepoState]


def collect_state(config: HubConfig, github: GitHub, now: datetime | None = None) -> HubState:
    now = now or datetime.now(UTC)
    repos = []
    for repo in config.repos:
        state = RepoState(repo=repo)
        body = github.get_file(repo, config.status_path)
        if body is None:
            state.problem = f"{config.status_path} not found, or the repo is not readable"
        else:
            try:
                state.status = ProjectStatus.model_validate_json(body)
            except ValueError as error:
                state.problem = f"{config.status_path} is not a valid report: {error}"
        state.pulls = [
            pull for pull in github.open_pulls(repo) if set(pull.labels) & set(config.pr_labels)
        ]
        repos.append(state)
    return HubState(generated_at=now, repos=repos)
