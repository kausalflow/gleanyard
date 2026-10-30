"""Read a registry from disk and check its recipes offline.

A recipe lives in ``recipes/<id>/``:

- ``recipe.yaml``: name, description, schema, maintainers, and why its fixture may be public;
- ``source.yaml``: the source file, disabled and with its rights pending;
- ``mapping.json``: the selector mapping, for a ``selectors`` source only;
- ``fixture.<ext>``: one saved page, and ``expected.json``: the records it gives;
- ``evidence/<date>-terms.yaml``: the terms someone read, as in a project.

``check_recipe`` puts these into a throwaway project with the registry's copy of the schema,
validates it and replays the fixture, the same checks `validate` and `test` make in a project.
Nothing is fetched.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from gleanyard.config import SOURCE_ID, LlmExtract, SelectorsExtract, SourceConfig
from gleanyard.project import Issue, load_project
from gleanyard.registry.model import (
    PUBLISHABLE_TIERS,
    REGISTRY_FILE,
    IndexEntry,
    RecipeMeta,
    RegistryConfig,
)
from gleanyard.replay import EXPECTED_SUFFIX, FIXTURES, replay_source

RECIPES = Path("recipes")
SCHEMAS = Path("schemas")
MAX_FIXTURE_BYTES = 256 * 1024
MAX_RECIPE_BYTES = 512 * 1024
FIXTURE_STEM = "fixture"


@dataclass
class Recipe:
    id: str
    files: dict[str, bytes]
    meta: RecipeMeta | None = None
    source: SourceConfig | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def fixture(self) -> str | None:
        names = sorted(name for name in self.files if name.startswith(FIXTURE_STEM + "."))
        return names[0] if names else None

    @property
    def digest(self) -> str:
        return recipe_digest(self.files)


def recipe_digest(files: dict[str, bytes]) -> str:
    """A short hash of every file in the recipe, in name order."""
    hasher = hashlib.sha256()
    for name in sorted(files):
        hasher.update(name.encode() + b"\0" + files[name] + b"\0")
    return hasher.hexdigest()[:12]


def read_files(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def load_config(root: Path) -> RegistryConfig:
    path = root / REGISTRY_FILE
    try:
        return RegistryConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    except (yaml.YAMLError, ValidationError) as error:
        raise ValueError(f"{REGISTRY_FILE}: {error}") from error


def load_schemas(root: Path) -> dict[str, bytes]:
    """The registry's schemas by name (from each file's ``name``), as written."""
    schemas: dict[str, bytes] = {}
    directory = root / SCHEMAS
    for path in sorted(directory.glob("*.yaml")) if directory.is_dir() else []:
        data = yaml.safe_load(path.read_bytes()) or {}
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            schemas[data["name"]] = path.read_bytes()
    return schemas


def load_recipes(root: Path) -> list[Recipe]:
    directory = root / RECIPES
    if not directory.is_dir():
        return []
    return [
        Recipe(id=path.name, files=read_files(path))
        for path in sorted(directory.iterdir())
        if path.is_dir()
    ]


def check_recipe(recipe: Recipe, schemas: dict[str, bytes]) -> Recipe:
    """Fill ``recipe.meta``, ``recipe.source`` and ``recipe.problems``."""
    problems = recipe.problems
    where = f"{RECIPES.as_posix()}/{recipe.id}"
    if not _matches(SOURCE_ID, recipe.id):
        problems.append(f"{where}: the folder name must be lowercase letters, digits and dashes")
        return recipe
    for required in ("recipe.yaml", "source.yaml", "expected.json"):
        if required not in recipe.files:
            problems.append(f"{where}/{required}: missing")
    try:
        meta_doc = yaml.safe_load(recipe.files.get("recipe.yaml", b"")) or {}
        recipe.meta = RecipeMeta.model_validate(meta_doc)
    except (yaml.YAMLError, ValidationError) as error:
        if "recipe.yaml" in recipe.files:
            problems.append(f"{where}/recipe.yaml: {_short_error(error)}")
    _check_files(recipe, where)
    if "source.yaml" not in recipe.files:
        return recipe
    try:
        raw = yaml.safe_load(recipe.files["source.yaml"]) or {}
    except yaml.YAMLError as error:
        problems.append(f"{where}/source.yaml: invalid YAML: {error}")
        return recipe
    if not isinstance(raw, dict):
        problems.append(f"{where}/source.yaml: expected a YAML mapping")
        return recipe
    for key in ("evidence", "recipe"):
        if key in raw:
            problems.append(
                f"{where}/source.yaml: remove `{key}:`; the recipe's evidence/ folder and "
                "`gleanyard add` take care of it"
            )
    schema_name = recipe.meta.schema_name if recipe.meta else raw.get("schema")
    schema = schemas.get(str(schema_name))
    if schema is None:
        problems.append(f"{where}: schema {schema_name!r} is not in {SCHEMAS.as_posix()}/")
        return recipe
    with tempfile.TemporaryDirectory() as tmp:
        project_root = Path(tmp)
        install_files(project_root, recipe, recipe.id, schema=schema)
        project = load_project(project_root)
        renames = _renames(recipe)
        for issue in project.issues:
            problems.append(_rename(issue, renames))
        source = next((item for item in project.sources if item.id == recipe.id), None)
        recipe.source = source
        if source is None:
            if not project.issues:
                problems.append(f"{where}/source.yaml: id must be {recipe.id!r}")
            return recipe
        _check_source(recipe, source, where)
        if project.ok and "expected.json" in recipe.files and recipe.fixture:
            result = replay_source(project, source)
            if result.verdict != "pass":
                reasons = "; ".join(result.reasons) or result.verdict
                problems.append(f"{where}: the fixture does not give expected.json: {reasons}")
    return recipe


def _check_source(recipe: Recipe, source: SourceConfig, where: str) -> None:
    problems = recipe.problems
    if source.enabled:
        problems.append(f"{where}/source.yaml: set `enabled: false`; each project enables it")
    if source.rights.review_status != "pending":
        problems.append(
            f"{where}/source.yaml: set `rights.review_status: pending`; the recipe's rights are "
            "a suggestion, each project approves its own"
        )
    tier = source.rights.effective_tier
    if tier not in PUBLISHABLE_TIERS:
        problems.append(
            f"{where}/source.yaml: tier {tier.value} cannot be in a public registry; only "
            f"sources whose records may be shared at all can ({_tiers()})"
        )
    if isinstance(source.extract, LlmExtract):
        problems.append(
            f"{where}/source.yaml: llm mode cannot be replayed offline, so the registry cannot "
            "check it; use fields, jsonld or selectors"
        )
    if isinstance(source.extract, SelectorsExtract):
        if source.extract.selectors != f"mappings/{recipe.id}.json":
            problems.append(
                f"{where}/source.yaml: extract.selectors must be mappings/{recipe.id}.json"
            )
        if "mapping.json" not in recipe.files:
            problems.append(f"{where}/mapping.json: missing; a selectors source needs it")
    elif "mapping.json" in recipe.files:
        problems.append(f"{where}/mapping.json: only a selectors source has a mapping")
    if recipe.meta and recipe.meta.schema_name != source.schema_name:
        problems.append(
            f"{where}: recipe.yaml names schema {recipe.meta.schema_name!r}, "
            f"source.yaml {source.schema_name!r}"
        )


def _check_files(recipe: Recipe, where: str) -> None:
    problems = recipe.problems
    fixtures = [name for name in recipe.files if name.startswith(FIXTURE_STEM + ".")]
    if not fixtures:
        problems.append(f"{where}: missing a fixture page (fixture.html, fixture.json, ...)")
    elif len(fixtures) > 1:
        problems.append(f"{where}: one fixture only, found {', '.join(sorted(fixtures))}")
    for name in fixtures:
        if len(recipe.files[name]) > MAX_FIXTURE_BYTES:
            problems.append(
                f"{where}/{name}: {len(recipe.files[name]) // 1024} KB; trim it to "
                f"{MAX_FIXTURE_BYTES // 1024} KB or less"
            )
    evidence = [name for name in recipe.files if name.startswith("evidence/")]
    if not any(name.endswith(".yaml") for name in evidence):
        problems.append(f"{where}/evidence/: add the terms you read as <date>-terms.yaml")
    for name in evidence:
        if not name.endswith(".yaml") or name.count("/") != 1:
            problems.append(f"{where}/{name}: evidence holds <date>-terms.yaml files only")
            continue
        try:
            data = yaml.safe_load(recipe.files[name])
        except yaml.YAMLError as error:
            problems.append(f"{where}/{name}: invalid YAML: {error}")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
            problems.append(f"{where}/{name}: list the pages you read under `pages`")
    if "expected.json" in recipe.files:
        try:
            snapshot = json.loads(recipe.files["expected.json"])
            if not snapshot.get("records"):
                problems.append(f"{where}/expected.json: holds no records")
        except (ValueError, AttributeError):
            problems.append(f"{where}/expected.json: not a snapshot from `gleanyard test`")
    known = {"recipe.yaml", "source.yaml", "mapping.json", "expected.json"}
    for name in sorted(recipe.files):
        if name in known or name in fixtures or name in evidence:
            continue
        problems.append(f"{where}/{name}: not part of a recipe; remove it")
    total = sum(len(body) for body in recipe.files.values())
    if total > MAX_RECIPE_BYTES:
        problems.append(f"{where}: {total // 1024} KB in all; keep a recipe under 512 KB")


def install_files(
    root: Path,
    recipe: Recipe,
    source_id: str,
    *,
    schema: bytes | None = None,
    source_text: str | None = None,
    evidence_header: str = "",
) -> list[Path]:
    """Write a recipe's files into a project layout; returns the paths written.

    Refuses to overwrite: every target must not exist yet.
    """
    targets: dict[Path, bytes] = {}
    if schema is not None:
        targets[root / "schema.yaml"] = schema
    text = source_text if source_text is not None else recipe.files["source.yaml"].decode()
    targets[root / "sources" / f"{source_id}.yaml"] = text.encode()
    if "mapping.json" in recipe.files:
        targets[root / "mappings" / f"{source_id}.json"] = recipe.files["mapping.json"]
    if recipe.fixture:
        suffix = Path(recipe.fixture).suffix
        targets[root / FIXTURES / f"{source_id}{suffix}"] = recipe.files[recipe.fixture]
        if schema is not None and "expected.json" in recipe.files:
            # In the check's throwaway project the recipe's snapshot is the one to match.
            targets[root / FIXTURES / f"{source_id}{EXPECTED_SUFFIX}"] = recipe.files[
                "expected.json"
            ]
    for name, body in recipe.files.items():
        if name.startswith("evidence/") and name.endswith(".yaml"):
            header = evidence_header.encode()
            targets[root / "evidence" / source_id / Path(name).name] = header + body
    clashes = [path for path in targets if path.exists()]
    if clashes:
        raise FileExistsError(", ".join(str(path) for path in clashes))
    for path, body in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return list(targets)


def remove_files(root: Path, paths: list[Path]) -> None:
    """Undo ``install_files``: remove the files and any folder left empty."""
    for path in paths:
        path.unlink(missing_ok=True)
    for path in paths:
        parent = path.parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def index_entry(recipe: Recipe) -> IndexEntry:
    """The recipe's line in ``index.json``; the recipe must have passed the check."""
    assert recipe.meta is not None and recipe.source is not None
    source, meta = recipe.source, recipe.meta
    today = None
    if "expected.json" in recipe.files:
        today = json.loads(recipe.files["expected.json"]).get("today")
    return IndexEntry(
        id=recipe.id,
        name=meta.name,
        description=meta.description,
        schema=meta.schema_name,
        tags=meta.tags,
        maintainers=meta.maintainers,
        host=urlsplit(str(source.url)).hostname or "",
        url=str(source.url),
        delivery=source.delivery.value,
        mode=source.extract.mode,
        tier=source.rights.effective_tier.value,
        coverage=source.coverage.model_dump(exclude_defaults=True),
        files=sorted(recipe.files),
        digest=recipe.digest,
        fixture_today=today,
    )


def copy_recipes(root: Path, out: Path, recipes: list[Recipe]) -> None:
    """Copy each recipe's files and the schemas into the built site."""
    for recipe in recipes:
        target = out / RECIPES / recipe.id
        if target.exists():
            shutil.rmtree(target)
        for name, body in recipe.files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    if (root / SCHEMAS).is_dir():
        shutil.copytree(root / SCHEMAS, out / SCHEMAS, dirs_exist_ok=True)


def _renames(recipe: Recipe) -> dict[str, str]:
    where = f"{RECIPES.as_posix()}/{recipe.id}"
    renames = {
        f"sources/{recipe.id}.yaml": f"{where}/source.yaml",
        f"mappings/{recipe.id}.json": f"{where}/mapping.json",
    }
    if recipe.fixture:
        suffix = Path(recipe.fixture).suffix
        renames[f"{FIXTURES.as_posix()}/{recipe.id}{suffix}"] = f"{where}/{recipe.fixture}"
    return renames


def _rename(issue: Issue, renames: dict[str, str]) -> str:
    issue = issue.model_copy(update={"file": renames.get(issue.file, issue.file)})
    return str(issue)


def _matches(pattern: str, value: str) -> bool:
    return re.match(pattern, value) is not None


def _short_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"
            for detail in error.errors()
        )
    return str(error)


def _tiers() -> str:
    return ", ".join(sorted(tier.value for tier in PUBLISHABLE_TIERS))
