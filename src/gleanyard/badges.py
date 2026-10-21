"""``gleanyard badge``: status badges for a project README, drawn from ``status.json``.

``badges/project.svg`` sums the project up (``3 ok``, ``1 degraded``, ``2 failing``), and
``badges/sources/<id>.svg`` shows one source's health. The files are plain SVG in the repo,
so a README shows them with a relative link and no badge service sees the project.
"""

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path

from gleanyard.status import ProjectStatus, SourceStatus

COLOURS = {"ok": "#1a7f37", "degraded": "#b25e00", "failing": "#cf222e", "disabled": "#6e7781"}
LABEL_COLOUR = "#555"
CHAR_WIDTH = 7  # px per character at 11px Verdana, close enough for short labels
PADDING = 10


def project_badge(status: ProjectStatus, label: str = "collection") -> str:
    counts = Counter(
        source.health
        for source in status.sources
        if source.health != "disabled" and not _uncollected(source)
    )
    if not counts:
        value = "not collected yet" if status.sources else "no sources"
        return render(label, value, COLOURS["disabled"])
    worst = next(health for health in ("failing", "degraded", "ok") if counts[health])
    parts = [
        f"{counts[health]} {health}" for health in ("failing", "degraded", "ok") if counts[health]
    ]
    return render(label, ", ".join(parts), COLOURS[worst])


def source_badge(source: SourceStatus) -> str:
    if _uncollected(source):
        return render(source.id, "not collected yet", COLOURS["disabled"])
    if source.health == "ok" and source.terms is not None and source.terms.changed:
        return render(source.id, "terms changed", COLOURS["degraded"])
    return render(source.id, source.health, COLOURS[source.health])


def _uncollected(source: SourceStatus) -> bool:
    return source.health == "ok" and source.checked_at is None


def render(label: str, value: str, colour: str) -> str:
    """A flat two-part badge, like the ones badge services draw."""
    left = len(label) * CHAR_WIDTH + PADDING
    right = len(value) * CHAR_WIDTH + PADDING
    width = left + right
    label, value = escape(label), escape(value)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" '
        f'aria-label="{label}: {value}"><title>{label}: {value}</title>'
        f'<clipPath id="r"><rect width="{width}" height="20" rx="3" fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)"><rect width="{left}" height="20" fill="{LABEL_COLOUR}"/>'
        f'<rect x="{left}" width="{right}" height="20" fill="{colour}"/></g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        'font-size="11">'
        f'<text x="{left / 2}" y="14">{label}</text>'
        f'<text x="{left + right / 2}" y="14">{value}</text></g></svg>\n'
    )


def write_badges(status: ProjectStatus, directory: Path) -> list[Path]:
    """Write the project badge and one badge per source; remove badges of removed sources."""
    sources_dir = directory / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    written = [directory / "project.svg"]
    written[0].write_text(project_badge(status), encoding="utf-8")
    for source in status.sources:
        path = sources_dir / f"{source.id}.svg"
        path.write_text(source_badge(source), encoding="utf-8")
        written.append(path)
    keep = set(written)
    for path in sources_dir.glob("*.svg"):
        if path not in keep:
            path.unlink()
    return written
