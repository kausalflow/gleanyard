"""The ``gleanyard`` command line. Every command supports ``--json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import typer

from gleanyard import __version__
from gleanyard.config import ProjectConfig, SourceConfig
from gleanyard.errors import SourceError
from gleanyard.probe import probe as probe_url
from gleanyard.project import load_project
from gleanyard.run import collect_source, export_project
from gleanyard.scaffold import scaffold as scaffold_url
from gleanyard.schema import ProjectSchema
from gleanyard.status import build_status, load_status, write_status
from gleanyard.tryout import try_source

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

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
