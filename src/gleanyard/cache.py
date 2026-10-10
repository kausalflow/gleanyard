"""Content-addressed raw cache.

Every response body is stored once under its SHA-256 (``objects/ab/abcd...``).
Each run writes a manifest listing the URLs it fetched and the body each one
resolved to, so extraction can be rerun from the cache with no network. Per-URL
validators (ETag, Last-Modified) are kept so the next fetch can send a
conditional request and reuse the stored body on ``304 Not Modified``.

The newest manifest from a healthy run is kept as the source's last-good
baseline regardless of retention, so repair always has a page to compare with.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from gleanyard.storage import ObjectStore


class CachedResponse(BaseModel):
    url: str
    status_code: int
    content_type: str | None = None
    sha256: str
    size: int
    retrieved_at: datetime
    not_modified: bool = False


class Validators(BaseModel):
    etag: str | None = None
    last_modified: str | None = None
    sha256: str


class RunManifest(BaseModel):
    source_id: str
    run_id: str
    started_at: datetime
    responses: list[CachedResponse] = Field(default_factory=list)

    def page_hashes(self) -> dict[str, str]:
        return {response.url: response.sha256 for response in self.responses}


def body_key(sha256: str) -> str:
    return f"objects/{sha256[:2]}/{sha256}"


class RawCache:
    def __init__(self, store: ObjectStore, prefix: str = "raw") -> None:
        self.store = store
        self.prefix = prefix.rstrip("/")

    def _key(self, *parts: str) -> str:
        return "/".join([self.prefix, *parts])

    # Bodies

    def put_body(self, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        key = self._key(body_key(digest))
        if not self.store.exists(key):
            self.store.put(key, body)
        return digest

    def get_body(self, sha256: str) -> bytes:
        body = self.store.get(self._key(body_key(sha256)))
        if hashlib.sha256(body).hexdigest() != sha256:
            raise ValueError(f"cached body {sha256} is corrupt")
        return body

    # Conditional-request validators, per source and URL

    def _validators_key(self, source_id: str) -> str:
        return self._key("validators", f"{source_id}.json")

    def validators(self, source_id: str) -> dict[str, Validators]:
        key = self._validators_key(source_id)
        if not self.store.exists(key):
            return {}
        document = json.loads(self.store.get(key))
        return {url: Validators.model_validate(value) for url, value in document.items()}

    def save_validators(self, source_id: str, validators: dict[str, Validators]) -> None:
        document = {url: value.model_dump() for url, value in sorted(validators.items())}
        self.store.put(self._validators_key(source_id), json.dumps(document, indent=1).encode())

    # Run manifests

    def save_manifest(self, manifest: RunManifest) -> str:
        key = self._key("runs", manifest.source_id, f"{manifest.run_id}.json")
        self.store.put(key, manifest.model_dump_json(indent=1).encode())
        return key

    def manifests(self, source_id: str) -> list[RunManifest]:
        keys = self.store.list_keys(self._key("runs", source_id))
        runs = [RunManifest.model_validate_json(self.store.get(key)) for key in keys]
        return sorted(runs, key=lambda run: run.started_at)

    def latest_manifest(self, source_id: str) -> RunManifest | None:
        runs = self.manifests(source_id)
        return runs[-1] if runs else None

    # Last-good baseline

    def mark_good(self, manifest: RunManifest) -> None:
        key = self._key("good", f"{manifest.source_id}.json")
        self.store.put(key, manifest.model_dump_json(indent=1).encode())

    def last_good(self, source_id: str) -> RunManifest | None:
        key = self._key("good", f"{source_id}.json")
        if not self.store.exists(key):
            return None
        return RunManifest.model_validate_json(self.store.get(key))

    # Retention

    def retention_plan(
        self, source_id: str, raw_days: int, now: datetime | None = None
    ) -> list[str]:
        """Manifest keys older than ``raw_days``; the last-good run is never listed."""
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(days=raw_days)
        good = self.last_good(source_id)
        expired = []
        for key in self.store.list_keys(self._key("runs", source_id)):
            run = RunManifest.model_validate_json(self.store.get(key))
            if run.started_at < cutoff and (good is None or run.run_id != good.run_id):
                expired.append(key)
        return expired

    def apply_retention(self, expired_manifests: list[str]) -> list[str]:
        """Delete expired manifests, then any body no remaining manifest references."""
        for key in expired_manifests:
            self.store.delete(key)
        referenced: set[str] = set()
        for key in self.store.list_keys(self._key("runs")) + self.store.list_keys(
            self._key("good")
        ):
            referenced |= {
                response.sha256
                for response in RunManifest.model_validate_json(self.store.get(key)).responses
            }
        for key in self.store.list_keys(self._key("validators")):
            referenced |= {value["sha256"] for value in json.loads(self.store.get(key)).values()}
        deleted = []
        for key in self.store.list_keys(self._key("objects")):
            if key.rsplit("/", 1)[-1] not in referenced:
                self.store.delete(key)
                deleted.append(key)
        return deleted
