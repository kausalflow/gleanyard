"""The ``gleanyard`` command line. Every command supports ``--json``."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import typer

from gleanyard import __version__
from gleanyard.config import ProjectConfig, SourceConfig
from gleanyard.errors import ConfigError, SourceError
from gleanyard.hub.alerts import compute_alerts, sync_issues, weekly_digest
from gleanyard.hub.config import load_hub_config
from gleanyard.hub.dashboard import render_dashboard
from gleanyard.hub.github import HttpGitHub
from gleanyard.hub.state import HubState, collect_state
from gleanyard.init import init_hub, init_project
from gleanyard.probe import probe as probe_url
from gleanyard.project import load_project
from gleanyard.run import collect_source, export_project
from gleanyard.scaffold import scaffold as scaffold_url
from gleanyard.schema import ProjectSchema
from gleanyard.status import build_status, load_status, write_status
from gleanyard.tryout import try_source

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
hub_app = typer.Typer(no_args_is_help=True, help="The status hub: watch many collection repos.")
app.add_typer(hub_app, name="hub")

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
    runs = [collect_source(project, item, from_cache=from_cache) for item in selected]
    if not from_cache and runs:
        write_status(project, build_status(project, runs, load_status(project)))
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
    if as_json:
        typer.echo(json.dumps({"path": str(path), "records": count}))
    else:
        typer.echo(f"wrote {count} records to {path}")


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
) -> None:
    """Live dry run of one source: sample records, fill rates and a pass/fail verdict."""
    project = _loaded(root)
    known = {item.id: item for item in project.sources}
    if source not in known:
        raise typer.BadParameter(f"unknown source: {source}")
    report = try_source(project, known[source])
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
    if not report.passed:
        raise typer.Exit(1)


@app.command()
def check(
    url: Annotated[str, typer.Argument(help="Candidate URL.")],
    root: ProjectDir = Path("."),
    as_json: JsonFlag = False,
) -> None:
    """Say whether a URL's host is already a source, a candidate or excluded (exit 1 if so)."""
    project = load_project(root)
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        raise typer.BadParameter("not a URL")
    sources = [
        item.id
        for item in project.sources
        if host in {urlsplit(str(u)).hostname for u in (item.url, item.homepage) if u}
    ]
    candidate = (root / "candidates" / f"{host}.yaml").is_file()
    excluded = host in {name.lower() for name in project.config.excluded_hosts}
    status = (
        "excluded" if excluded else "source" if sources else "candidate" if candidate else "new"
    )
    if as_json:
        typer.echo(
            json.dumps({"host": host, "status": status, "sources": sources, "candidate": candidate})
        )
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
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    target = hub_repo or os.environ.get("GITHUB_REPOSITORY")
    if not token or not (target or dry_run):
        typer.echo("set GH_TOKEN and --hub-repo (or GITHUB_REPOSITORY)", err=True)
        raise typer.Exit(2)
    github = HttpGitHub(token or "")
    state = collect_state(config, github)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    alerts = compute_alerts(state, config)
    result = sync_issues(github, target or "", alerts, dry_run=dry_run)
    digest = weekly_digest(github, target or "", state, config, dry_run=dry_run)
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
    """Render the snapshot as a static dashboard page for GitHub Pages."""
    try:
        state = HubState.model_validate_json(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        typer.echo(f"cannot read {state_path}: {error}; run `gleanyard hub run` first", err=True)
        raise typer.Exit(2) from error
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_dashboard(state, title), encoding="utf-8")
    (out / "state.json").write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out / 'index.html'}")


@hub_app.command("init")
def hub_init(
    directory: Annotated[Path, typer.Argument(help="New hub directory (empty or missing).")],
    repo: Annotated[
        list[str] | None, typer.Option("--repo", help="Collection repo owner/name (repeatable).")
    ] = None,
    name: Annotated[str | None, typer.Option(help="Hub name; default: the directory name.")] = None,
) -> None:
    """Create the status hub repo: hub.yaml, a workflow, and a Pages dashboard."""
    hub = name or directory.resolve().name.lower().replace("_", "-")
    try:
        written = init_hub(directory, hub, repo or [])
    except ConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from error
    typer.echo(f"created hub {hub!r} in {directory} ({len(written)} files)")
