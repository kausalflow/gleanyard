"""Rights, coverage and retention blocks, carried over from cityklub.

Rights are declared per source and checked again at export against the
*current* source config, so withdrawing a permission removes records from the
next export without touching history.

The owner's review is a suggestion by default: a source collects and exports with the rights
written in its file while its review is pending, and only an explicit no (a rejected review,
the tier ``blocked``, a tier the policy does not export) stops it. With
``rights_policy.require_reviewed_permission: true`` a project goes back to fail-closed: nothing
is collected without ``private_store`` and ``normalize``, and nothing is exported unapproved.
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
    send_to_model: bool = False
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


class RightsPolicy(StrictModel):
    """Project-wide export rules; the package default is cityklub's policy."""

    schema_version: Literal[1] = 1
    permitted_tiers: list[RightsTier] = Field(
        default_factory=lambda: [
            RightsTier.OPEN,
            RightsTier.OPEN_ATTRIBUTION,
            RightsTier.LICENSED,
            RightsTier.FACTS_ONLY,
            RightsTier.LINK_ONLY,
        ]
    )
    require_reviewed_permission: bool = Field(
        default=False,
        description=(
            "true: collect only with private_store and normalize allowed, and export only "
            "approved sources. false (default): the owner's review is a suggestion; a pending "
            "source collects and exports with the rights its file states."
        ),
    )


def collect_blocker(rights: SourceRights, policy: RightsPolicy) -> str | None:
    """Why a source may not be collected at all, or None if it may."""
    if rights.review_status == "rejected":
        return "the owner rejected this source's rights"
    if rights.effective_tier == RightsTier.BLOCKED:
        return "the source's tier is blocked"
    actions = rights.allowed_actions
    if policy.require_reviewed_permission and not (actions.private_store and actions.normalize):
        return "rights do not allow private_store and normalize"
    return None


def export_blocker(rights: SourceRights | None, policy: RightsPolicy, today: date) -> str | None:
    """Why a source may not be exported at all today, or None if it may."""
    if rights is None:
        return "source is no longer configured"
    if rights.effective_tier not in policy.permitted_tiers:
        return f"tier {rights.effective_tier.value} is not exportable"
    if rights.review_status == "rejected":
        return "rights were rejected"
    if policy.require_reviewed_permission and rights.review_status != "approved":
        return "rights are not approved"
    if rights.expires_at is not None and rights.expires_at < today:
        return "rights review has expired"
    if rights.attribution_required and not rights.attribution_text:
        return "attribution is required but no attribution text is set"
    if (
        policy.require_reviewed_permission
        and rights.effective_tier in {RightsTier.OPEN, RightsTier.OPEN_ATTRIBUTION}
        and not (rights.license_id and rights.license_url)
    ):
        return "open tier needs a licence id and URL"
    return None
