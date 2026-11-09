"""``gleanyard research check``: may an unattended research run become a pull request?

The opt-in ``research`` workflow lets a coding agent follow the Research procedure and write
``candidates/<host>.yaml`` files. This check then decides, without a model, whether that edit
may be proposed: new candidate files only, one per host, each naming a ``url`` on that host and
``why`` it fits, none for a host that is already a source or excluded, and no more than the
run's limit. Setting a candidate up stays a separate, reviewed step.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gleanyard.probe import Option
from gleanyard.project import load_project
from gleanyard.repair import changed_files, unborn

CANDIDATES = "candidates"
MAX_CANDIDATES = 10


class Candidate(BaseModel):
    """``candidates/<host>.yaml``: a page proposed as a source, not set up yet."""

    model_config = ConfigDict(extra="allow")

    url: str = Field(description="The page or feed, on the host the file is named for.")
    why: str = Field(description="Why the page publishes what the schema describes.")
    option: Option | str | None = Field(
        default=None,
        description="The best option from `gleanyard probe URL --json`, or a line about it.",
    )
    legal_pages: list[str] = Field(
        default_factory=list, description="Imprint and terms pages that `probe` found."
    )
    recipe: str | None = Field(
        default=None, description="A registry recipe that fits, for `gleanyard add`."
    )


class CandidateCheck(BaseModel):
    path: str
    host: str | None = None
    url: str | None = None
    problems: list[str] = Field(default_factory=list)


class ResearchCheck(BaseModel):
    base: str
    candidates: list[CandidateCheck] = Field(default_factory=list)
    problems: list[str] = Field(
        default_factory=list, description="Reasons the change cannot be proposed at all."
    )
    notes: list[str] = Field(default_factory=list)

    @property
    def files(self) -> list[str]:
        return [item.path for item in self.candidates]

    @property
    def proposable(self) -> bool:
        return (
            bool(self.candidates)
            and not self.problems
            and all(not item.problems for item in self.candidates)
        )

    def summary(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json"),
            "files": self.files,
            "proposable": self.proposable,
            "markdown": self.markdown(),
        }

    def markdown(self) -> str:
        verdict = "**ready for review**" if self.proposable else "**not proposable**"
        lines = [f"## Research check: {verdict}", ""]
        lines += [f"- Problem: {problem}" for problem in self.problems]
        lines += [f"- Note: {note}" for note in self.notes]
        for item in self.candidates:
            mark = "ok" if not item.problems else "FAIL"
            lines.append(f"- `{item.path}` ({item.url or 'no url'}): {mark}")
            lines += [f"  - {problem}" for problem in item.problems]
        return "\n".join(lines) + "\n"


def check_research(
    root: Path, base: str = "HEAD", max_candidates: int = MAX_CANDIDATES
) -> ResearchCheck:
    """Judge the working tree's changes against ``base`` as an automated research run."""
    project = load_project(root)
    result = ResearchCheck(base=base)
    changes = changed_files(project.root, base)
    if not changes:
        result.problems.append("nothing changed")
        return result
    first_commit = unborn(project.root, base)
    skipped = 0
    for change in changes:
        path = PurePosixPath(change.path)
        if path.parent != PurePosixPath(CANDIDATES) or path.suffix != ".yaml":
            if first_commit:
                skipped += 1  # the project itself, not yet committed
                continue
            result.problems.append(f"{change.path}: research may add candidates/<host>.yaml only")
        elif change.status != "A":
            result.problems.append(f"{change.path}: research may add candidates, not change them")
        else:
            result.candidates.append(_check_candidate(project, change.path))
    if first_commit:
        result.notes.append(
            f"no commit yet: every file is new; {skipped} file(s) outside {CANDIDATES}/ "
            "are taken as the project's first commit and not judged"
        )
        if not result.candidates:
            result.problems.append(f"no new {CANDIDATES}/<host>.yaml files")
    if len(result.candidates) > max_candidates:
        result.problems.append(
            f"{len(result.candidates)} candidates; a research run may propose {max_candidates}"
        )
    return result


def _check_candidate(project, path: str) -> CandidateCheck:
    item = CandidateCheck(path=path, host=PurePosixPath(path).stem.lower())
    try:
        data = yaml.safe_load((project.root / path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        item.problems.append(f"not readable YAML: {error}")
        return item
    if not isinstance(data, dict):
        item.problems.append("expected a mapping with url and why")
        return item
    url = data.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
        item.problems.append("`url` must be an http(s) URL")
        return item
    item.url = url
    host = (urlsplit(url).hostname or "").lower()
    if host != item.host:
        item.problems.append(f"the file is named for {item.host} but url is on {host}")
    if not isinstance(data.get("why"), str) or not data["why"].strip():
        item.problems.append("`why` must say why the page fits the schema")
    try:
        Candidate.model_validate(data)
    except ValidationError as error:
        for issue in error.errors():
            where = ".".join(str(part) for part in issue["loc"])
            if where not in {"url", "why"}:  # reported above in plainer words
                item.problems.append(f"`{where}`: {issue['msg']}")
    known = project.host_status(url)
    if known["status"] == "source":
        item.problems.append(f"{host} is already a source: {', '.join(known['sources'])}")
    elif known["status"] == "excluded":
        item.problems.append(f"{host} is in excluded_hosts")
    return item
