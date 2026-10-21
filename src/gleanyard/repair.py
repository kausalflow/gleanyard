"""``gleanyard repair``: the deterministic half of an automated repair.

The template's opt-in ``repair`` workflow lets a coding agent edit a broken source, but the
agent decides nothing. ``plan`` reads ``status.json`` and names the sources an automated fix
may attempt; ``check`` compares the agent's edits with the base commit and decides whether
they can become a pull request:

- only source files, mappings, fixtures and evidence may change; generated files (records,
  exports, status) are left out of the proposal, and anything else stops it;
- a source keeps its id, url, delivery, rights, fetch limits and ``enabled`` flag, and is not
  switched to ``llm`` mode;
- every changed source must pass ``try``, and its record count and fill rates must not fall
  below the last good run by the same rules that mark a run ``degraded``.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, Field

from gleanyard.config import SelectorsExtract
from gleanyard.project import Project, load_project
from gleanyard.status import (
    DROP_RATIO,
    FILL_DROP_RATIO,
    FILL_MIN_BASELINE,
    MIN_RECORDS,
    SourceStatus,
    _baseline,
    _baseline_fill_rates,
    load_status,
)
from gleanyard.tryout import TryReport, try_source

# A failure an edit to the source can fix. The others are left to the owner.
FIXABLE = frozenset({"structure_changed", "empty", "pagination_changed", "filter_ignored"})
NOT_AUTOMATED = {
    "blocked": "the host refused access; the owner reviews robots.txt and the terms",
    "rights": "the source's rights do not allow it; rights are the owner's decision",
    "transport": "a network or server error; it usually clears on the next run",
    "bounds_exceeded": "a limit was reached; raising limits is the owner's decision",
    "model": "the model provider failed; check the key and the provider",
    "no_cache": "nothing was cached to compare with",
}
# Keys of a source file an automated repair must not change.
PROTECTED = (
    "id",
    "schema",
    "enabled",
    "delivery",
    "url",
    "homepage",
    "fetch",
    "coverage",
    "rights",
    "retention",
)


class Target(BaseModel):
    id: str
    health: str
    reasons: list[str] = Field(description="error_kind of a failing source, or warning kinds.")
    detail: str | None = None


class Skipped(BaseModel):
    id: str
    reason: str


class RepairPlan(BaseModel):
    targets: list[Target] = Field(default_factory=list)
    skipped: list[Skipped] = Field(default_factory=list)


def plan_repair(project: Project) -> RepairPlan:
    """Sources an automated repair may attempt, and why the others are left alone."""
    plan = RepairPlan()
    report = load_status(project)
    if report is None:
        return plan
    enabled = {source.id for source in project.sources if source.enabled}
    for entry in report.sources:
        if entry.health not in {"failing", "degraded"} or entry.id not in enabled:
            continue
        if entry.health == "degraded":
            kinds = sorted({check.kind for check in entry.warnings}) or ["degraded"]
            plan.targets.append(
                Target(id=entry.id, health=entry.health, reasons=kinds, detail=entry.note)
            )
        elif entry.error_kind in FIXABLE:
            plan.targets.append(
                Target(
                    id=entry.id,
                    health=entry.health,
                    reasons=[entry.error_kind],
                    detail=entry.error,
                )
            )
        else:
            kind = entry.error_kind or "unknown"
            reason = NOT_AUTOMATED.get(kind, "not a failure an edit to the source can fix")
            plan.skipped.append(Skipped(id=entry.id, reason=f"{kind}: {reason}"))
    return plan


class Change(BaseModel):
    path: str
    status: str = Field(description="A added, M modified, D deleted.")


class SourceCheck(BaseModel):
    id: str
    passed: bool
    problems: list[str] = Field(default_factory=list)
    report: TryReport


class RepairCheck(BaseModel):
    base: str
    files: list[str] = Field(default_factory=list, description="The files to propose.")
    ignored: list[str] = Field(default_factory=list, description="Generated files left out.")
    problems: list[str] = Field(
        default_factory=list, description="Reasons the change cannot be proposed at all."
    )
    notes: list[str] = Field(default_factory=list)
    sources: list[SourceCheck] = Field(default_factory=list)

    @property
    def proposable(self) -> bool:
        """The change may become a pull request (a draft unless it is also ``ready``)."""
        return bool(self.files) and not self.problems

    @property
    def ready(self) -> bool:
        """Every changed source passes ``try`` and matches its last good run."""
        return self.proposable and bool(self.sources) and all(s.passed for s in self.sources)

    def summary(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json"),
            "proposable": self.proposable,
            "ready": self.ready,
            "markdown": self.markdown(),
        }

    def markdown(self) -> str:
        verdict = (
            "**ready for review**"
            if self.ready
            else "**a draft**: some checks fail"
            if self.proposable
            else "**not proposable**"
        )
        lines = [f"## Repair check: {verdict}", ""]
        lines += [f"- Problem: {problem}" for problem in self.problems]
        for item in self.sources:
            mark = "PASS" if item.passed else "FAIL"
            lines.append(f"- `{item.id}`: {mark}")
            lines += [f"  - {problem}" for problem in item.problems]
        lines += [f"- {note}" for note in self.notes]
        if self.files:
            lines += ["", "Files: " + ", ".join(f"`{name}`" for name in self.files)]
        if self.ignored:
            lines += ["", "Left out (generated): " + ", ".join(f"`{n}`" for n in self.ignored)]
        for item in self.sources:
            lines += ["", item.report.markdown().rstrip()]
        return "\n".join(lines) + "\n"


def check_repair(
    root: Path,
    base: str = "HEAD",
    *,
    client: httpx.Client | None = None,
    today: date | None = None,
) -> RepairCheck:
    """Judge the working tree's changes against ``base`` as an automated repair."""
    project = load_project(root)
    result = RepairCheck(base=base)
    changes = changed_files(project.root, base)
    if not changes:
        result.problems.append("nothing changed")
        return result
    source_dir = PurePosixPath(project.config.sources)
    generated = _generated_paths(project)
    changed_sources: list[str] = []
    changed_mappings: set[str] = set()
    for change in changes:
        path = PurePosixPath(change.path)
        if any(path == prefix or prefix in path.parents for prefix in generated):
            result.ignored.append(change.path)
            continue
        if change.status == "D":
            result.problems.append(f"{change.path}: deleting files is the owner's decision")
            continue
        if source_dir in path.parents and path.suffix == ".yaml":
            if change.status == "A":
                result.problems.append(
                    f"{change.path}: a new source needs setup and a rights review, not a repair"
                )
                continue
            changed_sources.append(change.path)
        elif path.parts[0] == "mappings":
            changed_mappings.add(change.path)
        elif path.parts[0] != "evidence" and path.parts[:2] != ("tests", "fixtures"):
            result.problems.append(
                f"{change.path}: a repair may change sources, mappings, fixtures and evidence only"
            )
            continue
        result.files.append(change.path)
    for path in changed_sources:
        result.problems += _protected_changes(project.root, base, path, result.notes)
    if not project.ok:
        result.problems += [f"validate: {issue}" for issue in project.issues]
    if result.problems:
        return result
    by_id = {source.id: source for source in project.sources}
    affected = [by_id[i] for i in _source_ids(project.root, changed_sources) if i in by_id]
    for source in project.sources:
        if (
            isinstance(source.extract, SelectorsExtract)
            and str(PurePosixPath(source.extract.selectors)) in changed_mappings
            and source not in affected
        ):
            affected.append(source)
    if not affected:
        result.notes.append("no source file or mapping changed, so there is nothing to try")
    status = load_status(project)
    before = {entry.id: entry for entry in status.sources} if status else {}
    for source in affected:
        report = try_source(project, source, client=client, today=today)
        problems = [] if report.passed else [f"try: {reason}" for reason in report.reasons]
        problems += compare_with_baseline(report, before.get(source.id))
        result.sources.append(
            SourceCheck(id=source.id, passed=not problems, problems=problems, report=report)
        )
    return result


def compare_with_baseline(report: TryReport, entry: SourceStatus | None) -> list[str]:
    """The ``degraded`` rules from ``status``, applied to a ``try`` against the last good run."""
    if entry is None or not report.passed or report.records < MIN_RECORDS:
        return []
    problems = []
    count = _baseline(entry)
    if count and count >= MIN_RECORDS and report.records < count * DROP_RATIO:
        problems.append(f"{report.records} records, the last good run had {count}")
    for name, rate in _baseline_fill_rates(entry).items():
        now = report.field_fill_rates.get(name, 0.0)
        if rate >= FILL_MIN_BASELINE and now <= rate * FILL_DROP_RATIO:
            problems.append(f"field {name} fills {now:.0%}, the last good run {rate:.0%}")
    return problems


def changed_files(root: Path, base: str) -> list[Change]:
    """Tracked changes against ``base`` plus untracked files, as repo-relative paths."""
    diff = _git(root, "diff", "--name-status", "--no-renames", base, "--")
    changes = []
    for line in diff.splitlines():
        status, _, path = line.partition("\t")
        changes.append(Change(path=path, status=status[:1]))
    for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
        changes.append(Change(path=path, status="A"))
    return sorted(changes, key=lambda change: change.path)


def _protected_changes(root: Path, base: str, path: str, notes: list[str]) -> list[str]:
    try:
        old = yaml.safe_load(_git(root, "show", f"{base}:{path}")) or {}
        new = yaml.safe_load((root / path).read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as error:
        return [f"{path}: cannot compare with {base}: {error}"]
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []  # `validate` reports a malformed file
    problems = [
        f"{path}: `{key}` changed; that is the owner's decision"
        for key in PROTECTED
        if old.get(key) != new.get(key)
    ]
    old_mode = (old.get("extract") or {}).get("mode")
    new_mode = (new.get("extract") or {}).get("mode")
    if old_mode != new_mode:
        if new_mode == "llm":
            problems.append(
                f"{path}: switching to `llm` mode costs money every run; that is the owner's call"
            )
        else:
            notes.append(f"`{path}` switches the pinned mode from `{old_mode}` to `{new_mode}`")
    return problems


def _generated_paths(project: Project) -> list[PurePosixPath]:
    config = project.config
    paths = [config.storage.root, config.export, config.status]
    if config.dataherb is not None:
        paths += [config.dataherb.metadata, config.dataherb.status_dir]
    return [PurePosixPath(path) for path in paths]


def _source_ids(root: Path, paths: list[str]) -> list[str]:
    ids = []
    for path in paths:
        data = yaml.safe_load((root / path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.append(data["id"])
    return ids


def _git(root: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RuntimeError("git is not installed") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"git {' '.join(args)}: {error.stderr.strip()}") from error
    return done.stdout
