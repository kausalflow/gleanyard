"""Object storage for raw responses and run state.

``LocalObjectStore`` writes under a directory; an S3-compatible store can be
added behind the same protocol without touching callers.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def delete(self, key: str) -> None: ...


def check_key(key: str) -> str:
    parts = PurePosixPath(key)
    if not key or parts.is_absolute() or ".." in parts.parts or str(parts) != key:
        raise ValueError(f"invalid object key: {key!r}")
    return key


class LocalObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> list[str]:
        base = self.root / prefix if prefix else self.root
        if not base.is_dir():
            return []
        return sorted(
            str(path.relative_to(self.root))
            for path in base.rglob("*")
            if path.is_file() and not path.name.endswith(".tmp")
        )

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
