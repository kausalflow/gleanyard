"""Load and check a collection project: schema, sources and their cross-references.

Every problem is reported as an ``Issue`` naming the file, the line and the
fix, so ``gleanyard validate --json`` gives an AI or a person something to act
on rather than a stack trace.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ValidationError

from gleanyard.config import (
    FieldsExtract,
    JsonLdExtract,
    LlmExtract,
    ProjectConfig,
    SelectorsExtract,
    SourceConfig,
)
from gleanyard.extract.fields import read_table
from gleanyard.schema import FieldClass, ProjectSchema

PROJECT_FILE = "gleanyard.yaml"


class Issue(BaseModel):
    file: str
    line: int | None = None
    path: str = ""
    message: str

    def __str__(self) -> str:
        where = f"{self.file}:{self.line}" if self.line else self.file
        return f"{where}: {self.path + ': ' if self.path else ''}{self.message}"


@dataclass
class Project:
    root: Path
    config: ProjectConfig
    schema: ProjectSchema | None
    sources: list[SourceConfig] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def host_status(self, url: str) -> dict[str, Any]:
        """Whether a URL's host is already a source, a candidate, excluded, or new."""
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            raise ValueError("not a URL")
        sources = [
            item.id
            for item in self.sources
            if host in {urlsplit(str(u)).hostname for u in (item.url, item.homepage) if u}
        ]
        candidate = (self.root / "candidates" / f"{host}.yaml").is_file()
        excluded = host in {name.lower() for name in self.config.excluded_hosts}
        status = (
            "excluded" if excluded else "source" if sources else "candidate" if candidate else "new"
        )
        return {"host": host, "status": status, "sources": sources, "candidate": candidate}


def load_project(root: Path) -> Project:
    root = root.resolve()
    issues: list[Issue] = []
    config = ProjectConfig()
    config_path = root / PROJECT_FILE
    if config_path.exists():
        parsed = _load_model(config_path, root, ProjectConfig, issues)
        config = parsed or config

    schema_path = root / config.schema_file
    schema = None
    if not schema_path.exists():
        issues.append(
            Issue(file=config.schema_file, message="schema file not found; create it first")
        )
    else:
        schema = _load_model(schema_path, root, ProjectSchema, issues)

    sources: list[SourceConfig] = []
    source_dir = root / config.sources
    paths = sorted(source_dir.rglob("*.yaml")) if source_dir.is_dir() else []
    seen: dict[str, str] = {}
    for path in paths:
        source = _load_model(path, root, SourceConfig, issues)
        if source is None:
            continue
        rel = _rel(path, root)
        if source.id != path.stem:
            issues.append(
                Issue(
                    file=rel,
                    line=_line(path, ["id"]),
                    path="id",
                    message=f"id {source.id!r} must match the file name {path.stem!r}",
                )
            )
        if source.id in seen:
            issues.append(
                Issue(
                    file=rel, path="id", message=f"duplicate source id, also in {seen[source.id]}"
                )
            )
        seen[source.id] = rel
        if schema is not None:
            issues.extend(_cross_check(source, schema, path, root))
        sources.append(source)
    partition = config.export_partition
    if schema is not None and partition is not None:
        if partition not in schema.fields:
            issues.append(
                Issue(
                    file=PROJECT_FILE,
                    path="export_partition",
                    message=f"export_partition names unknown field {partition!r}",
                )
            )
        elif schema.fields[partition].field_class == FieldClass.PERSONAL:
            issues.append(
                Issue(
                    file=PROJECT_FILE,
                    path="export_partition",
                    message=f"export_partition cannot use personal field {partition!r}",
                )
            )
    return Project(root=root, config=config, schema=schema, sources=sources, issues=issues)


def _cross_check(
    source: SourceConfig, schema: ProjectSchema, path: Path, root: Path
) -> list[Issue]:
    rel = _rel(path, root)
    issues: list[Issue] = []

    def issue(loc: list[str | int], message: str) -> None:
        issues.append(Issue(file=rel, line=_line(path, loc), path=_dotted(loc), message=message))

    if source.schema_name != schema.name:
        issue(
            ["schema"],
            f"schema is {source.schema_name!r} but the project schema is {schema.name!r}",
        )
    extract = source.extract
    for key in extract.defaults:
        if key not in schema.fields:
            issue(["extract", "defaults", key], _unknown_field(key, schema))
    mapped: set[str] = set(extract.defaults)
    if isinstance(extract, FieldsExtract | JsonLdExtract):
        for key in extract.map:
            if key not in schema.fields:
                issue(["extract", "map", key], _unknown_field(key, schema))
        mapped |= set(extract.map)
    if isinstance(extract, FieldsExtract):
        for index, case in enumerate(extract.cases):
            for key in case.set:
                if key not in schema.fields:
                    issue(["extract", "cases", index, "set", key], _unknown_field(key, schema))
        for key in extract.rewrite:
            if key not in schema.fields:
                issue(["extract", "rewrite", key], _unknown_field(key, schema))
        for index, join in enumerate(extract.join):
            table = root / join.file
            if not table.is_file():
                issue(["extract", "join", index, "file"], f"join file {join.file!r} not found")
                continue
            try:
                read_table(table, join)
            except (ValueError, UnicodeDecodeError, csv.Error) as error:
                issue(["extract", "join", index, "file"], str(error))
        if extract.cases:
            # A field counts as mapped when every case sets it, since one of them always applies.
            mapped |= set.intersection(*(set(case.set) for case in extract.cases))
        missing = [name for name in schema.required_fields if name not in mapped]
        if missing:
            issue(
                ["extract", "map"],
                f"required fields not mapped: {', '.join(missing)}; "
                "add them to extract.map or extract.defaults",
            )
    if isinstance(extract, SelectorsExtract) and not (root / extract.selectors).is_file():
        issue(["extract", "selectors"], f"selector file {extract.selectors!r} not found")
    if source.evidence is not None and not (root / "evidence" / source.evidence).is_dir():
        issue(["evidence"], f"evidence folder evidence/{source.evidence}/ not found")
    if source.filter is not None and source.filter.field not in schema.fields:
        issue(["filter", "field"], _unknown_field(source.filter.field, schema))
    actions = source.rights.allowed_actions
    if source.enabled and not (actions.private_store and actions.normalize):
        issue(
            ["enabled"],
            "an enabled source needs rights.allowed_actions.private_store and normalize; "
            "set enabled: false until rights are reviewed",
        )
    if source.enabled and isinstance(extract, LlmExtract) and not actions.send_to_model:
        issue(
            ["rights", "allowed_actions", "send_to_model"],
            "llm mode sends page text to a model provider; set send_to_model: true once the "
            "source's rights allow it",
        )
    unknown_public = [name for name in source.rights.public_fields if name not in schema.fields]
    if unknown_public:
        issue(["rights", "public_fields"], f"unknown fields: {', '.join(unknown_public)}")
    return issues


def _unknown_field(key: str, schema: ProjectSchema) -> str:
    return (
        f"{key!r} is not a field in schema {schema.name!r}; fields are: {', '.join(schema.fields)}"
    )


M = TypeVar("M", bound=BaseModel)


def _load_model(path: Path, root: Path, model: type[M], issues: list[Issue]) -> M | None:
    rel = _rel(path, root)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        issues.append(
            Issue(file=rel, line=mark.line + 1 if mark else None, message=f"invalid YAML: {error}")
        )
        return None
    if not isinstance(document, dict):
        issues.append(Issue(file=rel, message="expected a YAML mapping at the top level"))
        return None
    try:
        return model.model_validate(document)
    except ValidationError as error:
        for detail in error.errors():
            loc = _strip_union_tags(list(detail["loc"]))
            issues.append(
                Issue(
                    file=rel,
                    line=_line(path, loc),
                    path=_dotted(loc),
                    message=_explain(detail),
                )
            )
        return None


def _strip_union_tags(loc: list[Any]) -> list[Any]:
    # Discriminated unions put the tag in the location: extract.llm.llm.model.
    if len(loc) > 1 and loc[0] == "extract" and loc[1] in {"fields", "jsonld", "selectors", "llm"}:
        return [loc[0], *loc[2:]]
    return loc


def _explain(detail: dict[str, Any]) -> str:
    kind = detail["type"]
    if kind == "extra_forbidden":
        return "unknown key; remove it or check the spelling"
    if kind == "missing":
        return "required key is missing"
    if kind == "union_tag_invalid":
        return "mode must be one of: fields, jsonld, selectors, llm"
    message = detail["msg"]
    return message.removeprefix("Value error, ")


def _dotted(loc: list[str | int]) -> str:
    return ".".join(str(part) for part in loc)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _line(path: Path, loc: list[str | int]) -> int | None:
    """1-based line of the deepest YAML node that ``loc`` reaches."""
    try:
        node = yaml.compose(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    line = node.start_mark.line + 1 if node is not None else None
    for part in loc:
        if isinstance(node, yaml.MappingNode):
            match = next((item for item in node.value if item[0].value == str(part)), None)
            if match is None:
                break
            line = match[0].start_mark.line + 1
            node = match[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            if part >= len(node.value):
                break
            node = node.value[part]
            line = node.start_mark.line + 1
        else:
            break
    return line
