"""Typed errors that every collection repo reports the same way.

Each error carries a stable ``kind`` so health checks, ``status.json`` and the
status hub can tell a broken page layout from a network outage without parsing
messages.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorKind(StrEnum):
    STRUCTURE_CHANGED = "structure_changed"
    PAGINATION_CHANGED = "pagination_changed"
    FILTER_IGNORED = "filter_ignored"
    BOUNDS_EXCEEDED = "bounds_exceeded"
    TRANSPORT = "transport"
    BLOCKED = "blocked"
    MODEL = "model"


class GleanyardError(Exception):
    """Base class for errors raised by the package."""


class ConfigError(GleanyardError):
    """A project, schema or source file is invalid."""


class SourceError(GleanyardError):
    """A source failed during collection; ``kind`` says how."""

    kind: ErrorKind

    def __init__(self, source_id: str, message: str) -> None:
        super().__init__(f"{source_id}: {message}")
        self.source_id = source_id


class StructureChanged(SourceError):
    """The delivered document no longer has the shape the extraction expects."""

    kind = ErrorKind.STRUCTURE_CHANGED


class PaginationChanged(SourceError):
    """Pagination no longer behaves as configured, so records may be missing."""

    kind = ErrorKind.PAGINATION_CHANGED


class FilterIgnored(SourceError):
    """The source ignored a query filter and returned out-of-bounds data."""

    kind = ErrorKind.FILTER_IGNORED


class BoundsExceeded(SourceError):
    """A configured limit (bytes, pages, records, cost) was reached."""

    kind = ErrorKind.BOUNDS_EXCEEDED


class TransportError(SourceError):
    """The request failed at the network or HTTP level."""

    kind = ErrorKind.TRANSPORT


class Blocked(SourceError):
    """The host refused access (robots.txt, 401/403, or a challenge page)."""

    kind = ErrorKind.BLOCKED


class ModelError(SourceError):
    """The language model was unavailable, failed, or returned unusable output."""

    kind = ErrorKind.MODEL
