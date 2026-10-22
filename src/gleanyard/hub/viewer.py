"""A searchable table of one project's public export, as a static page beside the dashboard.

The rows come from the export the project already publishes (``exports/records.jsonl``),
which only holds fields each source's rights allow, so the viewer adds no new exposure beyond
publishing it on the hub's site. The page carries its rows as JSON and filters them in the
browser; it needs no server.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

from gleanyard.hub.dashboard import CSS, viewer_name
from gleanyard.hub.state import HubState, RepoState

VIEWER_DIR = "records"
CELL_CHARS = 200  # longer text is cut in the table

VIEWER_CSS = """
main{max-width:80rem}input[type=search]{width:100%;font:inherit;padding:.5rem .75rem;
border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);
margin:.5rem 0 1rem}.scroll{overflow-x:auto}td{max-width:24rem}
"""

SCRIPT = """
const data = JSON.parse(document.getElementById('rows').textContent);
const body = document.querySelector('tbody');
const count = document.getElementById('count');
const columns = data.columns;
const text = data.rows.map(r => columns.map(c => r[c] ?? '').join(' ').toLowerCase());
function cell(value) {
  const td = document.createElement('td');
  if (/^https?:\\/\\//.test(value)) {
    const a = document.createElement('a'); a.href = value; a.textContent = value; td.append(a);
  } else { td.textContent = value; }
  return td;
}
function show(query) {
  const words = query.toLowerCase().split(/\\s+/).filter(Boolean);
  body.replaceChildren();
  let shown = 0;
  data.rows.forEach((row, i) => {
    if (!words.every(w => text[i].includes(w))) return;
    shown += 1;
    if (shown > 500) return;
    const tr = document.createElement('tr');
    columns.forEach(c => tr.append(cell(row[c] ?? '')));
    body.append(tr);
  });
  count.textContent = shown + ' of ' + data.rows.length + ' rows' +
    (shown > 500 ? ' (first 500 shown; search to narrow)' : '');
}
document.getElementById('q').addEventListener('input', e => show(e.target.value));
show('');
"""


def table_rows(rows: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
    """Columns in first-seen order, with the source last, and each row's cells as text."""
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns and not key.startswith("_") and key != "id":
                columns.append(key)
    columns.append("source")
    cells = []
    for row in rows:
        sources = row.get("_sources") or [row.get("_source") or {}]
        item = {key: _text(row[key]) for key in columns if key in row}
        item["source"] = ", ".join(str(s.get("source_id", "")) for s in sources)
        cells.append(item)
    return columns, cells


def render_viewer(repo: RepoState, title: str = "Collection status") -> str:
    assert repo.export is not None
    sample = repo.export
    columns, cells = table_rows(sample.rows)
    payload = json.dumps({"columns": columns, "rows": cells}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")  # keep the JSON inside its script element
    more = (
        f" The first {len(sample.rows)} of {sample.total} rows are here."
        if sample.total > len(sample.rows)
        else ""
    )
    head = "".join(f"<th>{escape(column)}</th>" for column in columns)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(repo.repo)}: records</title><style>{CSS}{VIEWER_CSS}</style></head>
<body><main>
<p class="muted"><a href="../index.html">{escape(title)}</a></p>
<h1>{escape(repo.repo)}</h1>
<p class="muted">The project's public export: only fields each source's rights allow.{more}</p>
<input id="q" type="search" placeholder="Search records" aria-label="Search records">
<p class="muted" id="count"></p>
<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody></tbody></table></div>
<script type="application/json" id="rows">{payload}</script>
<script>{SCRIPT}</script>
</main></body></html>
"""


def _text(value: Any) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value)
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1] + "…"


def write_viewers(state: HubState, out: Path, title: str = "Collection status") -> list[Path]:
    """One viewer page per project whose export was read; returns the pages written."""
    directory = out / VIEWER_DIR
    written = []
    for repo in state.repos:
        if repo.export is None or repo.export.problem:
            continue
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / viewer_name(repo.repo)
        path.write_text(render_viewer(repo, title), encoding="utf-8")
        written.append(path)
    return written
