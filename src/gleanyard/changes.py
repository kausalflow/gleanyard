"""What changed between collects: a change log per day and a public feed.

Each collect compares a source's new records with the ones it stored last time, by record id,
and appends one line per added, changed or removed record to ``changes/<date>.jsonl`` under
the storage root (``data/``). Records keep ``first_seen``, the time a record with that id was
first collected; ``last_seen`` is its latest ``provenance.retrieved_at``.

``gleanyard export`` turns the recent changes into a JSON Feed (https://jsonfeed.org) next to
the export, ``exports/changes.json`` by default. The feed goes through the same rights filter
as the export, against the current source config: a change shows only the exported fields,
and only for sources whose rights allow export.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from gleanyard.collect import OutputRecord
from gleanyard.export import public_record
from gleanyard.project import Project
from gleanyard.storage import LocalObjectStore

CHANGES = "changes"
FEED_DAYS = 30  # days of changes in the public feed
FEED_LIMIT = 500  # items in the public feed, newest first

Kind = Literal["added", "changed", "removed"]


class Change(BaseModel):
    at: datetime
    source_id: str
    change: Kind
    id: str
    fields: list[str] = Field(
        default_factory=list, description="For a changed record: the fields that differ."
    )
    record: OutputRecord = Field(description="The new record; for a removal, the last one.")


def carry_first_seen(previous: list[OutputRecord], current: list[OutputRecord]) -> None:
    """Give each current record the ``first_seen`` of the stored record with its id."""
    before = {record.id: record for record in previous}
    for record in current:
        old = before.get(record.id)
        if old is None:
            record.first_seen = record.provenance.retrieved_at
        else:
            record.first_seen = old.first_seen or old.provenance.retrieved_at


def diff_records(
    source_id: str, previous: list[OutputRecord], current: list[OutputRecord], at: datetime
) -> list[Change]:
    """Added, changed and removed records, in that order."""
    before = {record.id: record for record in previous}
    after = {record.id: record for record in current}
    changes = [
        Change(at=at, source_id=source_id, change="added", id=key, record=record)
        for key, record in after.items()
        if key not in before
    ]
    for key, record in after.items():
        old = before.get(key)
        if old is None or old.values == record.values:
            continue
        fields = sorted(
            name
            for name in set(old.values) | set(record.values)
            if old.values.get(name) != record.values.get(name)
        )
        changes.append(
            Change(
                at=at, source_id=source_id, change="changed", id=key, fields=fields, record=record
            )
        )
    changes += [
        Change(at=at, source_id=source_id, change="removed", id=key, record=record)
        for key, record in before.items()
        if key not in after
    ]
    return changes


def append_changes(store: LocalObjectStore, changes: list[Change]) -> None:
    by_day: dict[str, list[Change]] = {}
    for change in changes:
        by_day.setdefault(f"{change.at:%Y-%m-%d}", []).append(change)
    for day, items in by_day.items():
        key = f"{CHANGES}/{day}.jsonl"
        old = store.get(key) if store.exists(key) else b""
        new = "".join(item.model_dump_json() + "\n" for item in items).encode()
        store.put(key, old + new)


def prune_changes(store: LocalObjectStore, keep_days: int, today: date) -> list[str]:
    """Delete change files older than ``keep_days``; return their keys."""
    cutoff = today - timedelta(days=keep_days)
    removed = []
    for key in store.list_keys(CHANGES):
        day = _day(key)
        if day is not None and day < cutoff:
            store.delete(key)
            removed.append(key)
    return removed


def load_changes(
    store: LocalObjectStore, since: date | None = None, sources: set[str] | None = None
) -> list[Change]:
    """Logged changes from ``since`` on, oldest first."""
    changes = []
    for key in store.list_keys(CHANGES):
        day = _day(key)
        if day is None or (since is not None and day < since):
            continue
        for line in store.get(key).decode().split("\n"):
            if not line.strip():
                continue
            change = Change.model_validate_json(line)
            if sources is None or change.source_id in sources:
                changes.append(change)
    return sorted(changes, key=lambda change: change.at)


def change_feed(
    project: Project, changes: list[Change], today: date, title: str | None = None
) -> dict[str, Any]:
    """A JSON Feed of the changes the current rights allow to publish, newest first."""
    assert project.schema is not None
    rights = {source.id: source.rights for source in project.sources}
    identity = project.schema.identity
    items = []
    for change in reversed(changes):
        row = public_record(
            change.record, project.schema, rights, project.config.rights_policy, today
        )
        if row is None:
            continue
        name = " / ".join(str(row[field]) for field in identity if field in row) or change.id
        item: dict[str, Any] = {
            "id": f"{change.id}:{change.change}:{change.at.isoformat()}",
            "title": f"{change.change.capitalize()}: {name}",
            "date_published": change.at.isoformat(),
            "_gleanyard": {"change": change.change, "record": row},
        }
        if change.fields:
            public = [field for field in change.fields if field in row]
            if not public:
                continue  # only fields that are not exported changed
            item["_gleanyard"]["fields"] = public
        if change.record.provenance.url:
            item["url"] = change.record.provenance.url
        items.append(item)
        if len(items) >= FEED_LIMIT:
            break
    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": title or f"{project.schema.name}: changes",
        "description": (
            f"Records added, changed or removed in the last {FEED_DAYS} days, "
            "filtered by each source's rights."
        ),
        "items": items,
    }


def _day(key: str) -> date | None:
    name = key.rsplit("/", 1)[-1].removesuffix(".jsonl")
    try:
        return date.fromisoformat(name)
    except ValueError:
        return None
