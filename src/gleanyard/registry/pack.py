"""``gleanyard registry pack``: turn a project's working source into a recipe.

Run inside a registry checkout. It copies the source, its mapping, its fixture with the
snapshot and its evidence into ``recipes/<id>/``, and the project's schema into ``schemas/``
when the registry does not have it yet. The copy is disabled, its rights review is reset to
pending and the owner's review notes and dates are dropped, since they were one project's
decision. A source whose tier may not be shared (``internal_only``, ``unknown``, ``blocked``)
or that needs a model (``llm``) is refused, and so is one without a fixture or evidence.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from gleanyard.config import LlmExtract, SelectorsExtract
from gleanyard.project import load_project
from gleanyard.registry.model import PUBLISHABLE_TIERS, RecipeMeta
from gleanyard.registry.recipes import (
    RECIPES,
    SCHEMAS,
    Recipe,
    check_recipe,
    load_schemas,
    read_files,
)
from gleanyard.registry.yamledit import Edit, edit_yaml
from gleanyard.replay import fixture_path, snapshot_path
from gleanyard.terms import EVIDENCE


class PackResult(BaseModel):
    recipe: str
    ok: bool = False
    written: list[str] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


def pack_source(
    project_root: Path,
    source_id: str,
    registry_root: Path,
    *,
    maintainers: list[str],
    description: str,
    fixture_basis: str,
    tags: list[str] | None = None,
) -> PackResult:
    result = PackResult(recipe=source_id)
    project = load_project(project_root)
    source = next((item for item in project.sources if item.id == source_id), None)
    if source is None or project.schema is None:
        result.problems.append(f"no source {source_id!r} in {project_root} (or no schema)")
        return result
    tier = source.rights.effective_tier
    if tier not in PUBLISHABLE_TIERS:
        result.problems.append(
            f"tier {tier.value} cannot be shared in a public registry; this source stays in "
            "its project"
        )
    if isinstance(source.extract, LlmExtract):
        result.problems.append("llm mode cannot be replayed offline, so it cannot be a recipe")
    fixture = fixture_path(project, source)
    snapshot = snapshot_path(project, source)
    if fixture is None or not snapshot.is_file():
        result.problems.append(
            f"the source needs a fixture and a snapshot: save a page as tests/fixtures/"
            f"{source_id}.<ext> and run `gleanyard test -s {source_id} --update`"
        )
    evidence = project.root / EVIDENCE / (source.evidence or source.id)
    papers = sorted(evidence.glob("*.yaml")) if evidence.is_dir() else []
    if not papers:
        result.problems.append(f"no terms review in {evidence.relative_to(project.root)}/")
    target = registry_root / RECIPES / source_id
    if target.exists():
        result.problems.append(f"{target.relative_to(registry_root)} exists already")
    schemas = load_schemas(registry_root)
    schema_text = (project.root / project.config.schema_file).read_bytes()
    known = schemas.get(project.schema.name)
    if known is not None and yaml.safe_load(known) != yaml.safe_load(schema_text):
        result.problems.append(
            f"the registry has a different schema {project.schema.name!r}; align the project's "
            "schema with it, or give the project's schema another name"
        )
    if result.problems:
        return result
    assert fixture is not None

    path = project.root / project.config.sources
    source_file = next(path.rglob(f"{source_id}.yaml"))
    edits = [
        Edit(("enabled",), False),
        Edit(("rights", "review_status"), "pending"),
        Edit(("rights", "reviewed_at"), drop=True),
        Edit(("rights", "expires_at"), drop=True),
        Edit(("rights", "notes"), drop=True),
        Edit(("evidence",), drop=True),
        Edit(("recipe",), drop=True),
    ]
    if isinstance(source.extract, SelectorsExtract):
        edits.append(Edit(("extract", "selectors"), f"mappings/{source_id}.json"))
    files: dict[str, bytes] = {
        "source.yaml": edit_yaml(source_file.read_text(encoding="utf-8"), edits).encode(),
        f"fixture{fixture.suffix}": fixture.read_bytes(),
        "expected.json": snapshot.read_bytes(),
    }
    if isinstance(source.extract, SelectorsExtract):
        files["mapping.json"] = (project.root / source.extract.selectors).read_bytes()
    for paper in papers:
        files[f"evidence/{paper.name}"] = paper.read_bytes()
    try:
        meta = RecipeMeta(
            name=source.name or source_id,
            description=description,
            schema=project.schema.name,
            tags=tags or [],
            maintainers=maintainers,
            fixture_basis=fixture_basis,
        )
    except ValidationError as error:
        result.problems += [
            f"recipe.yaml: {'.'.join(str(p) for p in detail['loc'])}: {detail['msg']}"
            for detail in error.errors()
        ]
        return result
    files["recipe.yaml"] = yaml.safe_dump(
        meta.model_dump(by_alias=True, exclude_defaults=True), sort_keys=False, allow_unicode=True
    ).encode()

    for name, body in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        result.written.append(str(path.relative_to(registry_root)))
    if known is None:
        schema_path = registry_root / SCHEMAS / f"{project.schema.name}.yaml"
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_bytes(schema_text)
        result.written.append(str(schema_path.relative_to(registry_root)))
    recipe = Recipe(id=source_id, files=read_files(target))
    checked = check_recipe(recipe, load_schemas(registry_root))
    result.problems = checked.problems
    result.ok = not result.problems
    return result
