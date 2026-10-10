"""The ``gleanyard`` command line. Every command supports ``--json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from gleanyard import __version__
from gleanyard.config import ProjectConfig, SourceConfig
from gleanyard.project import load_project
from gleanyard.schema import ProjectSchema

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
