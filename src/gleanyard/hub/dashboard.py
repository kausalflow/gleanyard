"""Render the hub snapshot as one static, self-contained HTML page (no JavaScript).

Every project is a row with a red, amber or green marker; opening it lists its sources and the
setup or repair pull requests waiting on a review. Pages deploy to GitHub Pages.
``render_summary`` gives the same overview as Markdown, for the workflow's job summary, which
only people with access to the hub repo can see.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape

from gleanyard.hub.state import HubState, RepoState
from gleanyard.status import FILL_MIN_BASELINE, SourceStatus

CSS = """
:root{--bg:#fff;--fg:#1c1f24;--muted:#5d6672;--line:#d9dee5;--card:#f6f8fa;
--green:#1a7f37;--amber:#b25e00;--red:#cf222e;--link:#0550ae}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--fg:#e6e9ee;--muted:#9aa4b0;
--line:#2b333d;--card:#161c23;--green:#3fb950;--amber:#d29922;--red:#f85149;--link:#79b8ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.5 system-ui,sans-serif}
main{max-width:56rem;margin:0 auto;padding:1.5rem 1rem 3rem}
h1{font-size:1.5rem;margin:0 0 .25rem}.muted{color:var(--muted);font-size:.875rem}
a{color:var(--link)}details{background:var(--card);border:1px solid var(--line);
border-radius:8px;margin:.75rem 0;padding:0 1rem}
summary{cursor:pointer;padding:.75rem 0;display:flex;gap:.6rem;align-items:center;
flex-wrap:wrap}
.dot{width:.8rem;height:.8rem;border-radius:50%;flex:none}
.green{background:var(--green)}.amber{background:var(--amber)}.red{background:var(--red)}
.repo{font-weight:600}.grow{flex:1}
table{width:100%;border-collapse:collapse;margin:.25rem 0 1rem;font-size:.875rem}
th,td{text-align:left;padding:.35rem .5rem;border-top:1px solid var(--line);
vertical-align:top;overflow-wrap:anywhere}th{color:var(--muted);font-weight:500}
.num{text-align:right;font-variant-numeric:tabular-nums}
.tag{font-size:.75rem;padding:.05rem .45rem;border-radius:999px;border:1px solid currentColor}
.tag.ok,.tag.disabled{color:var(--muted)}.tag.degraded{color:var(--amber)}
.tag.failing{color:var(--red)}
.problem{color:var(--red)}
.fields td{border-top:none;padding-top:0}.fields details{background:none;border:none;
margin:0;padding:0}.fields summary{padding:.2rem 0;font-size:.8125rem;color:var(--muted)}
.fields table{margin:0 0 .5rem}.bar{display:inline-block;height:.5rem;border-radius:2px;
background:var(--green);vertical-align:middle;margin-right:.4rem}.bar.drift{background:var(--amber)}
@media (max-width:36rem){.hide-sm{display:none}}
"""


def viewer_name(repo: str) -> str:
    """A file name for a project's record viewer: owner/name becomes owner--name.html."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", repo.replace("/", "--")) + ".html"


DRIFT_RATIO = 0.8  # a field filling under 80% of its baseline is marked, before it alerts at 50%


def render_dashboard(state: HubState, title: str = "Collection status") -> str:
    counts = {"green": 0, "amber": 0, "red": 0}
    for repo in state.repos:
        counts[repo.level] += 1
    rows = "\n".join(_repo(repo, state.generated_at) for repo in state.repos)
    problems = "".join(f"<p class=problem>{escape(problem)}</p>" for problem in state.problems)
    summary = ", ".join(f"{counts[level]} {level}" for level in ("red", "amber", "green"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body><main>
<h1>{escape(title)}</h1>
<p class="muted">{len(state.repos)} projects ({summary}). Updated {_stamp(state.generated_at)}.</p>
{problems}{rows}
</main></body></html>
"""


def _stamp(moment: datetime | None) -> str:
    return escape(f"{moment:%Y-%m-%d %H:%M} UTC") if moment else "never"


def _repo(repo: RepoState, now: datetime) -> str:
    status = repo.status
    note = escape(repo.problem) if repo.problem else ""
    if status:
        broken = [s for s in status.sources if s.health in {"failing", "degraded"}]
        note = f"{len(status.sources)} sources" + (
            f", {len(broken)} need attention" if broken else ""
        )
        note += f" · last run {_stamp(status.generated_at)}"
    body = ""
    if status and status.sources:
        body += (
            "<table><tr><th>Source</th><th>State</th><th class=num>Records</th>"
            "<th class='hide-sm'>Last success</th><th>Detail</th></tr>"
        )
        for source in status.sources:
            detail = escape(source.error or source.note or "")
            if source.error_kind and source.error:
                detail = f"<code>{escape(source.error_kind)}</code> {detail}"
            if source.terms is not None and source.terms.changed:
                detail += " <span class='tag degraded'>terms changed</span>"
            body += (
                f"<tr><td>{escape(source.id)}</td>"
                f"<td><span class='tag {escape(source.health)}'>{escape(source.health)}</span></td>"
                f"<td class=num>{source.records}</td>"
                f"<td class='hide-sm'>{_stamp(source.last_success_at)}</td><td>{detail}</td></tr>"
            )
            body += _fields(source)
        body += "</table>"
    if repo.pulls:
        body += "<table><tr><th>Waiting pull request</th><th>Open since</th></tr>"
        for pull in repo.pulls:
            days = (now - pull.created_at).days
            body += (
                f"<tr><td><a href='{escape(pull.url)}'>#{pull.number} {escape(pull.title)}</a></td>"
                f"<td>{days} day(s)</td></tr>"
            )
        body += "</table>"
    if repo.on_github:
        link = f"https://github.com/{escape(repo.repo)}"
        links = [f"<a href='{link}/actions'>Actions</a>", f"<a href='{link}'>Repo</a>"]
    else:
        links = [f"<a href='{escape(repo.url or '')}'>status.json</a>"]
    if repo.export is not None:
        if repo.export.problem:
            links.append(f"<span class=problem>records: {escape(repo.export.problem)}</span>")
        else:
            page = f"records/{viewer_name(repo.repo)}"
            links.insert(0, f"<a href='{escape(page)}'>Browse {repo.export.total} records</a>")
    actions = f"<p class=muted>{' · '.join(links)}</p>"
    return (
        f"<details{' open' if repo.level != 'green' else ''}><summary>"
        f"<span class='dot {repo.level}' role=img aria-label='{repo.level}'></span>"
        f"<span class=repo>{escape(repo.repo)}</span><span class='muted grow'>{note}</span>"
        f"</summary>{body}{actions}</details>"
    )


def drifting_fields(source: SourceStatus) -> list[str]:
    """Fields filling clearly less than in the baseline run, though not yet a warning."""
    baseline = source.baseline_fill_rates
    return [
        name
        for name, before in sorted(baseline.items())
        if before >= FILL_MIN_BASELINE
        and source.field_fill_rates.get(name, 0.0) < before * DRIFT_RATIO
    ]


def _fields(source: SourceStatus) -> str:
    """How often each field is filled now and in the baseline run, as a folded table."""
    names = sorted(set(source.field_fill_rates) | set(source.baseline_fill_rates))
    if not names:
        return ""
    drifting = set(drifting_fields(source))
    rows = ""
    for name in names:
        now = source.field_fill_rates.get(name, 0.0)
        before = source.baseline_fill_rates.get(name)
        kind = "bar drift" if name in drifting else "bar"
        bar = f"<span class='{kind}' style='width:{now * 4:.2f}rem'></span>"
        rows += (
            f"<tr><td>{escape(name)}</td><td>{bar}{now:.0%}</td>"
            f"<td class=num>{'' if before is None else f'{before:.0%}'}</td></tr>"
        )
    label = f"Fields ({len(drifting)} filling less than before)" if drifting else "Fields"
    return (
        f"<tr class=fields><td colspan=5><details{' open' if drifting else ''}>"
        f"<summary>{label}</summary><table><tr><th>Field</th><th>Filled now</th>"
        f"<th class=num>Baseline</th></tr>{rows}</table></details></td></tr>"
    )


MARKERS = {"green": "🟢", "amber": "🟠", "red": "🔴"}


def render_summary(state: HubState, title: str = "Collection status") -> str:
    """The dashboard as Markdown: one line per project, a table for those that need attention."""
    lines = [f"# {title}", "", f"Updated {state.generated_at:%Y-%m-%d %H:%M} UTC.", ""]
    lines += [f"> {problem}" for problem in state.problems]
    for repo in state.repos:
        if repo.status is None:
            lines.append(f"- {MARKERS['red']} **{repo.repo}**: {repo.problem}")
            continue
        sources = repo.status.sources
        broken = [s for s in sources if s.health in {"failing", "degraded"}]
        lines.append(
            f"- {MARKERS[repo.level]} **{repo.repo}**: {len(sources)} sources, "
            f"{sum(s.records for s in sources)} records"
            + (f", {len(broken)} need attention" if broken else "")
        )
        for source in broken:
            detail = source.error or source.note or ""
            lines.append(f"  - `{source.id}` {source.health}: {detail}")
        for source in sources:
            if source.terms is not None and source.terms.changed:
                lines.append(f"  - `{source.id}` terms changed: {', '.join(source.terms.changed)}")
            drifting = drifting_fields(source) if source.health == "ok" else []
            if drifting:
                lines.append(f"  - `{source.id}` fields filling less: {', '.join(drifting)}")
        for pull in repo.pulls:
            lines.append(f"  - waiting: [#{pull.number} {pull.title}]({pull.url})")
    return "\n".join(lines) + "\n"
