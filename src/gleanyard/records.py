"""How stored records (``data/records/<source>.jsonl``) are written and read.

``full``, the default, writes one ``OutputRecord`` per line in the order the run found them.
``compact`` (``storage.records: compact``) is for sources with many thousands of records:
the first line lists each distinct provenance once, and every following line, sorted by id,
holds a record with the index of its provenance instead of a copy. Sorting keeps a commit's
diff to the records that changed. Both read back to the same records, so a project can
switch at any time; the next collect rewrites the file.
"""

from __future__ import annotations

import json
from typing import Literal

from gleanyard.collect import OutputRecord

COMPACT = "gleanyard.records/compact-v1"


def dump_records(records: list[OutputRecord], layout: Literal["full", "compact"]) -> bytes:
    if layout == "full":
        return "".join(record.model_dump_json() + "\n" for record in records).encode()
    index: dict[str, int] = {}
    provenance: list[dict] = []
    lines = []
    for record in sorted(records, key=lambda item: item.id):
        key = record.provenance.model_dump_json()
        if key not in index:
            index[key] = len(provenance)
            provenance.append(record.provenance.model_dump(mode="json"))
        line = record.model_dump(mode="json", exclude={"provenance"}, exclude_defaults=True)
        line["values"] = record.values
        line["p"] = index[key]
        lines.append(json.dumps(line, ensure_ascii=False, sort_keys=True))
    header = json.dumps({"format": COMPACT, "provenance": provenance}, ensure_ascii=False)
    return "".join(line + "\n" for line in [header, *lines]).encode()


def parse_records(text: str) -> list[OutputRecord]:
    # Split on "\n" only: str.splitlines() also breaks on U+2028, U+0085 and form feeds,
    # which JSON leaves raw inside strings.
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return []
    first = json.loads(lines[0])
    if first.get("format") != COMPACT:
        return [OutputRecord.model_validate_json(line) for line in lines]
    provenance = first["provenance"]
    records = []
    for line in lines[1:]:
        item = json.loads(line)
        item["provenance"] = provenance[item.pop("p")]
        records.append(OutputRecord.model_validate(item))
    return records
