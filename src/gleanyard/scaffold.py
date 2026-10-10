"""``gleanyard scaffold URL``: draft a source file from a probe.

For feeds the draft already maps schema fields to the feed's own keys by matching field
names and schema ``aliases`` (``DTSTART`` to ``start_date``), so the assistant only fills
the gaps. For pages with schema.org data it pins ``jsonld``. For plain pages it writes
the ``selectors`` or ``llm`` skeleton and leaves the mapping to the setup procedure.
Drafts are always ``enabled: false`` with rights pending.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import BaseModel, Field

from gleanyard.config import SourceConfig
from gleanyard.errors import SourceError
from gleanyard.probe import Option, ProbeReport, probe
from gleanyard.readers import read_items
from gleanyard.schema import ProjectSchema


class Scaffold(BaseModel):
    source_id: str
    option: Option
    yaml_text: str
    unmapped_required: list[str] = Field(default_factory=list)
    unmapped_optional: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def scaffold(
    url: str,
    source_id: str,
    schema: ProjectSchema,
    *,
    user_agent: str,
    rank: int = 1,
    client: httpx.Client | None = None,
    report: ProbeReport | None = None,
) -> Scaffold:
    report = report or probe(url, user_agent=user_agent, client=client)
    if not report.robots_allows:
        raise SourceError(source_id, "robots.txt disallows this URL")
    if not report.options:
        raise SourceError(source_id, "the probe found nothing to collect from this URL")
    if not 1 <= rank <= len(report.options):
        raise SourceError(
            source_id, f"option {rank} does not exist; probe found {len(report.options)}"
        )
    option = report.options[rank - 1]
    extract: dict[str, Any]
    notes: list[str] = []
    unmapped_required: list[str] = []
    unmapped_optional: list[str] = []
    reader: dict[str, Any] = {}
    if option.mode == "fields":
        keys, reader = _feed_keys(option, source_id, schema, user_agent, client)
        mapping = _match(schema, keys)
        extract = {"mode": "fields", "map": mapping}
        unmapped_required = [n for n, f in schema.fields.items() if f.required and n not in mapping]
        unmapped_optional = [
            n for n, f in schema.fields.items() if not f.required and n not in mapping
        ]
        if unmapped_required:
            notes.append(
                "required fields not found in the feed: add them to extract.map or "
                f"extract.defaults ({', '.join(unmapped_required)})"
            )
    elif option.mode == "jsonld":
        extract = {"mode": "jsonld", "types": option.types}
        notes.append("fields are read by name and schema aliases; add extract.map for the rest")
    elif option.mode == "selectors":
        extract = {"mode": "selectors", "selectors": f"mappings/{source_id}.json"}
        notes.append(
            f"draft mappings/{source_id}.json with crawl4ai (see CLAUDE.md, set up a source)"
        )
    else:
        extract = {
            "mode": "llm",
            "llm": {"model": "anthropic:claude-haiku-5-5"},
        }
        notes.append("choose the model; llm mode needs rights.allowed_actions.send_to_model")
    document: dict[str, Any] = {
        "id": source_id,
        "schema": schema.name,
        "enabled": False,
        "delivery": option.delivery,
        "url": option.url,
    }
    if option.url.rstrip("/") != url.rstrip("/") and _same_host(option.url, url):
        document["homepage"] = url
    if reader:
        document["reader"] = reader
    document["extract"] = extract
    document["rights"] = {
        "review_status": "pending",
        "notes": f"Drafted from a probe on {date.today().isoformat()}; review terms first.",
    }
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    return Scaffold(
        source_id=source_id,
        option=option,
        yaml_text=text,
        unmapped_required=unmapped_required,
        unmapped_optional=unmapped_optional,
        notes=notes,
    )


def _same_host(a: str, b: str) -> bool:
    return urlsplit(a).hostname == urlsplit(b).hostname


def _feed_keys(
    option: Option,
    source_id: str,
    schema: ProjectSchema,
    user_agent: str,
    client: httpx.Client | None,
) -> tuple[list[str], dict[str, Any]]:
    """Keys a feed offers, read with the real reader, and any reader options it needs."""
    own = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        body = own.get(option.url, headers={"User-Agent": user_agent}).content
    except httpx.HTTPError as error:
        raise SourceError(source_id, f"could not read the feed: {error}") from error
    finally:
        if client is None:
            own.close()
    reader: dict[str, Any] = {}
    candidates = [""] if option.delivery != "json" else _json_paths(body)
    for items_path in candidates:
        if option.delivery == "json":
            reader = {"items_path": items_path} if items_path else {}
        source = SourceConfig.model_validate(
            {
                "id": source_id,
                "schema": schema.name,
                "delivery": option.delivery,
                "url": option.url,
                "reader": reader,
                "extract": {"mode": "fields", "map": {}},
                "rights": {},
            }
        )
        try:
            items = read_items(source, body, today=date.today())
        except SourceError:
            continue
        if items:
            return _flat_keys(items[:20]), reader
    return [], reader


def _json_paths(body: bytes) -> list[str]:
    import json

    try:
        data = json.loads(body)
    except ValueError:
        return [""]
    if isinstance(data, list):
        return [""]
    return (
        [key for key, value in data.items() if isinstance(value, list)]
        if isinstance(data, dict)
        else [""]
    )


def _flat_keys(items: list[dict[str, Any]]) -> list[str]:
    """Keys that carry a value in at least one sampled item (readers emit empty keys too)."""
    keys: list[str] = []
    for item in items:
        for key, value in item.items():
            if value in (None, "", [], {}):
                continue
            names = [key, *(f"{key}.{sub}" for sub in value)] if isinstance(value, dict) else [key]
            keys.extend(name for name in names if name not in keys)
    return keys


def _match(schema: ProjectSchema, keys: list[str]) -> dict[str, str]:
    """Schema field to feed key, by the field's name or any of its aliases."""
    by_lower = {key.lower(): key for key in keys}
    mapping: dict[str, str] = {}
    for name, spec in schema.fields.items():
        for candidate in (name, *spec.aliases):
            if candidate.lower() in by_lower:
                mapping[name] = by_lower[candidate.lower()]
                break
    return mapping
