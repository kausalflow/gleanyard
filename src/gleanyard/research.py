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
from pydantic import BaseModel, Field

from gleanyard.project import load_project
from gleanyard.repair import changed_files

CANDIDATES = "candidates"
MAX_CANDIDATES = 10


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
    for change in changes:
        path = PurePosixPath(change.path)
        if path.parent != PurePosixPath(CANDIDATES) or path.suffix != ".yaml":
            result.problems.append(f"{change.path}: research may add candidates/<host>.yaml only")
        elif change.status != "A":
            result.problems.append(f"{change.path}: research may add candidates, not change them")
        else:
            result.candidates.append(_check_candidate(project, change.path))
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
    known = project.host_status(url)
    if known["status"] == "source":
        item.problems.append(f"{host} is already a source: {', '.join(known['sources'])}")
    elif known["status"] == "excluded":
        item.problems.append(f"{host} is in excluded_hosts")
    return item
