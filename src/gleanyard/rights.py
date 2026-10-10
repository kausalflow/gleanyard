"""Rights, coverage and retention blocks, carried over from cityklub.

Rights are declared per source and checked again at export against the
*current* source config, so withdrawing a permission removes records from the
next export without touching history. Everything defaults to fail-closed.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from gleanyard.schema import StrictModel


class RightsTier(StrEnum):
    OPEN = "open"
    OPEN_ATTRIBUTION = "open_attribution"
    LICENSED = "licensed"
    FACTS_ONLY = "facts_only"
    LINK_ONLY = "link_only"
    INTERNAL_ONLY = "internal_only"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class AllowedActions(StrictModel):
    discover_urls: bool = True
    private_store: bool = False
    normalize: bool = False
    redistribute_facts: bool = False
    redistribute_text: bool = False
    redistribute_images: bool = False


class SourceRights(StrictModel):
    review_status: Literal["pending", "approved", "rejected"] = "pending"
    collection_basis: str = "unknown"
    copyright_class: str = "unknown"
    database_rights: str = "unknown"
    effective_tier: RightsTier = RightsTier.UNKNOWN
    license_id: str | None = None
    license_url: str | None = None
    attribution_required: bool = False
    attribution_text: str | None = None
    allowed_actions: AllowedActions = Field(default_factory=AllowedActions)
    public_fields: list[str] = Field(default_factory=list)
    reviewed_at: date | None = None
    expires_at: date | None = None
    notes: str | None = None


class Coverage(StrictModel):
    """Where and what a source covers; informational, shown on the dashboard."""

    country: str | None = None
    region: str | None = None
    city: str | None = None
    scope_type: Literal["municipality", "district", "multi_municipality", "national"] | None = None
    scope_name: str | None = None
    categories: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope_type is not None and not (self.scope_name or "").strip():
            raise ValueError("explicit coverage scope needs a nonblank scope_name")
        if self.scope_type == "district" and self.city is not None:
            raise ValueError("district coverage cannot assign one city to all records")
        if self.scope_type == "municipality" and not self.city:
            raise ValueError("municipality coverage needs a city")
        return self


class Retention(StrictModel):
    raw_days: int = Field(default=30, ge=1)
    normalized_days: int = Field(default=730, ge=1)
