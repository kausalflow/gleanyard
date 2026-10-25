"""``gleanyard registry check`` and ``build``: the registry's checks and its static site.

The site is what `gleanyard add` and `gleanyard search` read: ``index.json``, every recipe's
files under ``recipes/<id>/``, the schemas, and an ``index.html`` to browse, all static, so
GitHub Pages serves it at no cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from gleanyard.registry.health import Fetch, Search, read_health
from gleanyard.registry.model import INDEX_FILE, IndexEntry, RegistryConfig, RegistryIndex
from gleanyard.registry.recipes import (
    Recipe,
    check_recipe,
    copy_recipes,
    index_entry,
    load_config,
    load_recipes,
    load_schemas,
)


@dataclass
class RegistryCheck:
    config: RegistryConfig | None
    recipes: list[Recipe] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and all(not recipe.problems for recipe in self.recipes)

    def summary(self) -> dict:
        return {
            "ok": self.ok,
            "problems": self.problems,
            "recipes": [
                {"id": recipe.id, "ok": not recipe.problems, "problems": recipe.problems}
                for recipe in self.recipes
            ],
        }


def check_registry(root: Path) -> RegistryCheck:
    """Check registry.yaml, the schemas and every recipe, offline."""
    try:
        config = load_config(root)
    except ValueError as error:
        return RegistryCheck(config=None, problems=[str(error)])
    result = RegistryCheck(config=config)
    schemas = load_schemas(root)
    result.recipes = [check_recipe(recipe, schemas) for recipe in load_recipes(root)]
    return result


def build_index(
    check: RegistryCheck,
    *,
    health: bool = True,
    fetch: Fetch | None = None,
    search: Search | None = None,
    now: datetime | None = None,
) -> tuple[RegistryIndex, list[str]]:
    assert check.config is not None and check.ok
    now = now or datetime.now(UTC)
    entries = [index_entry(recipe) for recipe in check.recipes]
    index = RegistryIndex(name=check.config.name, generated_at=now, recipes=entries)
    problems: list[str] = []
    if health:
        kwargs = {"fetch": fetch} if fetch else {}
        found, index.reports_read, problems = read_health(
            check.config, {entry.id for entry in entries}, search=search, now=now, **kwargs
        )
        for entry in entries:
            entry.health = found[entry.id]
    return index, problems


def write_site(root: Path, out: Path, check: RegistryCheck, index: RegistryIndex) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    copy_recipes(root, out, check.recipes)
    (out / INDEX_FILE).write_text(index.model_dump_json(indent=2, by_alias=True) + "\n")
    (out / "index.html").write_text(render_page(index, check.config), encoding="utf-8")
    return [out / INDEX_FILE, out / "index.html"]


CSS = """
:root{--bg:#fff;--fg:#1c1f24;--muted:#5d6672;--line:#d9dee5;--card:#f6f8fa;
--green:#1a7f37;--amber:#b25e00;--red:#cf222e;--link:#0550ae}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--fg:#e6e9ee;--muted:#9aa4b0;
--line:#2b333d;--card:#161c23;--green:#3fb950;--amber:#d29922;--red:#f85149;--link:#79b8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.5 system-ui,sans-serif}
main{max-width:56rem;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.5rem;margin:0 0 .25rem}h2{font-size:1.125rem;margin:2rem 0 .5rem}
.muted{color:var(--muted);font-size:.875rem}a{color:var(--link)}
details{background:var(--card);border:1px solid var(--line);border-radius:8px;margin:.75rem 0;
padding:0 1rem}summary{cursor:pointer;padding:.75rem 0;display:flex;gap:.6rem;
align-items:center;flex-wrap:wrap}.name{font-weight:600}.grow{flex:1}
.tag{font-size:.75rem;padding:.05rem .45rem;border-radius:999px;border:1px solid currentColor}
.tag.ok{color:var(--green)}.tag.degraded{color:var(--amber)}.tag.failing{color:var(--red)}
.tag.unknown{color:var(--muted)}
code{font-size:.875rem;overflow-wrap:anywhere}dl{display:grid;grid-template-columns:auto 1fr;
gap:.25rem .75rem;margin:0 0 1rem;font-size:.875rem}dt{color:var(--muted)}dd{margin:0;
overflow-wrap:anywhere}
"""


def render_page(index: RegistryIndex, config: RegistryConfig | None) -> str:
    groups: dict[str, list[IndexEntry]] = defaultdict(list)
    for entry in index.recipes:
        groups[entry.schema_name].append(entry)
    registry = f" --registry {config.url}" if config and config.url else ""
    sections = "".join(
        f"<h2>Schema <code>{escape(name)}</code></h2>"
        + "".join(_recipe(entry, registry) for entry in entries)
        for name, entries in sorted(groups.items())
    )
    title = escape(index.name)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><main>
<h1>{title}</h1>
<p class="muted">{len(index.recipes)} recipe(s). Health from {index.reports_read} project
report(s). Updated {index.generated_at:%Y-%m-%d %H:%M} UTC.
<a href="{INDEX_FILE}">index.json</a></p>
<p>Install a recipe into a gleanyard project with
<code>gleanyard add &lt;id&gt;{escape(registry)}</code>.
It arrives disabled, with its rights pending: read its evidence, decide, then enable it.</p>
{sections or "<p>No recipes yet.</p>"}
</main></body></html>
"""


def _recipe(entry: IndexEntry, registry: str) -> str:
    health = entry.health
    if health.users:
        counts = f"{health.ok} ok, {health.degraded} degraded, {health.failing} failing"
        label = f"{health.level} in {health.users} project(s)"
    else:
        counts, label = "no project reports it yet", "unknown"
    evidence = [name for name in entry.files if name.startswith("evidence/")]
    links = ", ".join(
        f'<a href="recipes/{escape(entry.id)}/{escape(name)}">{escape(name)}</a>'
        for name in ["source.yaml", *evidence]
    )
    tags = ", ".join(escape(tag) for tag in entry.tags) or "none"
    return f"""<details><summary><span class="name">{escape(entry.name)}</span>
<code class="grow">{escape(entry.id)}</code><span class="tag {health.level}">{escape(label)}</span>
</summary>
<p>{escape(entry.description)}</p>
<dl><dt>Install</dt><dd><code>gleanyard add {escape(entry.id)}{escape(registry)}</code></dd>
<dt>Source</dt><dd><a href="{escape(entry.url)}">{escape(entry.host)}</a>,
{escape(entry.delivery)} / {escape(entry.mode)}</dd>
<dt>Suggested tier</dt><dd>{escape(entry.tier)}</dd>
<dt>Health</dt><dd>{escape(counts)}</dd>
<dt>Tags</dt><dd>{tags}</dd>
<dt>Files</dt><dd>{links}</dd>
<dt>Maintainers</dt><dd>{escape(", ".join(entry.maintainers))}</dd></dl>
</details>"""
