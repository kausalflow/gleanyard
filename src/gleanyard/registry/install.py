"""``gleanyard add`` and ``gleanyard search``: find a recipe and install it into a project.

A registry is a built site (``index.json`` plus the recipe files, usually on GitHub Pages) or
a local checkout of a registry repo. Installing a recipe writes the source, its mapping, its
fixture and its evidence under the project's own names, rewrites the source for the project
(its id, its schema, a ``recipe`` key that says where it came from), validates the project and
replays the fixture to write the project's own snapshot. If any of that fails, nothing is
left behind, unless ``force`` keeps the files to fix by hand.

The source always arrives with ``enabled: false`` and ``rights.review_status: pending``: the
recipe's evidence is someone else's reading of the terms, and the project's owner decides.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, Field

from gleanyard.config import SOURCE_ID
from gleanyard.project import load_project
from gleanyard.registry.model import INDEX_FILE, REGISTRY_FILE, IndexEntry, RegistryIndex
from gleanyard.registry.recipes import RECIPES, Recipe, install_files, remove_files
from gleanyard.registry.yamledit import Edit, edit_yaml
from gleanyard.replay import ReplayResult, replay_source

Fetch = Callable[[str], bytes]


class RegistryError(Exception):
    pass


class Registry:
    """A registry to read from: a built site's URL, or a folder (a checkout or a built site)."""

    def __init__(self, location: str, fetch: Fetch | None = None) -> None:
        self.location = location
        self.local = not location.startswith(("https://", "http://"))
        self.fetch = fetch or _fetch
        self._index: RegistryIndex | None = None

    def _url(self, path: str) -> str:
        return self.location.rstrip("/") + "/" + path

    def index(self) -> RegistryIndex:
        if self._index is not None:
            return self._index
        if self.local:
            root = Path(self.location)
            if (root / INDEX_FILE).is_file():
                self._index = RegistryIndex.model_validate_json((root / INDEX_FILE).read_bytes())
            elif (root / REGISTRY_FILE).is_file():
                self._index = _index_of_checkout(root)
            else:
                raise RegistryError(f"{root} has neither {INDEX_FILE} nor {REGISTRY_FILE}")
        else:
            try:
                body = self.fetch(self._url(INDEX_FILE))
            except httpx.HTTPError as error:
                raise RegistryError(f"cannot read {self._url(INDEX_FILE)}: {error}") from error
            try:
                self._index = RegistryIndex.model_validate_json(body)
            except ValueError as error:
                raise RegistryError(f"{self._url(INDEX_FILE)} is not a registry index") from error
        return self._index

    def recipe(self, recipe_id: str) -> tuple[IndexEntry, Recipe]:
        entry = self.index().get(recipe_id)
        if entry is None:
            raise RegistryError(
                f"no recipe {recipe_id!r} in the registry; `gleanyard search` lists them"
            )
        files: dict[str, bytes] = {}
        for name in entry.files:
            path = f"{RECIPES.as_posix()}/{entry.id}/{name}"
            if self.local:
                files[name] = (Path(self.location) / path).read_bytes()
            else:
                try:
                    files[name] = self.fetch(self._url(path))
                except httpx.HTTPError as error:
                    raise RegistryError(f"cannot read {self._url(path)}: {error}") from error
        recipe = Recipe(id=entry.id, files=files)
        if recipe.digest != entry.digest:
            raise RegistryError(
                f"recipe {entry.id!r} does not match its digest in the index; try again later"
            )
        return entry, recipe


def _fetch(url: str) -> bytes:
    response = httpx.get(url, timeout=30, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _index_of_checkout(root: Path) -> RegistryIndex:
    from gleanyard.registry.site import build_index, check_registry

    check = check_registry(root)
    if not check.ok:
        problems = check.problems + [p for recipe in check.recipes for p in recipe.problems]
        raise RegistryError(
            "the registry checkout does not pass `gleanyard registry check`: "
            + "; ".join(problems[:3])
        )
    index, _ = build_index(check, health=False)
    return index


def search(index: RegistryIndex, query: str = "", schema: str | None = None) -> list[IndexEntry]:
    """Recipes whose id, name, description, host or tags hold every word of ``query``."""
    words = query.lower().split()
    found = []
    for entry in index.recipes:
        if schema and entry.schema_name != schema:
            continue
        text = " ".join([entry.id, entry.name, entry.description, entry.host, *entry.tags]).lower()
        if all(word in text for word in words):
            found.append(entry)
    return found


class AddResult(BaseModel):
    recipe: str
    source_id: str
    ok: bool = False
    written: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    replay: ReplayResult | None = None


EVIDENCE_HEADER = (
    "# Copied by `gleanyard add` from the registry recipe {recipe}. This is someone else's\n"
    "# reading of the terms: read the pages yourself, add your own evidence file, and only\n"
    "# then set the source's rights and enable it.\n"
)
SOURCE_NOTE = (
    "\n# Installed with `gleanyard add`. The rights above are the recipe's suggestion: review\n"
    "# the evidence, set them for this project, then enable the source.\n"
)


def add_recipe(
    root: Path,
    entry: IndexEntry,
    recipe: Recipe,
    *,
    source_id: str | None = None,
    registry: str | None = None,
    force: bool = False,
) -> AddResult:
    """Install ``recipe`` into the project at ``root``."""
    source_id = source_id or entry.id
    result = AddResult(recipe=entry.id, source_id=source_id)
    project = load_project(root)
    if project.schema is None:
        result.problems.append("the project has no schema; run `gleanyard validate` first")
        return result
    if not _valid_id(source_id):
        result.problems.append(f"source id {source_id!r} must be lowercase letters, digits, dashes")
        return result
    if any(item.id == source_id for item in project.sources):
        result.problems.append(f"the project already has a source {source_id!r}; use --id")
        return result
    host = project.host_status(entry.url)
    if host["status"] in {"source", "excluded"}:
        why = (
            f"{host['host']} is already a source here ({', '.join(host['sources'])})"
            if host["status"] == "source"
            else f"{host['host']} is in the project's excluded_hosts"
        )
        if not force:
            result.problems.append(why + "; --force installs it anyway")
            return result
        result.notes.append(why)
    if entry.schema_name != project.schema.name:
        result.notes.append(
            f"the recipe was written for schema {entry.schema_name!r}; it is checked against "
            f"this project's {project.schema.name!r}"
        )
    text = edit_yaml(
        recipe.files["source.yaml"].decode("utf-8"),
        [
            Edit(("id",), source_id),
            Edit(("schema",), project.schema.name),
            Edit(("enabled",), False),
            Edit(("rights", "review_status"), "pending"),
            Edit(("evidence",), drop=True),
            *(
                [Edit(("extract", "selectors"), f"mappings/{source_id}.json")]
                if "mapping.json" in recipe.files
                else []
            ),
            Edit(("recipe",), drop=True),
        ],
    )
    origin = {"id": entry.id, "digest": entry.digest}
    if registry:
        origin["registry"] = registry
    text = (
        text.rstrip("\n") + "\n" + SOURCE_NOTE + yaml.safe_dump({"recipe": origin}, sort_keys=False)
    )
    try:
        written = install_files(
            project.root,
            recipe,
            source_id,
            source_text=text,
            evidence_header=EVIDENCE_HEADER.format(recipe=entry.id),
        )
    except FileExistsError as error:
        result.problems.append(f"would overwrite {error}; use --id to pick another name")
        return result
    result.written = [str(path.relative_to(project.root)) for path in written]

    project = load_project(root)
    names = {*result.written}
    result.problems += [str(issue) for issue in project.issues if issue.file in names]
    source = next((item for item in project.sources if item.id == source_id), None)
    if source is not None and not result.problems:
        day = _fixture_day(recipe)
        replay = replay_source(project, source, update=True, today=day)
        result.replay = replay
        if replay.verdict == "pass":
            snapshot = project.root / "tests" / "fixtures" / f"{source_id}.expected.json"
            written.append(snapshot)
            result.written.append(str(snapshot.relative_to(project.root)))
        else:
            reasons = "; ".join(replay.reasons) or replay.verdict
            result.problems.append(
                f"the fixture does not pass with this project's schema: {reasons}"
            )
    if result.problems and not force:
        remove_files(project.root, written)
        result.written = []
        return result
    result.ok = not result.problems
    return result


def _fixture_day(recipe: Recipe) -> date | None:
    try:
        return date.fromisoformat(json.loads(recipe.files["expected.json"])["today"])
    except (KeyError, ValueError, TypeError):
        return None


def _valid_id(value: str) -> bool:
    return re.match(SOURCE_ID, value) is not None
