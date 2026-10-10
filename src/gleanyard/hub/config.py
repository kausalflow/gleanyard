"""``hub.yaml``: which repos the hub watches and when it alerts."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field, ValidationError, field_validator

from gleanyard.errors import ConfigError
from gleanyard.schema import StrictModel

REPO = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class HubConfig(StrictModel):
    schema_version: int = 1
    repos: list[str] = Field(min_length=1, description="Collection repos as owner/name.")
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
    def check_repos(cls, repos: list[str]) -> list[str]:
        import re

        bad = [repo for repo in repos if not re.match(REPO, repo)]
        if bad:
            raise ValueError(f"not owner/name: {', '.join(bad)}")
        if len(set(repos)) != len(repos):
            raise ValueError("a repo is listed twice")
        return repos


def load_hub_config(path: Path) -> HubConfig:
    try:
        return HubConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    except (yaml.YAMLError, ValidationError) as error:
        raise ConfigError(f"{path}: {error}") from error
