"""Rights review: the owner's decision on a source, asked one source (or shared review) at a time.

The assistant leaves every new source ``review_status: pending`` and ``enabled: false``.
``gleanyard rights review`` walks the pending ones, shows what the evidence says, suggests the
``allowed_actions`` and ``public_fields`` that fit the chosen tier and the schema's field
classes, and writes the answer into the source file. Sources that share an evidence folder are
asked once. Only the ``rights:`` block and the ``enabled:`` line of a file change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from gleanyard.config import SourceConfig
from gleanyard.project import Project
from gleanyard.rights import AllowedActions, RightsTier, SourceRights
from gleanyard.schema import FieldClass, ProjectSchema
from gleanyard.terms import evidence_id, latest_evidence

OPEN_TIERS = {RightsTier.OPEN, RightsTier.OPEN_ATTRIBUTION, RightsTier.LICENSED}
SILENT_TIERS = {RightsTier.INTERNAL_ONLY, RightsTier.UNKNOWN, RightsTier.BLOCKED}


@dataclass
class ReviewGroup:
    """Pending sources that share one evidence folder, with what that folder says."""

    folder: str
    sources: list[SourceConfig]
    evidence_path: str | None = None
    reading: str | None = None
    suggested_tier: RightsTier | None = None
    quotes: list[tuple[str, str]] = field(default_factory=list)


def pending_groups(project: Project, only: list[str] | None = None) -> list[ReviewGroup]:
    """Sources still ``pending`` (or the ones named in ``only``), grouped by evidence folder."""
    groups: dict[str, ReviewGroup] = {}
    for source in project.sources:
        if only and source.id not in only:
            continue
        if not only and source.rights.review_status != "pending":
            continue
        folder = evidence_id(source)
        if folder not in groups:
            groups[folder] = _group(project, folder, source)
        groups[folder].sources.append(source)
    return list(groups.values())


def _group(project: Project, folder: str, source: SourceConfig) -> ReviewGroup:
    group = ReviewGroup(folder=folder, sources=[])
    evidence = latest_evidence(project, source)
    if evidence is None:
        return group
    group.evidence_path = evidence.path
    try:
        data = yaml.safe_load((project.root / evidence.path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return group
    if not isinstance(data, dict):
        return group
    if isinstance(data.get("reading"), str):
        group.reading = data["reading"].strip()
    try:
        group.suggested_tier = RightsTier(data.get("suggested_tier"))
    except ValueError:
        group.suggested_tier = None
    for page in data.get("pages") or []:
        if isinstance(page, dict) and isinstance(page.get("url"), str):
            group.quotes.append((page["url"], str(page.get("quote") or "").strip()))
    return group


def suggest_rights(tier: RightsTier, schema: ProjectSchema, current: SourceRights) -> SourceRights:
    """Allowed actions and public fields that fit a tier; a starting point for the owner.

    Every required field is public whenever the tier exports at all, because export drops a
    record whose required field it may not show.
    """
    exports = tier not in SILENT_TIERS
    keep = tier != RightsTier.BLOCKED
    actions = AllowedActions(
        discover_urls=current.allowed_actions.discover_urls and keep,
        private_store=keep,
        normalize=keep,
        send_to_model=current.allowed_actions.send_to_model and keep,
        redistribute_facts=exports,
        redistribute_text=tier in OPEN_TIERS,
        redistribute_images=tier in OPEN_TIERS,
    )
    fields: list[str] = []
    if exports:
        for name, spec in schema.fields.items():
            klass = spec.field_class
            if klass == FieldClass.PERSONAL:
                continue
            if tier == RightsTier.LINK_ONLY:
                wanted = spec.required
            elif tier == RightsTier.FACTS_ONLY:
                wanted = klass == FieldClass.FACT
            else:
                wanted = True
            if wanted:
                fields.append(name)
    return current.model_copy(
        update={
            "effective_tier": tier,
            "allowed_actions": actions,
            "public_fields": fields,
            "attribution_required": tier == RightsTier.OPEN_ATTRIBUTION
            or current.attribution_required,
        }
    )


def rights_block(rights: SourceRights) -> dict[str, Any]:
    """The ``rights:`` mapping as it is written: defaults and empty values left out."""
    data = rights.model_dump(mode="json", exclude_defaults=True)
    data.setdefault("review_status", rights.review_status)
    data.setdefault("effective_tier", rights.effective_tier.value)
    for key in ("reviewed_at", "expires_at"):
        if key in data:
            data[key] = getattr(rights, key)  # a date, so YAML writes it unquoted
    actions = rights.allowed_actions.model_dump()
    data["allowed_actions"] = {key: value for key, value in actions.items() if value}
    if rights.public_fields:
        data["public_fields"] = list(rights.public_fields)
    order = [
        "review_status",
        "effective_tier",
        "license_id",
        "license_url",
        "attribution_required",
        "attribution_text",
        "allowed_actions",
        "public_fields",
        "reviewed_at",
        "expires_at",
        "notes",
    ]
    return {key: data[key] for key in order if key in data} | {
        key: value for key, value in data.items() if key not in order
    }


_TOP_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def write_decision(path: Path, rights: SourceRights, enabled: bool) -> None:
    """Replace the ``rights:`` block and the ``enabled:`` line; keep every other line."""
    lines = path.read_text(encoding="utf-8").splitlines()
    block = yaml.safe_dump(
        {"rights": rights_block(rights)}, sort_keys=False, allow_unicode=True, width=100
    ).splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("rights:")), None)
    if start is None:
        lines += block
    else:
        end = start + 1
        while end < len(lines) and not _TOP_KEY.match(lines[end]):
            end += 1
        # Comments and blank lines just above the next key belong to that key.
        while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
            end -= 1
        lines[start:end] = block
    flag = f"enabled: {'true' if enabled else 'false'}"
    at = next((i for i, line in enumerate(lines) if re.match(r"^enabled\s*:", line)), None)
    if at is None:
        after = next((i for i, line in enumerate(lines) if line.startswith("schema:")), -1)
        lines.insert(after + 1, flag)
    else:
        comment = re.search(r"\s+#.*$", lines[at])
        lines[at] = flag + (comment.group(0) if comment else "")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def decide(
    rights: SourceRights,
    status: str,
    today: date,
    notes: str | None = None,
) -> SourceRights:
    """Stamp the owner's answer on the rights: status, review date and notes."""
    update: dict[str, Any] = {"review_status": status, "reviewed_at": today}
    if notes is not None:
        update["notes"] = notes or None
    return rights.model_copy(update=update)
