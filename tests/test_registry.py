"""The source registry: recipes are checked offline, built into a static site, and installed
into projects disabled and with their rights pending."""

import json
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from test_html import FARM_MAPPING, make_project
from test_second_project import EXAMPLE as DATASETS
from typer.testing import CliRunner

from gleanyard.cli import app
from gleanyard.init import init_project, init_registry
from gleanyard.project import load_project
from gleanyard.registry.health import read_health
from gleanyard.registry.install import Registry, RegistryError, add_recipe, search
from gleanyard.registry.model import RegistryConfig
from gleanyard.registry.pack import pack_source
from gleanyard.registry.site import build_index, check_registry, write_site
from gleanyard.registry.yamledit import Edit, edit_yaml
from gleanyard.replay import replay_project
from gleanyard.run import SourceRun
from gleanyard.status import build_status

EXAMPLE = Path(__file__).parents[1] / "examples" / "registry"
FARM_DAY = date(2026, 10, 1)
NOW = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
runner = CliRunner()


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    shutil.copytree(EXAMPLE, root)
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    init_project(root, "catalog", "dataset")
    shutil.copy(DATASETS / "schema.yaml", root / "schema.yaml")
    return root


def recipe_file(registry: Path, recipe: str, name: str = "source.yaml") -> Path:
    return registry / "recipes" / recipe / name


def problems(root: Path) -> list[str]:
    check = check_registry(root)
    return check.problems + [p for recipe in check.recipes for p in recipe.problems]


# --- check -------------------------------------------------------------------------------


def test_the_example_registry_passes():
    check = check_registry(EXAMPLE)
    assert check.ok, check.summary()
    assert [recipe.id for recipe in check.recipes] == ["city-feed", "lab-pages", "portal-api"]


def test_an_internal_only_source_is_refused(registry):
    path = recipe_file(registry, "portal-api")
    path.write_text(path.read_text().replace("facts_only", "internal_only"))
    assert any("internal_only cannot be in a public registry" in p for p in problems(registry))


def test_a_recipe_must_be_disabled_and_pending(registry):
    path = recipe_file(registry, "portal-api")
    text = path.read_text().replace("enabled: false", "enabled: true")
    path.write_text(text.replace("review_status: pending", "review_status: approved"))
    found = problems(registry)
    assert any("enabled: false" in p for p in found)
    assert any("review_status: pending" in p for p in found)


def test_a_fixture_that_no_longer_gives_the_snapshot_fails(registry):
    path = recipe_file(registry, "portal-api", "fixture.json")
    path.write_text(path.read_text().replace("Synthetic air quality", "Something else"))
    assert any("does not give expected.json" in p for p in problems(registry))


def test_stray_files_missing_evidence_and_big_fixtures_are_reported(registry):
    recipe = registry / "recipes" / "city-feed"
    (recipe / "notes.txt").write_text("hello")
    shutil.rmtree(recipe / "evidence")
    (recipe / "fixture.rss").write_bytes(b"x" * (300 * 1024))
    found = " | ".join(problems(registry))
    assert "notes.txt: not part of a recipe" in found
    assert "add the terms you read" in found
    assert "trim it to 256 KB" in found


def test_a_source_error_names_the_recipe_file(registry):
    path = recipe_file(registry, "portal-api")
    path.write_text(path.read_text().replace("delivery: json", "delivery: jsn"))
    assert any(p.startswith("recipes/portal-api/source.yaml:") for p in problems(registry))


def test_an_empty_registry_passes(tmp_path):
    root = tmp_path / "new-registry"
    init_registry(root, "new-registry", "https://o.github.io/new-registry/")
    assert check_registry(root).ok
    workflows = sorted(path.name for path in (root / ".github/workflows").iterdir())
    assert workflows == ["check.yml", "publish.yml"]
    check = yaml.safe_load((root / ".github/workflows/check.yml").read_text())
    assert check[True]["pull_request"]["paths"]  # only runs when recipes or schemas change
    assert "--registry https://o.github.io/new-registry/" in (root / "README.md").read_text()


# --- build and health --------------------------------------------------------------------


def status_json(*entries: dict) -> bytes:
    sources = [
        {"id": "s", "enabled": True, "health": "ok", "checked_at": NOW.isoformat()} | entry
        for entry in entries
    ]
    report = {"generated_at": NOW.isoformat(), "schema_name": "x", "healthy": True}
    return json.dumps(report | {"sources": sources}).encode()


def test_health_counts_fresh_enabled_reports_only():
    config = RegistryConfig(
        name="r", users=["a/one", "https://example.org/two/status.json"], fresh_days=14
    )
    old = (NOW - timedelta(days=30)).isoformat()
    reports = {
        "https://raw.githubusercontent.com/a/one/HEAD/status.json": status_json(
            {"recipe": "portal-api", "last_success_at": NOW.isoformat()},
            {"recipe": "city-feed", "health": "failing"},
            {"recipe": "lab-pages", "checked_at": old},  # stale
        ),
        "https://example.org/two/status.json": status_json(
            {"recipe": "portal-api", "health": "degraded"},
            {"recipe": "city-feed", "health": "failing"},
            {"recipe": "lab-pages", "enabled": False, "health": "disabled"},
            {"recipe": "unknown-recipe"},
        ),
        "https://raw.githubusercontent.com/b/found/main/status.json": b"not json",
    }
    config.discover = {"topic": "gleanyard"}
    health, read, notes = read_health(
        RegistryConfig.model_validate(config.model_dump()),
        {"portal-api", "city-feed", "lab-pages"},
        fetch=lambda url: reports[url],
        search=lambda topic: ["https://raw.githubusercontent.com/b/found/main/status.json"],
        now=NOW,
    )
    assert read == 2 and notes == ["1 of 3 status.json file(s) could not be read"]
    assert (health["portal-api"].level, health["portal-api"].users) == ("degraded", 2)
    assert health["portal-api"].last_success_at == NOW
    assert (health["city-feed"].level, health["city-feed"].failing) == ("failing", 2)
    assert (health["lab-pages"].level, health["lab-pages"].users) == ("unknown", 0)


def test_build_writes_a_site_that_add_can_read(registry, tmp_path):
    check = check_registry(registry)
    index, _ = build_index(check, health=False, now=NOW)
    site = tmp_path / "site"
    write_site(registry, site, check, index)
    assert (site / "index.html").read_text().count("<details>") == 3
    assert (site / "recipes/lab-pages/evidence/2026-10-21-terms.yaml").is_file()
    assert (site / "schemas/dataset.yaml").is_file()
    data = json.loads((site / "index.json").read_text())
    entry = next(item for item in data["recipes"] if item["id"] == "portal-api")
    assert entry["schema"] == "dataset" and entry["host"] == "data.example"
    assert entry["tier"] == "facts_only" and entry["fixture_today"] == "2026-10-21"
    assert "source.yaml" in entry["files"] and entry["health"]["level"] == "unknown"


def test_build_refuses_a_registry_that_fails_its_check(registry, tmp_path):
    path = recipe_file(registry, "portal-api")
    path.write_text(path.read_text().replace("enabled: false", "enabled: true"))
    result = runner.invoke(app, ["registry", "build", str(registry), "--out", str(tmp_path / "s")])
    assert result.exit_code == 1 and "not building" in result.output


# --- search and add ----------------------------------------------------------------------


def served(site: Path):
    """A fetch that serves a built site as if it were on GitHub Pages."""

    def fetch(url: str) -> bytes:
        path = site / url.removeprefix("https://reg.example/")
        if not path.is_file():
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=None)
        return path.read_bytes()

    return fetch


@pytest.fixture
def site(registry, tmp_path) -> Path:
    check = check_registry(registry)
    index, _ = build_index(check, health=False, now=NOW)
    write_site(registry, tmp_path / "site", check, index)
    return tmp_path / "site"


def test_search_matches_every_word():
    index = Registry(str(EXAMPLE)).index()
    assert [entry.id for entry in search(index, "synthetic portal")] == ["portal-api"]
    assert len(search(index, "", schema="dataset")) == 3
    assert search(index, "", schema="event") == []


def test_add_from_a_site_installs_a_disabled_pending_source(project, site):
    registry = Registry("https://reg.example/", fetch=served(site))
    entry, recipe = registry.recipe("portal-api")
    result = add_recipe(project, entry, recipe, registry="https://reg.example/")
    assert result.ok, result
    assert set(result.written) == {
        "sources/portal-api.yaml",
        "tests/fixtures/portal-api.json",
        "tests/fixtures/portal-api.expected.json",
        "evidence/portal-api/2026-10-21-terms.yaml",
    }
    loaded = load_project(project)
    assert loaded.ok, loaded.issues
    source = loaded.sources[0]
    assert not source.enabled and source.rights.review_status == "pending"
    assert source.recipe.id == "portal-api" and source.recipe.registry == "https://reg.example/"
    evidence = (project / "evidence/portal-api/2026-10-21-terms.yaml").read_text()
    assert evidence.startswith("# Copied by `gleanyard add`")
    assert runner.invoke(app, ["test", str(project)]).exit_code == 0


def test_a_selectors_recipe_round_trips_under_a_new_id(tmp_path):
    # A selectors source from one project becomes a recipe, then a source in another.
    origin = make_project(
        tmp_path / "farm",
        {
            "delivery": "html",
            "url": "https://farm.example/events",
            "extract": {"mode": "selectors", "selectors": "mappings/m.json"},
            "rights": {"effective_tier": "facts_only"},
        },
        FARM_MAPPING,
    )
    (origin / "tests" / "fixtures").mkdir(parents=True)
    page = (Path(__file__).parent / "fixtures" / "farm.html").read_text()
    rows = [line for line in page.splitlines() if "<th>" not in line and "broken" not in line]
    (origin / "tests/fixtures/s.html").write_text("\n".join(rows))
    (origin / "evidence" / "s").mkdir(parents=True)
    (origin / "evidence" / "s" / "2026-10-20-terms.yaml").write_text("pages: []\n")
    assert replay_project(load_project(origin), update=True, today=FARM_DAY).passed
    registry = tmp_path / "reg"
    init_registry(registry, "reg")
    packed = pack_source(
        origin,
        "s",
        registry,
        maintainers=["someone"],
        description="Farm events",
        fixture_basis="Made up for the test.",
    )
    assert packed.ok, packed.problems
    assert "selectors: mappings/s.json" in recipe_file(registry, "s").read_text()

    target = make_project(tmp_path / "other", {"delivery": "rss", "url": "https://x.example/f"})
    (target / "sources" / "s.yaml").unlink()
    entry, recipe = Registry(str(registry)).recipe("s")
    result = add_recipe(target, entry, recipe, source_id="farm-events")
    assert result.ok, result.problems
    assert (target / "mappings" / "farm-events.json").is_file()
    source = load_project(target).sources[0]
    assert source.extract.selectors == "mappings/farm-events.json"


def test_add_rolls_back_when_the_project_schema_does_not_fit(project, registry):
    schema = yaml.safe_load((project / "schema.yaml").read_text())
    del schema["fields"]["license"]
    (project / "schema.yaml").write_text(yaml.safe_dump(schema))
    entry, recipe = Registry(str(registry)).recipe("portal-api")
    result = add_recipe(project, entry, recipe)
    assert not result.ok and result.written == []
    assert any("'license' is not a field" in p for p in result.problems)
    assert not (project / "sources/portal-api.yaml").exists()
    assert not (project / "evidence/portal-api").exists()


def test_add_with_force_keeps_the_files_to_fix(project, registry):
    schema = yaml.safe_load((project / "schema.yaml").read_text())
    del schema["fields"]["license"]
    (project / "schema.yaml").write_text(yaml.safe_dump(schema))
    entry, recipe = Registry(str(registry)).recipe("portal-api")
    result = add_recipe(project, entry, recipe, force=True)
    assert not result.ok and "sources/portal-api.yaml" in result.written


def test_add_refuses_a_host_the_project_already_collects(project, registry):
    entry, recipe = Registry(str(registry)).recipe("portal-api")
    assert add_recipe(project, entry, recipe).ok
    again = add_recipe(project, entry, recipe, source_id="portal-api-2")
    assert not again.ok and "already a source here (portal-api)" in again.problems[0]
    forced = add_recipe(project, entry, recipe, source_id="portal-api-2", force=True)
    assert forced.ok and "portal-api-2" in {s.id for s in load_project(project).sources}


def test_add_checks_the_digest(site):
    index = json.loads((site / "index.json").read_text())
    index["recipes"][0]["digest"] = "000000000000"
    (site / "index.json").write_text(json.dumps(index))
    with pytest.raises(RegistryError, match="digest"):
        Registry("https://reg.example/", fetch=served(site)).recipe(index["recipes"][0]["id"])


def test_cli_search_and_add(project, registry):
    found = runner.invoke(app, ["search", "feed", "--registry", str(registry)])
    assert found.exit_code == 0 and found.output.startswith("city-feed")
    added = runner.invoke(
        app, ["add", "city-feed", str(project), "--id", "city", "--registry", str(registry)]
    )
    assert added.exit_code == 0, added.output
    assert "added 'city' (disabled, rights pending)" in added.output
    text = (project / "sources/city.yaml").read_text()
    assert "registry:" not in text  # a local folder is not worth recording
    missing = runner.invoke(app, ["add", "nope", str(project), "--registry", str(registry)])
    assert missing.exit_code == 2 and "no recipe 'nope'" in missing.output


def test_status_reports_the_recipe(project, registry):
    entry, recipe = Registry(str(registry)).recipe("portal-api")
    add_recipe(project, entry, recipe)
    loaded = load_project(project)
    run = SourceRun(source_id="portal-api", ok=True, records=2)
    status = build_status(loaded, [run], None, NOW)
    assert status.sources[0].recipe == "portal-api"
    assert status.sources[0].recipe_digest == entry.digest


# --- pack --------------------------------------------------------------------------------


def datasets_with_evidence(tmp_path: Path, tier: str = "facts_only") -> Path:
    root = tmp_path / "datasets"
    shutil.copytree(DATASETS, root)
    path = root / "sources" / "portal-api.yaml"
    text = path.read_text().replace("facts_only", tier)
    path.write_text(text.replace("rights:\n", "rights:\n  notes: approved by me\n"))
    (root / "evidence" / "portal-api").mkdir(parents=True)
    (root / "evidence" / "portal-api" / "2026-10-21-terms.yaml").write_text(
        "pages:\n  - url: https://data.example/terms\n    quote: made up\n"
    )
    return root


def test_pack_writes_a_recipe_that_passes(tmp_path):
    registry = tmp_path / "reg"
    init_registry(registry, "reg")
    result = pack_source(
        datasets_with_evidence(tmp_path),
        "portal-api",
        registry,
        maintainers=["someone"],
        description="A made-up API",
        fixture_basis="Made up for the test.",
    )
    assert result.ok, result.problems
    source = yaml.safe_load(recipe_file(registry, "portal-api").read_text())
    assert source["enabled"] is False and source["rights"]["review_status"] == "pending"
    assert "notes" not in source["rights"]
    assert (registry / "schemas" / "dataset.yaml").is_file()


def test_pack_refuses_internal_only(tmp_path):
    registry = tmp_path / "reg"
    init_registry(registry, "reg")
    result = pack_source(
        datasets_with_evidence(tmp_path, tier="internal_only"),
        "portal-api",
        registry,
        maintainers=["someone"],
        description="A made-up API",
        fixture_basis="Made up for the test.",
    )
    assert not result.ok and "internal_only cannot be shared" in result.problems[0]
    assert not (registry / "recipes" / "portal-api").exists()


# --- yaml edits --------------------------------------------------------------------------


def test_edits_keep_comments():
    text = (
        "id: a  # first\n# about the rights\nrights:\n  review_status: approved\n"
        "  notes: >-\n    long\n    note\n  public_fields: [x]\nenabled: true\n"
    )
    edited = edit_yaml(
        text,
        [
            Edit(("rights", "review_status"), "pending"),
            Edit(("rights", "notes"), drop=True),
            Edit(("enabled",), False),
        ],
    )
    assert edited == (
        "id: a  # first\n# about the rights\nrights:\n  review_status: pending\n"
        "  public_fields: [x]\nenabled: false\n"
    )


def test_edits_fall_back_to_a_dump_for_flow_style():
    edited = edit_yaml(
        "rights: {review_status: approved}\n", [Edit(("rights", "review_status"), "pending")]
    )
    assert yaml.safe_load(edited) == {"rights": {"review_status": "pending"}}
