"""The project schema: the one contract every record in a project must meet.

A project writes ``schema.yaml`` once. Every extraction mode produces plain
dicts keyed by schema field names; ``ProjectSchema.validate_record`` turns each
into a normalized, scope-checked ``Record`` or a list of reasons it was
rejected. Rejected records go to the review file, never to the output.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gleanyard.normalizers import NORMALIZERS, NormalizeError, is_missing


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldType(StrEnum):
    STRING = "string"
    TEXT = "text"
    DATE = "date"
    DATETIME = "datetime"
    TIME_TEXT = "time_text"
    POSTCODE = "postcode"
    URL = "url"
    MONEY = "money"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"


_ORDERED_TYPES = frozenset({"date", "datetime", "int", "float", "money"})


class FieldClass(StrEnum):
    """Decides what export may do with a field, independent of its type."""

    FACT = "fact"
    PROTECTED_TEXT = "protected_text"
    IMAGE = "image"
    PERSONAL = "personal"


class Scope(StrictModel):
    """Project bounds a value must fall inside; out-of-scope records are rejected.

    ``min`` and ``max`` accept ``today`` or ``today+N`` / ``today-N`` (days) for
    date fields, so "future events only" is ``min: today``.
    """

    min: float | str | None = None
    max: float | str | None = None
    pattern: str | None = None
    enum: list[str] | None = None

    @model_validator(mode="after")
    def check_pattern(self):
        if self.pattern is not None:
            re.compile(self.pattern)
        return self


class FieldSpec(StrictModel):
    type: FieldType = FieldType.STRING
    required: bool = False
    many: bool = False
    field_class: FieldClass = Field(default=FieldClass.FACT, alias="class")
    description: str | None = None
    aliases: list[str] = Field(
        default_factory=list,
        description="Source keys that mean this field (DTSTART, startDate); used by scaffold.",
    )
    max_length: int | None = Field(default=None, ge=1)
    country: str | None = Field(default=None, description="Country code for postcode fields.")
    currency: str | None = Field(default=None, description="Default currency for money fields.")
    scope: Scope | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def check_scope_fits_type(self):
        if self.scope is None or (self.scope.min is None and self.scope.max is None):
            return self
        if self.type not in _ORDERED_TYPES:
            raise ValueError(f"scope min/max only apply to {', '.join(sorted(_ORDERED_TYPES))}")
        for bound in (self.scope.min, self.scope.max):
            if bound is not None:
                _bound(self, bound, date.today())
        return self


class ProjectSchema(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: int = Field(default=1, ge=1)
    description: str | None = None
    identity: list[str] = Field(
        min_length=1,
        description="Fields that together identify one record within a source.",
    )
    fields: dict[str, FieldSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def check_references(self):
        for name in self.fields:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"field name {name!r} must be lower_snake_case")
        unknown = [name for name in self.identity if name not in self.fields]
        if unknown:
            raise ValueError(f"identity names unknown fields: {', '.join(unknown)}")
        optional = [name for name in self.identity if not self.fields[name].required]
        if optional and len(optional) == len(self.identity):
            raise ValueError("identity needs at least one required field")
        return self

    @property
    def required_fields(self) -> list[str]:
        return [name for name, spec in self.fields.items() if spec.required]

    def validate_record(
        self,
        values: dict[str, Any],
        *,
        source_id: str,
        base_url: str | None = None,
        today: date | None = None,
    ) -> ValidationResult:
        today = today or date.today()
        clean: dict[str, Any] = {}
        errors: list[FieldIssue] = []
        warnings: list[FieldIssue] = []
        for key in values:
            if key not in self.fields:
                errors.append(FieldIssue(field=key, reason="not a field in the schema"))
        for name, spec in self.fields.items():
            raw = values.get(name)
            if is_missing(raw):
                if spec.required:
                    errors.append(FieldIssue(field=name, reason="required field is missing"))
                continue
            try:
                value = _normalize(spec, raw, base_url)
                for item in value if spec.many else [value]:
                    _check_scope(spec, item, today)
            except NormalizeError as error:
                issue = FieldIssue(field=name, reason=str(error), value=_preview(raw))
                if spec.required or isinstance(error, ScopeError):
                    errors.append(issue)
                else:
                    warnings.append(issue)
                continue
            if not is_missing(value):
                clean[name] = value
            elif spec.required:
                errors.append(FieldIssue(field=name, reason="required field is empty"))
        if errors:
            return ValidationResult(record=None, errors=errors, warnings=warnings)
        return ValidationResult(
            record=Record(id=self.record_id(source_id, clean), values=clean),
            errors=[],
            warnings=warnings,
        )

    def record_id(self, source_id: str, values: dict[str, Any]) -> str:
        parts = [source_id, *(_identity_part(values.get(name)) for name in self.identity)]
        return "rec_" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]


class ScopeError(NormalizeError):
    """The value is valid but outside the project's bounds."""


class FieldIssue(StrictModel):
    field: str
    reason: str
    value: str | None = None


class Record(StrictModel):
    id: str
    values: dict[str, Any]


class ValidationResult(StrictModel):
    record: Record | None
    errors: list[FieldIssue]
    warnings: list[FieldIssue]

    @property
    def ok(self) -> bool:
        return self.record is not None


def _normalize(spec: FieldSpec, raw: Any, base_url: str | None) -> Any:
    if spec.many:
        items = raw if isinstance(raw, list) else [raw]
        values = [_normalize_one(spec, item, base_url) for item in items if not is_missing(item)]
        return list(dict.fromkeys(values)) if spec.type != FieldType.MONEY else values
    if isinstance(raw, list):
        if len(raw) != 1:
            raise NormalizeError(f"expected one value, got {len(raw)}")
        raw = raw[0]
    return _normalize_one(spec, raw, base_url)


def _normalize_one(spec: FieldSpec, raw: Any, base_url: str | None) -> Any:
    normalizer = NORMALIZERS[spec.type]
    if spec.type == FieldType.URL:
        value = normalizer(raw, base_url)
    elif spec.type == FieldType.POSTCODE:
        value = normalizer(raw, spec.country)
    elif spec.type == FieldType.MONEY:
        value = normalizer(raw, spec.currency)
    else:
        value = normalizer(raw)
    if spec.max_length and isinstance(value, str) and len(value) > spec.max_length:
        raise NormalizeError(f"longer than {spec.max_length} characters")
    return value


def _check_scope(spec: FieldSpec, value: Any, today: date) -> None:
    scope = spec.scope
    if scope is None:
        return
    if scope.enum is not None and value not in scope.enum:
        raise ScopeError(f"{value!r} is not one of the allowed values")
    if scope.pattern is not None and not re.search(scope.pattern, str(value)):
        raise ScopeError(f"{value!r} does not match {scope.pattern!r}")
    comparable = _comparable(spec, value)
    for bound, below in ((scope.min, True), (scope.max, False)):
        if bound is None:
            continue
        limit = _bound(spec, bound, today)
        if (below and comparable < limit) or (not below and comparable > limit):
            word = "before" if below else "after"
            if spec.type not in {FieldType.DATE, FieldType.DATETIME}:
                word = "below" if below else "above"
            raise ScopeError(f"{value!r} is {word} the project bound {bound!r}")


def _comparable(spec: FieldSpec, value: Any) -> Any:
    if spec.type in {FieldType.DATE, FieldType.DATETIME}:
        return date.fromisoformat(str(value)[:10])
    if spec.type == FieldType.MONEY:
        return float(value["amount"])
    return value


_RELATIVE = re.compile(r"^today(?:([+-])(\d+))?$")


def _bound(spec: FieldSpec, bound: float | str, today: date) -> Any:
    if spec.type in {FieldType.DATE, FieldType.DATETIME}:
        match = _RELATIVE.match(str(bound).strip())
        if match:
            sign, days = match.groups()
            offset = timedelta(days=int(days or 0))
            return today - offset if sign == "-" else today + offset
        return date.fromisoformat(str(bound))
    return float(bound)


def _identity_part(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(sorted(_identity_part(item) for item in value))
    if isinstance(value, dict):
        return ",".join(f"{key}={value[key]}" for key in sorted(value))
    return "" if value is None else str(value).casefold()


def _preview(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "..."
