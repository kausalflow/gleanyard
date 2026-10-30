"""The registry's files: ``registry.yaml``, each recipe's ``recipe.yaml``, and ``index.json``."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from gleanyard.rights import RightsTier
from gleanyard.schema import StrictModel

DEFAULT_REGISTRY = "https://kausalflow.github.io/gleanyard-registry/"
REGISTRY_FILE = "registry.yaml"
INDEX_FILE = "index.json"

# A recipe is shared in public, so only tiers whose records may be shared at all can be in it.
# internal_only, unknown and blocked sources stay in their own project.
PUBLISHABLE_TIERS = frozenset(
    {
        RightsTier.OPEN,
        RightsTier.OPEN_ATTRIBUTION,
        RightsTier.LICENSED,
        RightsTier.FACTS_ONLY,
        RightsTier.LINK_ONLY,
    }
)

Level = Literal["ok", "degraded", "failing", "unknown"]


class RecipeMeta(StrictModel):
    """``recipes/<id>/recipe.yaml``: what the recipe is and who looks after it."""

    schema_version: Literal[1] = 1
    name: str = Field(min_length=1)
    description: str = Field(min_length=1, description="What the source publishes, briefly.")
    schema_name: str = Field(alias="schema", description="Name of a schema in schemas/.")
    tags: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(
        min_length=1, description="GitHub logins to ask about the recipe."
    )
    fixture_basis: str = Field(
        min_length=10,
        description=(
            "Why the fixture page may be shared in public: for example trimmed to two events "
            "and facts only, or published under an open licence."
        ),
    )

    model_config = {"extra": "forbid", "populate_by_name": True}


class Discover(StrictModel):
    topic: str = Field(default="gleanyard", description="Public repos with this topic count.")


class RegistryConfig(StrictModel):
    """``registry.yaml`` at the registry's root."""

    schema_version: Literal[1] = 1
    name: str = Field(min_length=1)
    url: str | None = Field(
        default=None, pattern=r"^https?://", description="Where the built site is served."
    )
    users: list[str] = Field(
        default_factory=list,
        description="Projects whose health counts: owner/name (public) or a status.json URL.",
    )
    discover: Discover | None = Field(
        default=None, description="Also read every public repo with a topic."
    )
    fresh_days: int = Field(
        default=14, ge=1, description="A report older than this does not count for health."
    )


class RecipeHealth(BaseModel):
    """How a recipe fares in the projects that installed it, from their own runs.

    Only counts are published, never which projects reported them.
    """

    level: Level = "unknown"
    users: int = 0
    ok: int = 0
    degraded: int = 0
    failing: int = 0
    last_success_at: datetime | None = None
    checked_at: datetime | None = None

    def add(self, health: str, checked_at: datetime | None, last_success_at: datetime | None):
        self.users += 1
        if health == "ok":
            self.ok += 1
        elif health == "degraded":
            self.degraded += 1
        else:
            self.failing += 1
        if checked_at and (self.checked_at is None or checked_at > self.checked_at):
            self.checked_at = checked_at
        if last_success_at and (
            self.last_success_at is None or last_success_at > self.last_success_at
        ):
            self.last_success_at = last_success_at
        if self.ok == self.users:
            self.level = "ok"
        elif self.failing == self.users:
            self.level = "failing"
        else:
            self.level = "degraded"


class IndexEntry(BaseModel):
    id: str
    name: str
    description: str
    schema_name: str = Field(alias="schema")
    tags: list[str] = Field(default_factory=list)
    maintainers: list[str] = Field(default_factory=list)
    host: str
    url: str
    delivery: str
    mode: str
    tier: str
    coverage: dict[str, Any] = Field(default_factory=dict)
    files: list[str] = Field(description="Paths under recipes/<id>/ that `add` downloads.")
    digest: str
    fixture_today: date | None = None
    health: RecipeHealth = Field(default_factory=RecipeHealth)

    model_config = {"populate_by_name": True}


class RegistryIndex(BaseModel):
    """``index.json``: every recipe, for `gleanyard search` and `gleanyard add`."""

    version: Literal[1] = 1
    name: str
    generated_at: datetime
    reports_read: int = Field(default=0, description="status.json files read for health.")
    recipes: list[IndexEntry] = Field(default_factory=list)

    def get(self, recipe_id: str) -> IndexEntry | None:
        return next((entry for entry in self.recipes if entry.id == recipe_id), None)
