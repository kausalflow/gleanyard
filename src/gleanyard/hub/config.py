"""``hub.yaml``: which repos the hub watches and when it alerts.

A watched project is one of:

- ``owner/name``: read ``status.json`` and its open pull requests through the GitHub API;
- a URL to a ``status.json`` (a raw file, a Pages site, any static host): fetched without a
  GitHub token, so no pull request alerts;
- found by ``discover``: every repo of an owner with a topic (``gleanyard`` by default).
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from gleanyard.errors import ConfigError
from gleanyard.schema import StrictModel

REPO = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
OWNER = r"^[A-Za-z0-9_.-]+$"


class StatusUrl(StrictModel):
    """A project whose ``status.json`` the hub fetches from a URL, with no GitHub token."""

    name: str = Field(min_length=1, description="How the project is shown on the dashboard.")
    url: str = Field(pattern=r"^https?://", description="URL of the project's status.json.")


class Discover(StrictModel):
    owner: str = Field(pattern=OWNER, description="GitHub user or organization to search.")
    topic: str = Field(default="gleanyard", description="Repos with this topic are watched.")


class Watched(StrictModel):
    """One project the hub reads: a GitHub repo or a status.json URL."""

    name: str
    repo: str | None = None
    url: str | None = None


def url_name(url: str) -> str:
    """A readable name for a bare status.json URL."""
    parts = urlsplit(url)
    path = [part for part in parts.path.split("/") if part]
    if parts.hostname == "raw.githubusercontent.com" and len(path) >= 2:
        return f"{path[0]}/{path[1]}"
    if path and path[-1].endswith(".json"):
        path = path[:-1]
    return "/".join([parts.hostname or url, *path])


class HubConfig(StrictModel):
    schema_version: int = 1
    repos: list[str | StatusUrl] = Field(
        default_factory=list,
        description="Projects as owner/name, a status.json URL, or {name, url}.",
    )
    discover: Discover | None = Field(
        default=None, description="Also watch every repo of an owner that has a topic."
    )
    status_path: str = Field(default="status.json", description="Where each repo keeps its report.")
    missed_run_hours: int = Field(
        default=36, ge=1, description="Alert when a repo's report is older than this."
    )
    pr_waiting_days: int = Field(
        default=7, ge=1, description="Alert when a setup or repair PR has waited this long."
    )
    pr_labels: list[str] = Field(default_factory=lambda: ["setup", "repair"])
    digest_weekday: int | None = Field(
        default=0, ge=0, le=6, description="Weekly digest day, Monday=0; null turns it off."
    )

    @field_validator("repos")
    @classmethod
    def check_repos(cls, repos: list[str | StatusUrl]) -> list[str | StatusUrl]:
        bad = [
            repo
            for repo in repos
            if isinstance(repo, str) and not re.match(REPO, repo) and not _is_url(repo)
        ]
        if bad:
            raise ValueError(f"not owner/name or an http(s) URL: {', '.join(bad)}")
        keys = [repo if isinstance(repo, str) else repo.url for repo in repos]
        if len(set(keys)) != len(keys):
            raise ValueError("a repo is listed twice")
        return repos

    @model_validator(mode="after")
    def needs_something_to_watch(self) -> HubConfig:
        if not self.repos and self.discover is None:
            raise ValueError("list at least one repo under `repos`, or set `discover`")
        return self

    def listed(self) -> list[Watched]:
        """The projects named in ``repos``, before discovery adds any."""
        watched = []
        for repo in self.repos:
            if isinstance(repo, StatusUrl):
                watched.append(Watched(name=repo.name, url=repo.url))
            elif _is_url(repo):
                watched.append(Watched(name=url_name(repo), url=repo))
            else:
                watched.append(Watched(name=repo, repo=repo))
        return watched

    @property
    def needs_github(self) -> bool:
        """Whether reading the projects needs the GitHub API (and so a token)."""
        return self.discover is not None or any(item.repo for item in self.listed())


def _is_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def load_hub_config(path: Path) -> HubConfig:
    try:
        return HubConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except (yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"{path}: {error}") from error
