"""The ``gleanyard`` command line. Every command supports ``--json``."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import click
import typer

from gleanyard import __version__
from gleanyard.badges import write_badges
from gleanyard.changes import load_changes
from gleanyard.config import ProjectConfig, SourceConfig
from gleanyard.dataherb import write_job_status, write_metadata
from gleanyard.errors import ConfigError, SourceError
from gleanyard.hub.alerts import SyncResult, compute_alerts, sync_issues, weekly_digest
from gleanyard.hub.check import check_hub
from gleanyard.hub.config import load_hub_config
from gleanyard.hub.dashboard import render_dashboard, render_summary
from gleanyard.hub.github import HttpGitHub
from gleanyard.hub.state import HubState, collect_state
from gleanyard.hub.viewer import VIEWER_DIR, write_viewers
from gleanyard.init import init_hub, init_project, init_registry
from gleanyard.mapping import draft_mapping
from gleanyard.probe import probe as probe_url
from gleanyard.project import load_project
from gleanyard.registry.health import search_topic
from gleanyard.registry.install import Registry, RegistryError, add_recipe
from gleanyard.registry.install import search as search_index
from gleanyard.registry.model import DEFAULT_REGISTRY
from gleanyard.registry.pack import pack_source
from gleanyard.registry.site import build_index, check_registry, write_site
from gleanyard.repair import check_repair, plan_repair
from gleanyard.replay import replay_project
from gleanyard.research import MAX_CANDIDATES, check_research
from gleanyard.review import (
    OPEN_TIERS,
    decide,
    pending_groups,
    suggest_rights,
    write_decision,
)
from gleanyard.rights import AllowedActions, RightsTier
from gleanyard.run import collect_source, export_project, record_store
from gleanyard.scaffold import scaffold as scaffold_url
from gleanyard.schema import ProjectSchema
from gleanyard.skill import skill_state, write_skill
from gleanyard.status import build_status, load_status, write_status
from gleanyard.terms import check_terms
from gleanyard.tryout import try_source

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
hub_app = typer.Typer(no_args_is_help=True, help="The status hub: watch many collection repos.")
app.add_typer(hub_app, name="hub")
repair_app = typer.Typer(
    no_args_is_help=True, help="Automated repair: which sources to fix, and whether a fix holds."
)
app.add_typer(repair_app, name="repair")
research_app = typer.Typer(
    no_args_is_help=True, help="Automated research: whether proposed candidates may be a PR."
)
app.add_typer(research_app, name="research")
registry_app = typer.Typer(
    no_args_is_help=True, help="A source registry: check recipes, build its site, add one."
)
app.add_typer(registry_app, name="registry")
rights_app = typer.Typer(
    no_args_is_help=True, help="Rights: the owner's decision on what a source may do."
)
app.add_typer(rights_app, name="rights")

JsonFlag = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]
ProjectDir = Annotated[
    Path, typer.Argument(help="Project repo root.", exists=True, file_okay=False)
]

JSON_SCHEMAS = {
    "source": SourceConfig,
    "schema": ProjectSchema,
    "project": ProjectConfig,
}


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[bool, typer.Option("--version", help="Show the version.")] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit


@app.command()
def validate(root: ProjectDir = Path("."), as_json: JsonFlag = False) -> None:
    """Check the schema and every source file; exit 1 on any problem."""
    project = load_project(root)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "ok": project.ok,
                    "schema": project.schema.name if project.schema else None,
                    "sources": [source.id for source in project.sources],
                    "issues": [issue.model_dump() for issue in project.issues],
                },
                indent=2,
            )
        )
    else:
        for issue in project.issues:
            typer.echo(str(issue), err=True)
        if project.ok:
            name = project.schema.name if project.schema else "?"
            typer.echo(f"ok: schema {name!r}, {len(project.sources)} source(s)")
    if not project.ok:
        raise typer.Exit(1)


@app.command("json-schema")
def json_schema(
    kind: Annotated[str, typer.Argument(help="source, schema or project.")],
) -> None:
    """Print the JSON Schema for a config file, for editors and AI tools."""
    model = JSON_SCHEMAS.get(kind)
    if model is None:
        raise typer.BadParameter(f"choose one of: {', '.join(JSON_SCHEMAS)}")
    typer.echo(json.dumps(model.model_json_schema(by_alias=True), indent=2))


SourceOption = Annotated[
    list[str] | None,
    typer.Option("--source", "-s", help="Only this source (repeatable); default: all enabled."),
]


def _loaded(root: Path):
    project = load_project(root)
    if not project.ok:
        for issue in project.issues:
            typer.echo(str(issue), err=True)
        typer.echo("fix these with `gleanyard validate` first", err=True)
        raise typer.Exit(2)
    return project


@app.command()
def collect(
    root: ProjectDir = Path("."),
    source: SourceOption = None,
    from_cache: Annotated[
        bool, typer.Option("--from-cache", help="Re-extract the last cached run; no network.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Fetch and extract sources; exit 1 if any source fails."""
    project = _loaded(root)
    if source:
        known = {item.id: item for item in project.sources}
        missing = [name for name in source if name not in known]
        if missing:
            raise typer.BadParameter(f"unknown source(s): {', '.join(missing)}")
        selected = [known[name] for name in source]
    else:
        selected = [item for item in project.sources if item.enabled]
    started_at = datetime.now(UTC)
    runs = [collect_source(project, item, from_cache=from_cache) for item in selected]
    if not from_cache and runs:
        report = build_status(project, runs, load_status(project))
        write_status(project, report)
        write_job_status(project, report, runs, started_at)
    if as_json:
        typer.echo(json.dumps([run.model_dump() for run in runs], indent=2))
    else:
        for run in runs:
            if run.ok:
                typer.echo(
                    f"ok    {run.source_id}: {run.records} records, {run.rejections} rejected"
                )
            else:
                typer.echo(f"FAIL  {run.source_id}: {run.error_kind}: {run.error or 'no records'}")
        if not runs:
            typer.echo("no enabled sources")
    if any(not run.ok for run in runs):
        raise typer.Exit(1)


@app.command()
def export(root: ProjectDir = Path("."), as_json: JsonFlag = False) -> None:
    """Write the rights-filtered public export from stored records."""
    project = _loaded(root)
    path, count = export_project(project)
    metadata = write_metadata(project, count, path)
    csv_path = project.root / project.config.export_csv if project.config.export_csv else None
    if as_json:
        result = {"path": str(path), "records": count}
        if csv_path:
            result["csv"] = str(csv_path)
        if metadata:
            result["dataherb"] = str(metadata)
        typer.echo(json.dumps(result))
    else:
        typer.echo(f"wrote {count} records to {path}")
        if csv_path:
            typer.echo(f"wrote them as CSV to {csv_path}")
        if metadata:
            typer.echo(f"wrote DataHerb metadata to {metadata}")


@app.command()
def changes(
    root: ProjectDir = Path("."),
    source: SourceOption = None,
    since: Annotated[
        datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="From this day on (YYYY-MM-DD); default: 7 days."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """List records added, changed or removed by recent collects (from data/, unfiltered)."""
    project = _loaded(root)
    day = since.date() if since else datetime.now(UTC).date() - timedelta(days=7)
    found = load_changes(record_store(project), since=day, sources=set(source) if source else None)
    if as_json:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in found], indent=2))
        return
    identity = project.schema.identity if project.schema else []
    for item in found:
        values = item.record.values
        name = " / ".join(str(values[f]) for f in identity if f in values) or item.id
        fields = f" ({', '.join(item.fields)})" if item.fields else ""
        typer.echo(f"{item.at:%Y-%m-%d %H:%M} {item.change:8} {item.source_id}: {name}{fields}")
    if not found:
        typer.echo(f"no changes since {day}")


@app.command()
def status(root: ProjectDir = Path("."), as_json: JsonFlag = False) -> None:
    """Show the health report from the last `collect`; exit 1 if any source is not ok."""
    project = _loaded(root)
    report = load_status(project)
    if report is None:
        typer.echo("no status yet: run `gleanyard collect` first", err=True)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        for item in report.sources:
            detail = item.error or item.note or f"{item.records} records"
            typer.echo(f"{item.health:9} {item.id}: {detail}")
    if not report.healthy:
        raise typer.Exit(1)


@app.command()
def badge(
    root: ProjectDir = Path("."),
    out: Annotated[Path, typer.Option(help="Directory, relative to the project.")] = Path("badges"),
    as_json: JsonFlag = False,
) -> None:
    """Write README status badges (project.svg, sources/<id>.svg) from status.json."""
    project = _loaded(root)
    # Before the first collect every source shows as "not collected yet", not as an error.
    report = load_status(project) or build_status(project, [])
    written = write_badges(report, project.root / out)
    paths = [str(path.relative_to(project.root)) for path in written]
    if as_json:
        typer.echo(json.dumps({"written": paths}))
    else:
        typer.echo(f"wrote {len(paths)} badge(s) to {out}")


@app.command()
def terms(
    root: ProjectDir = Path("."),
    source: SourceOption = None,
    force: Annotated[
        bool, typer.Option("--force", help="Check now, even if checked in the last week.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Check each source's terms pages and robots.txt for changes; exit 1 if any changed."""
    project = _loaded(root)
    if source:
        known = {item.id: item for item in project.sources}
        missing = [name for name in source if name not in known]
        if missing:
            raise typer.BadParameter(f"unknown source(s): {', '.join(missing)}")
        selected = [known[name] for name in source]
    else:
        selected = [item for item in project.sources if item.enabled]
    report = load_status(project) or build_status(project, [])
    entries = {entry.id: entry for entry in report.sources}
    results = check_terms(
        project, selected, {key: entry.terms for key, entry in entries.items()}, force=force
    )
    if any(result.checked for result in results):
        for result in results:
            if result.source_id in entries:
                entries[result.source_id].terms = result.state
        write_status(project, report)
    changed = [result for result in results if result.state and result.state.changed]
    if as_json:
        typer.echo(json.dumps([result.model_dump(mode="json") for result in results], indent=2))
    else:
        for result in results:
            mark = "CHANGED" if result.state and result.state.changed else "ok"
            typer.echo(f"{mark:8} {result.source_id}: {result.note or 'unchanged'}")
            for url in result.state.changed if result.state else []:
                typer.echo(f"  {url}")
            for url, error in (result.state.errors if result.state else {}).items():
                typer.echo(f"  could not read {url}: {error}")
    if changed:
        raise typer.Exit(1)


@app.command()
def probe(
    url: Annotated[str, typer.Argument(help="Page, feed or document URL.")],
    root: Annotated[Path, typer.Option(help="Project repo, for its user agent.")] = Path("."),
    as_json: JsonFlag = False,
) -> None:
    """Find feeds, schema.org markup and sitemaps at a URL and rank how to collect from it."""
    config = load_project(root).config
    try:
        report = probe_url(url, user_agent=config.user_agent)
    except (ValueError, SourceError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return
    if not report.robots_allows:
        typer.echo("robots.txt disallows this URL; do not collect from it")
        raise typer.Exit(1)
    typer.echo(f"{report.url}: HTTP {report.status}, {report.document or '?'}")
    for option in report.options:
        typer.echo(
            f"{option.rank}. {option.delivery}/{option.mode}  {option.url}\n   {option.reason}"
        )
    for label, items in (("sitemaps", report.sitemaps), ("legal pages", report.legal_pages)):
        if items:
            typer.echo(f"{label}: {', '.join(items)}")


@app.command("try")
def try_command(
    source: Annotated[str, typer.Argument(help="Source id.")],
    root: ProjectDir = Path("."),
    as_json: JsonFlag = False,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print a PR description instead.")
    ] = False,
    save_pages: Annotated[
        Path | None,
        typer.Option("--save-pages", help="Also write the fetched pages to this directory."),
    ] = None,
) -> None:
    """Live dry run of one source: sample records, fill rates and a pass/fail verdict."""
    project = _loaded(root)
    known = {item.id: item for item in project.sources}
    if source not in known:
        raise typer.BadParameter(f"unknown source: {source}")
    report = try_source(project, known[source], save_pages=save_pages)
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    elif markdown:
        typer.echo(report.markdown())
    else:
        typer.echo(f"{report.verdict.upper()}  {report.source_id}  mode {report.mode}")
        for reason in report.reasons:
            typer.echo(f"  {reason}")
        for name, rate in report.field_fill_rates.items():
            typer.echo(f"  {name:16} {rate:6.0%}")
        for row in report.samples[:3]:
            typer.echo(f"  sample: {json.dumps(row, ensure_ascii=False)[:200]}")
        for path in report.saved_pages:
            typer.echo(f"  saved: {path}")
    if not report.passed:
        raise typer.Exit(1)


@app.command("test")
def test_command(
    root: ProjectDir = Path("."),
    source: SourceOption = None,
    update: Annotated[
        bool,
        typer.Option("--update", help="Write the snapshot from today's replay, if it passes."),
    ] = False,
    today: Annotated[
        datetime | None,
        typer.Option(
            "--today",
            formats=["%Y-%m-%d"],
            help="With --update: judge the fixture as of this date (YYYY-MM-DD), for a page "
            "saved before its events passed. Default: today.",
        ),
    ] = None,
    as_json: JsonFlag = False,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print a PR description instead.")
    ] = False,
) -> None:
    """Replay each source's fixture offline against its snapshot; exit 1 on any difference."""
    project = _loaded(root)
    selected = None
    if source:
        known = {item.id: item for item in project.sources}
        missing = [name for name in source if name not in known]
        if missing:
            raise typer.BadParameter(f"unknown source(s): {', '.join(missing)}")
        selected = [known[name] for name in source]
    if today is not None and not update:
        raise typer.BadParameter("--today only applies with --update; a snapshot keeps its date")
    day = today.date() if today is not None else None
    report = replay_project(project, selected, update=update, today=day)
    if as_json:
        typer.echo(json.dumps([item.model_dump() for item in report.results], indent=2))
    elif markdown:
        typer.echo(report.markdown())
    else:
        for item in report.results:
            typer.echo(f"{item.verdict.upper():5} {item.source_id}")
            for reason in item.reasons:
                typer.echo(f"  {reason}")
        if not report.results:
            typer.echo("no sources")
    if not report.passed:
        raise typer.Exit(1)


@app.command("map")
def map_command(
    source: Annotated[str, typer.Argument(help="Source id (a `selectors` source).")],
    root: ProjectDir = Path("."),
    as_json: JsonFlag = False,
    model: Annotated[
        str | None, typer.Option("--model", help="Model id for crawl4ai, e.g. anthropic/<model>.")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace an existing mapping.")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report without writing.")] = False,
) -> None:
    """Draft or repair a source's selector mapping with crawl4ai (needs gleanyard[map])."""
    project = load_project(root)
    # A mapping that does not exist yet is exactly what `map` is for.
    issues = [
        issue
        for issue in project.issues
        if not (issue.path == "extract.selectors" and "not found" in issue.message)
    ]
    if issues:
        for issue in issues:
            typer.echo(str(issue), err=True)
        typer.echo("fix these with `gleanyard validate` first", err=True)
        raise typer.Exit(2)
    known = {item.id: item for item in project.sources}
    if source not in known:
        raise typer.BadParameter(f"unknown source: {source}")
    try:
        report = draft_mapping(project, known[source], model=model, force=force, dry_run=dry_run)
    except (ConfigError, SourceError) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    else:
        if report.error:
            typer.echo(f"FAIL  {report.source_id}: {report.error}")
        else:
            verb = "wrote" if report.written else "would write"
            typer.echo(f"OK  {verb} {report.path}: {report.records} records on {report.page}")
        for name, rate in report.field_fill_rates.items():
            typer.echo(f"  {name:16} {rate:6.0%}")
        for note in report.notes:
            typer.echo(f"  {note}")
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def check(
    url: Annotated[str, typer.Argument(help="Candidate URL.")],
    root: ProjectDir = Path("."),
    as_json: JsonFlag = False,
) -> None:
    """Say whether a URL's host is already a source, a candidate or excluded (exit 1 if so)."""
    try:
        result = load_project(root).host_status(url)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    host, status, sources = result["host"], result["status"], result["sources"]
    if as_json:
        typer.echo(json.dumps(result))
    else:
        typer.echo(f"{host}: {status}" + (f" ({', '.join(sources)})" if sources else ""))
    if status != "new":
        raise typer.Exit(1)


@app.command()
def scaffold(
    url: Annotated[str, typer.Argument(help="Page, feed or document URL.")],
    source_id: Annotated[str, typer.Option("--id", help="New source id.")],
    root: ProjectDir = Path("."),
    option: Annotated[int, typer.Option(help="Which `probe` option to use.")] = 1,
    write: Annotated[bool, typer.Option("--write", help="Save to sources/<id>.yaml.")] = False,
    as_json: JsonFlag = False,
) -> None:
    """Draft a source file from a probe; feeds are mapped to the schema through its aliases."""
    project = _loaded(root)
    assert project.schema is not None
    try:
        draft = scaffold_url(
            url,
            source_id,
            project.schema,
            user_agent=project.config.user_agent,
            rank=option,
        )
    except SourceError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    path = root / project.config.sources / f"{source_id}.yaml"
    if write:
        if path.exists():
            typer.echo(f"{path} exists; not overwriting", err=True)
            raise typer.Exit(1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(draft.yaml_text, encoding="utf-8")
    if as_json:
        typer.echo(draft.model_dump_json(indent=2))
        return
    typer.echo(draft.yaml_text)
    for note in draft.notes:
        typer.echo(f"note: {note}", err=True)
    if write:
        typer.echo(f"wrote {path}", err=True)


@app.command()
def init(
    directory: Annotated[Path, typer.Argument(help="New project directory (empty or missing).")],
    schema: Annotated[str, typer.Option(help="Name of the project schema.")] = "record",
    name: Annotated[
        str | None, typer.Option(help="Project name; default: the directory name.")
    ] = None,
) -> None:
    """Create a collection project: schema, workflows and CLAUDE.md for an AI assistant."""
    project = name or directory.resolve().name.lower().replace("_", "-")
    try:
        written = init_project(directory, project, schema)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"created {project!r} in {directory} ({len(written)} files)")
    typer.echo("next: edit schema.yaml and research.md, then `gleanyard validate`")
    typer.echo("on GitHub, add the topic `gleanyard` to the repo so a status hub finds it")


@app.command()
def skill(
    root: ProjectDir = Path("."),
    check_only: Annotated[
        bool, typer.Option("--check", help="Only report; exit 1 if missing or outdated.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Write the assistant's procedures (.claude/skills/gleanyard/SKILL.md) for this version."""
    before = skill_state(root)
    if not check_only and before.state != "current":
        write_skill(root)
    result = {**before.model_dump(), "written": not check_only and before.state != "current"}
    if as_json:
        typer.echo(json.dumps(result))
    elif result["written"]:
        typer.echo(f"wrote {before.path} (was {before.state}) for gleanyard {__version__}")
    else:
        typer.echo(f"{before.path}: {before.state} for gleanyard {__version__}")
    if check_only and before.state != "current":
        raise typer.Exit(1)


@app.command("mcp")
def mcp_command(root: ProjectDir = Path(".")) -> None:
    """Serve the project's commands as MCP tools over stdio (needs gleanyard[mcp])."""
    try:
        from gleanyard.mcp_server import build_server
    except ImportError as error:
        typer.echo("the MCP server needs the extra: pip install 'gleanyard[mcp]'", err=True)
        raise typer.Exit(2) from error
    build_server(root).run("stdio")


RegistryOption = Annotated[
    str | None,
    typer.Option(
        "--registry",
        help=f"Registry URL or folder; default: $GLEANYARD_REGISTRY or {DEFAULT_REGISTRY}",
    ),
]


def _registry(location: str | None) -> Registry:
    return Registry(location or os.environ.get("GLEANYARD_REGISTRY") or DEFAULT_REGISTRY)


@rights_app.command("review")
def rights_review(
    root: ProjectDir = Path("."),
    sources: Annotated[
        list[str] | None,
        typer.Option("--source", "-s", help="Review this source even if decided (repeatable)."),
    ] = None,
) -> None:
    """Ask the owner, source by source, for the rights of pending sources and write them."""
    project = load_project(root)
    if project.schema is None:
        for issue in project.issues:
            typer.echo(str(issue), err=True)
        raise typer.Exit(1)
    groups = pending_groups(project, sources)
    if not groups:
        typer.echo("no sources wait for a rights review")
        return
    today = datetime.now(UTC).date()
    tiers = [tier.value for tier in RightsTier]
    action_names = list(AllowedActions.model_fields)
    written = 0
    for number, group in enumerate(groups, 1):
        ids = [source.id for source in group.sources]
        typer.echo("")
        typer.secho(f"[{number}/{len(groups)}] {', '.join(ids)}", bold=True)
        for source in group.sources:
            typer.echo(f"  {source.id}: {source.url}")
        if group.evidence_path is None:
            typer.echo(f"  no evidence/{group.folder}/<date>-terms.yaml: read the terms first")
        else:
            typer.echo(f"  evidence: {group.evidence_path}")
            for url, quote in group.quotes:
                typer.echo(f"  - {url}")
                if quote:
                    typer.echo(f'    "{_clip(quote)}"')
            if group.reading:
                typer.echo(f"  reading: {_clip(group.reading, 600)}")
        current = group.sources[0].rights
        default_tier = group.suggested_tier or current.effective_tier
        tier = RightsTier(
            typer.prompt(
                f"Tier ({', '.join(tiers)})",
                default=default_tier.value,
                type=click.Choice(tiers),
                show_choices=False,
            )
        )
        rights = suggest_rights(tier, project.schema, current)
        on = [name for name in action_names if getattr(rights.allowed_actions, name)]
        typer.echo(f"  allowed actions: {', '.join(on) or 'none'}")
        typer.echo(f"  public fields:   {', '.join(rights.public_fields) or 'none'}")
        if not typer.confirm("Keep these?", default=True):
            on = _names(
                typer.prompt("Allowed actions (comma-separated)", default=", ".join(on)),
                action_names,
                "action",
            )
            fields = _names(
                typer.prompt(
                    "Public fields (comma-separated)",
                    default=", ".join(rights.public_fields),
                ),
                list(project.schema.fields),
                "field",
            )
            rights = rights.model_copy(
                update={
                    "allowed_actions": AllowedActions(
                        **{name: name in on for name in action_names}
                    ),
                    "public_fields": fields,
                }
            )
        if tier in OPEN_TIERS and tier != RightsTier.LICENSED:
            rights = rights.model_copy(
                update={
                    "license_id": typer.prompt("Licence id", default=rights.license_id or "")
                    or None,
                    "license_url": typer.prompt("Licence URL", default=rights.license_url or "")
                    or None,
                }
            )
        if rights.attribution_required:
            text = typer.prompt("Attribution text", default=rights.attribution_text or "")
            rights = rights.model_copy(update={"attribution_text": text or None})
        status = typer.prompt(
            "Decision (approve, reject, skip)",
            default="approve",
            type=click.Choice(["approve", "reject", "skip"]),
            show_choices=False,
        )
        if status == "skip":
            continue
        enabled = False
        if status == "approve" and tier != RightsTier.BLOCKED:
            label = "this source" if len(ids) == 1 else f"these {len(ids)} sources"
            enabled = typer.confirm(f"Enable {label}?", default=True)
        notes = typer.prompt("Notes", default=current.notes or "", show_default=False)
        rights = decide(rights, "approved" if status == "approve" else "rejected", today, notes)
        for source in group.sources:
            path = root / "sources" / f"{source.id}.yaml"
            write_decision(path, rights, enabled)
            typer.echo(f"wrote {path.relative_to(root)}")
            written += 1
    typer.echo("")
    checked = load_project(root)
    for issue in checked.issues:
        typer.echo(str(issue), err=True)
    if not checked.ok:
        raise typer.Exit(1)
    typer.echo(f"ok: {written} source file(s) updated; `gleanyard validate` is clean")


def _clip(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _names(answer: str, allowed: list[str], kind: str) -> list[str]:
    names = [name.strip() for name in answer.split(",") if name.strip()]
    unknown = [name for name in names if name not in allowed]
    if unknown:
        typer.echo(f"unknown {kind}(s): {', '.join(unknown)}; choose from {', '.join(allowed)}")
        raise typer.Exit(2)
    return names


@app.command("search")
def search_command(
    query: Annotated[str, typer.Argument(help="Words to look for; empty lists every recipe.")] = "",
    schema: Annotated[str | None, typer.Option(help="Only recipes for this schema.")] = None,
    registry: RegistryOption = None,
    as_json: JsonFlag = False,
) -> None:
    """Find recipes in the source registry, with their health across projects."""
    try:
        found = search_index(_registry(registry).index(), query, schema)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    if as_json:
        typer.echo(json.dumps([entry.model_dump(mode="json", by_alias=True) for entry in found]))
        return
    for entry in found:
        health = entry.health
        users = f"{health.level} in {health.users}" if health.users else "unknown"
        typer.echo(
            f"{entry.id:32} {users:14} {entry.schema_name}  {entry.delivery}/{entry.mode}  "
            f"{entry.name}"
        )
    if not found:
        typer.echo("no recipes found")


@app.command("add")
def add_command(
    recipe: Annotated[str, typer.Argument(help="Recipe id, from `gleanyard search`.")],
    root: ProjectDir = Path("."),
    source_id: Annotated[
        str | None, typer.Option("--id", help="Source id in this project; default: the recipe id.")
    ] = None,
    registry: RegistryOption = None,
    force: Annotated[
        bool,
        typer.Option("--force", help="Keep the files even if a check fails; allow a known host."),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Install a registry recipe as a disabled source with pending rights; exit 1 if it fails."""
    chosen = _registry(registry)
    try:
        entry, files = chosen.recipe(recipe)
    except RegistryError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    result = add_recipe(
        root,
        entry,
        files,
        source_id=source_id,
        registry=None if chosen.local else chosen.location,
        force=force,
    )
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        for path in result.written:
            typer.echo(f"wrote {path}")
        for note in result.notes:
            typer.echo(f"note: {note}")
        for problem in result.problems:
            typer.echo(f"problem: {problem}", err=True)
        if result.ok:
            sid = result.source_id
            typer.echo(
                f"added {sid!r} (disabled, rights pending). Next: read evidence/{sid}/, set "
                f"its rights, run `gleanyard try {sid}`, then set enabled: true."
            )
        elif result.written:
            typer.echo("kept the files (--force); fix the problems above by hand")
        else:
            typer.echo("nothing was written")
    if not result.ok:
        raise typer.Exit(1)


def _registry_check_or_exit(root: Path, as_json: bool):
    result = check_registry(root)
    if as_json:
        typer.echo(json.dumps(result.summary(), indent=2))
    else:
        for problem in result.problems:
            typer.echo(f"problem: {problem}")
        for recipe in result.recipes:
            typer.echo(f"{'ok  ' if not recipe.problems else 'FAIL'}  {recipe.id}")
            for problem in recipe.problems:
                typer.echo(f"      {problem}")
        if not result.recipes and not result.problems:
            typer.echo("no recipes yet")
    return result


@registry_app.command("check")
def registry_check(root: ProjectDir = Path("."), as_json: JsonFlag = False) -> None:
    """Validate every recipe and replay its fixture, offline; exit 1 on any problem."""
    if not _registry_check_or_exit(root, as_json).ok:
        raise typer.Exit(1)


@registry_app.command("build")
def registry_build(
    root: ProjectDir = Path("."),
    out: Annotated[Path, typer.Option(help="Output directory for the static site.")] = Path("site"),
    health: Annotated[
        bool,
        typer.Option(
            "--health/--no-health",
            help="Read the status.json of projects that use the recipes (no scraping).",
        ),
    ] = True,
) -> None:
    """Check the registry, then write its site: index.json, the recipe files, index.html."""
    check = _registry_check_or_exit(root, as_json=False)
    if not check.ok:
        typer.echo("not building: fix the problems above", err=True)
        raise typer.Exit(1)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    index, problems = build_index(
        check, health=health, search=lambda topic: search_topic(topic, token)
    )
    write_site(root, out, check, index)
    for problem in problems:
        typer.echo(f"note: {problem}")
    typer.echo(
        f"wrote {out / 'index.json'}: {len(index.recipes)} recipe(s), "
        f"health from {index.reports_read} report(s)"
    )


@registry_app.command("pack")
def registry_pack(
    source: Annotated[str, typer.Argument(help="Source id in the project.")],
    project: Annotated[
        Path, typer.Option(help="The project repo the source comes from.", exists=True)
    ],
    maintainer: Annotated[
        list[str], typer.Option("--maintainer", help="GitHub login to ask (repeatable).")
    ],
    description: Annotated[str, typer.Option(help="What the source publishes.")],
    fixture_basis: Annotated[
        str, typer.Option("--fixture-basis", help="Why the fixture page may be public.")
    ],
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Tag (repeatable).")] = None,
    root: ProjectDir = Path("."),
    as_json: JsonFlag = False,
) -> None:
    """Copy a working source from a project into this registry as a recipe."""
    result = pack_source(
        project,
        source,
        root,
        maintainers=maintainer,
        description=description,
        fixture_basis=fixture_basis,
        tags=tag,
    )
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        for path in result.written:
            typer.echo(f"wrote {path}")
        for problem in result.problems:
            typer.echo(f"problem: {problem}", err=True)
        if result.ok:
            typer.echo(f"packed {source!r}; run `gleanyard registry check`, then open a PR")
    if not result.ok:
        raise typer.Exit(1)


@registry_app.command("init")
def registry_init(
    directory: Annotated[Path, typer.Argument(help="New registry directory (empty or missing).")],
    url: Annotated[
        str | None, typer.Option(help="Where its Pages site will be, e.g. https://o.github.io/r/")
    ] = None,
    name: Annotated[
        str | None, typer.Option(help="Registry name; default: the directory name.")
    ] = None,
) -> None:
    """Create a source registry repo: registry.yaml, check and publish workflows, a README."""
    registry = name or directory.resolve().name.lower().replace("_", "-")
    try:
        written = init_registry(directory, registry, url)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"created registry {registry!r} in {directory} ({len(written)} files)")
    typer.echo("next: add a schema to schemas/, then `gleanyard registry pack` a source")


def _hub_clients() -> tuple[HttpGitHub | None, HttpGitHub | None]:
    """The client that reads the projects (GH_TOKEN, e.g. a read-only HUB_TOKEN) and the one
    that writes issues in the hub repo (the workflow's own GITHUB_TOKEN when there is one)."""
    read_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    write_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return (
        HttpGitHub(read_token) if read_token else None,
        HttpGitHub(write_token) if write_token else None,
    )


@hub_app.command("run")
def hub_run(
    config_path: Annotated[Path, typer.Option("--config", help="hub.yaml")] = Path("hub.yaml"),
    hub_repo: Annotated[
        str | None,
        typer.Option(
            "--hub-repo", help="owner/name for alert issues; default: $GITHUB_REPOSITORY."
        ),
    ] = None,
    state_path: Annotated[Path, typer.Option("--state", help="Snapshot for the dashboard.")] = Path(
        "data/state.json"
    ),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Do not touch issues.")] = False,
    as_json: JsonFlag = False,
) -> None:
    """Read every watched repo, write the snapshot, and open or close alert issues."""
    try:
        config = load_hub_config(config_path)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(2) from error
    reader, writer = _hub_clients()
    target = hub_repo or os.environ.get("GITHUB_REPOSITORY")
    if not (target or dry_run):
        typer.echo("set --hub-repo (or GITHUB_REPOSITORY), or use --dry-run", err=True)
        raise typer.Exit(2)
    if (reader is None and config.needs_github) or (writer is None and not dry_run):
        typer.echo(
            "set GH_TOKEN: reading repos and opening issues need the GitHub API "
            "(a hub that only lists status.json URLs can --dry-run without one)",
            err=True,
        )
        raise typer.Exit(2)
    state = collect_state(config, reader)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    alerts = compute_alerts(state, config)
    if writer is None:  # a token-free dry run: nothing to sync
        result, digest = SyncResult(opened=[alert.key for alert in alerts]), None
    else:
        result = sync_issues(writer, target or "", alerts, dry_run=dry_run)
        digest = weekly_digest(writer, target or "", state, config, dry_run=dry_run)
    summary = {
        "repos": [{"repo": r.repo, "level": r.level} for r in state.repos],
        "alerts": [alert.key for alert in alerts],
        "opened": result.opened,
        "closed": result.closed,
        "digest": digest,
    }
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
    else:
        for repo in state.repos:
            typer.echo(f"{repo.level:6} {repo.repo}")
        typer.echo(
            f"alerts: {len(alerts)} active, {len(result.opened)} opened, "
            f"{len(result.closed)} closed"
        )


@hub_app.command("build")
def hub_build(
    state_path: Annotated[Path, typer.Option("--state", help="Snapshot from `hub run`.")] = Path(
        "data/state.json"
    ),
    out: Annotated[Path, typer.Option(help="Output directory for the static site.")] = Path("site"),
    title: Annotated[str, typer.Option(help="Page title.")] = "Collection status",
) -> None:
    """Render the snapshot as a dashboard page (index.html) and a Markdown summary (summary.md)."""
    try:
        state = HubState.model_validate_json(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        typer.echo(f"cannot read {state_path}: {error}; run `gleanyard hub run` first", err=True)
        raise typer.Exit(2) from error
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_dashboard(state, title), encoding="utf-8")
    (out / "state.json").write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_summary(state, title), encoding="utf-8")
    viewers = write_viewers(state, out, title)
    typer.echo(f"wrote {out / 'index.html'} and {out / 'summary.md'}")
    if viewers:
        typer.echo(f"wrote {len(viewers)} record viewer(s) to {out / VIEWER_DIR}")


@hub_app.command("check")
def hub_check(
    config_path: Annotated[Path, typer.Option("--config", help="hub.yaml")] = Path("hub.yaml"),
    hub_repo: Annotated[
        str | None,
        typer.Option("--hub-repo", help="owner/name of the hub; default: $GITHUB_REPOSITORY."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Check the hub's setup: config, token, every project's status.json, Pages. Exit 1 if not."""
    reader, writer = _hub_clients()
    target = hub_repo or os.environ.get("GITHUB_REPOSITORY")
    lines = check_hub(config_path, reader, target, hub_github=writer)
    if as_json:
        typer.echo(json.dumps([line.model_dump() for line in lines], indent=2))
    else:
        for line in lines:
            typer.echo(f"{'ok  ' if line.ok else 'FAIL'}  {line.what}")
            if line.fix:
                typer.echo(f"      fix: {line.fix}")
    if not all(line.ok for line in lines):
        raise typer.Exit(1)


@hub_app.command("init")
def hub_init(
    directory: Annotated[Path, typer.Argument(help="New hub directory (empty or missing).")],
    repo: Annotated[
        list[str] | None,
        typer.Option("--repo", help="Project as owner/name or a status.json URL (repeatable)."),
    ] = None,
    owner: Annotated[
        str | None,
        typer.Option(help="Also watch every repo of this user or org with the topic `gleanyard`."),
    ] = None,
    pages: Annotated[
        bool,
        typer.Option(
            "--pages/--no-pages",
            help="Publish the dashboard on GitHub Pages (public on most plans).",
        ),
    ] = False,
    name: Annotated[str | None, typer.Option(help="Hub name; default: the directory name.")] = None,
) -> None:
    """Create the status hub repo: hub.yaml, a workflow, and a dashboard."""
    hub = name or directory.resolve().name.lower().replace("_", "-")
    if not repo and not owner:
        typer.echo("give --owner (to find repos by topic) or at least one --repo", err=True)
        raise typer.Exit(2)
    try:
        written = init_hub(directory, hub, repo or [], owner=owner, pages=pages)
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"created hub {hub!r} in {directory} ({len(written)} files)")
    typer.echo("next: push it to GitHub, follow its README, then run `gleanyard hub check`")


@repair_app.command("plan")
def repair_plan(root: ProjectDir = Path("."), as_json: JsonFlag = False) -> None:
    """List the failing or degraded sources an automated repair may attempt, and the rest."""
    plan = plan_repair(_loaded(root))
    if as_json:
        typer.echo(plan.model_dump_json(indent=2))
        return
    for target in plan.targets:
        typer.echo(f"repair  {target.id}: {', '.join(target.reasons)}  {target.detail or ''}")
    for item in plan.skipped:
        typer.echo(f"skip    {item.id}: {item.reason}")
    if not plan.targets and not plan.skipped:
        typer.echo("nothing to repair")


@repair_app.command("check")
def repair_check(
    root: ProjectDir = Path("."),
    base: Annotated[str, typer.Option(help="Git ref the changes are compared with.")] = "HEAD",
    as_json: JsonFlag = False,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print a PR description instead.")
    ] = False,
) -> None:
    """Judge uncommitted changes as a repair. Exit 0 ready, 1 draft only, 2 not proposable."""
    try:
        result = check_repair(root, base)
    except RuntimeError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    if as_json:
        typer.echo(json.dumps(result.summary(), indent=2))
    elif markdown:
        typer.echo(result.markdown())
    else:
        typer.echo("ready" if result.ready else "draft" if result.proposable else "not proposable")
        for problem in result.problems:
            typer.echo(f"  problem: {problem}")
        for item in result.sources:
            typer.echo(f"  {'PASS' if item.passed else 'FAIL'}  {item.id}")
            for problem in item.problems:
                typer.echo(f"        {problem}")
        for note in result.notes:
            typer.echo(f"  note: {note}")
    if not result.ready:
        raise typer.Exit(1 if result.proposable else 2)


@research_app.command("check")
def research_check(
    root: ProjectDir = Path("."),
    base: Annotated[str, typer.Option(help="Git ref the changes are compared with.")] = "HEAD",
    max_candidates: Annotated[
        int, typer.Option("--max", help="Most candidates one run may propose.")
    ] = MAX_CANDIDATES,
    as_json: JsonFlag = False,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Print a PR description instead.")
    ] = False,
) -> None:
    """Judge uncommitted changes as a research run: new candidates only. Exit 0 if proposable."""
    try:
        result = check_research(root, base, max_candidates)
    except RuntimeError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(2) from error
    if as_json:
        typer.echo(json.dumps(result.summary(), indent=2))
    elif markdown:
        typer.echo(result.markdown())
    else:
        typer.echo("ready" if result.proposable else "not proposable")
        for problem in result.problems:
            typer.echo(f"  problem: {problem}")
        for item in result.candidates:
            typer.echo(f"  {'ok' if not item.problems else 'FAIL':4}  {item.path}")
            for problem in item.problems:
                typer.echo(f"        {problem}")
    if not result.proposable:
        raise typer.Exit(1)
