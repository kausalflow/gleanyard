import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError

from gleanyard.changes import load_changes
from gleanyard.dataherb import write_metadata
from gleanyard.merge import prefer_rows
from gleanyard.project import load_project
from gleanyard.records import COMPACT, dump_records, parse_records
from gleanyard.run import collect_source, export_project, load_records, record_store
from gleanyard.schema import ProjectSchema

EXAMPLE = Path(__file__).parents[1] / "examples" / "events"
FIXTURES = Path(__file__).parent / "fixtures"
CALENDAR = (FIXTURES / "calendar.ics").read_text()
SOURCE = "aachen-city-events"


def first_events(count: int) -> bytes:
    """The fixture calendar with only its first ``count`` events."""
    head, *events = CALENDAR.split("BEGIN:VEVENT")
    kept = events[:count]
    if count < len(events):
        kept[-1] = kept[-1].split("END:VEVENT")[0] + "END:VEVENT\r\n"
        return (
            head + "".join("BEGIN:VEVENT" + event for event in kept) + "END:VCALENDAR\r\n"
        ).encode()
    return CALENDAR.encode()


def client(body: bytes) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=body, headers={"content-type": "text/calendar"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def setup(root: Path, source: dict | None = None, config: dict | None = None) -> None:
    path = root / "sources" / f"{SOURCE}.yaml"
    document = yaml.safe_load(path.read_text())
    document.update(
        enabled=True,
        fetch={"delay_seconds": 0},
        rights={
            "review_status": "approved",
            "effective_tier": "facts_only",
            "allowed_actions": {
                "private_store": True,
                "normalize": True,
                "redistribute_facts": True,
            },
            "public_fields": ["title", "start_date", "city", "source_url"],
        },
        **(source or {}),
    )
    document["reader"].update(past_days=0, horizon_days=30)
    path.write_text(yaml.safe_dump(document))
    if config:
        (root / "gleanyard.yaml").write_text(yaml.safe_dump(config))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "events"
    shutil.copytree(EXAMPLE, root)
    return root


def collect(root: Path, body: bytes):
    project = load_project(root)
    assert project.ok, project.issues
    source = next(item for item in project.sources if item.id == SOURCE)
    return project, collect_source(project, source, client=client(body), today=date(2026, 10, 10))


def test_keep_removed_keeps_records_a_later_run_no_longer_finds(root):
    setup(root, {"keep_removed": True})
    project, run = collect(root, first_events(99))
    everything = {record.id for record in load_records(project, SOURCE)}
    assert run.ok and len(everything) == run.records > 2

    project, run = collect(root, first_events(2))
    assert run.ok and 0 < run.records < len(everything)  # the run reports what it found ...
    assert {record.id for record in load_records(project, SOURCE)} == everything  # ... and keeps
    kinds = [
        change.change for change in load_changes(record_store(project), since=date(2000, 1, 1))
    ]
    assert "removed" not in kinds


def test_without_keep_removed_a_later_run_replaces_the_records(root):
    setup(root)
    collect(root, first_events(99))
    project, run = collect(root, first_events(2))
    assert len(load_records(project, SOURCE)) == run.records == 1


def test_compact_records_are_sorted_with_provenance_once_and_read_back(root):
    setup(root, config={"storage": {"records": "compact"}})
    project, run = collect(root, first_events(99))
    lines = (root / "data" / "records" / f"{SOURCE}.jsonl").read_text().splitlines()
    header = json.loads(lines[0])
    assert header["format"] == COMPACT and len(header["provenance"]) == 1
    ids = [json.loads(line)["id"] for line in lines[1:]]
    assert ids == sorted(ids) and len(ids) == run.records
    assert all("provenance" not in json.loads(line) for line in lines[1:])

    records = load_records(project, SOURCE)
    assert {record.provenance.source_id for record in records} == {SOURCE}
    full = parse_records(dump_records(records, "full").decode())
    assert parse_records(dump_records(full, "compact").decode()) == sorted(
        full, key=lambda record: record.id
    )


def test_compact_records_keep_raw_line_separators(root):
    setup(root, config={"storage": {"records": "compact"}})
    project, _ = collect(root, first_events(99))
    records = load_records(project, SOURCE)
    records[0].values["title"] = "Parodien einmalig\x85gut."
    again = parse_records(dump_records(records, "compact").decode())
    assert any(record.values["title"] == "Parodien einmalig\x85gut." for record in again)


def test_partitioned_export_writes_one_file_per_value(root):
    config = {
        "export_partition": "start_date",
        "export_csv": "dataset/events.csv",
        "dataherb": {"id": "events"},
    }
    setup(root, config=config)
    project, run = collect(root, first_events(99))
    folder, count = export_project(project, today=date(2026, 10, 10))
    assert folder == root / "exports" / "records" and count == run.records
    files = sorted(folder.glob("*.jsonl"))
    assert len(files) > 1
    for file in files:
        rows = [json.loads(line) for line in file.read_text().splitlines()]
        assert {row["start_date"] for row in rows} == {file.stem}
    csvs = sorted((root / "dataset" / "events").glob("*.csv"))
    assert [file.stem for file in csvs] == [file.stem for file in files]

    write_metadata(project, count)
    package = json.loads((root / "dataherb.json").read_text())["datapackage"]
    paths = {resource["name"]: resource["path"] for resource in package["resources"]}
    assert paths["records"] == [f"exports/records/{file.name}" for file in files]
    assert paths["records-csv"] == "dataset/events.csv"  # the CSV is also written whole
    whole = (root / "dataset" / "events.csv").read_text().splitlines()
    assert len(whole) == count + 1

    stale = folder / "1999-01-01.jsonl"
    stale.write_text("{}\n")
    export_project(project, today=date(2026, 10, 10))
    assert not stale.exists()


def test_export_partition_must_name_a_shareable_field(root):
    setup(root, config={"export_partition": "nowhere"})
    assert any("export_partition" in issue.message for issue in load_project(root).issues)


def schema(series):
    return ProjectSchema.model_validate(
        {
            "name": "indicator",
            "identity": ["indicator", "country", "period"],
            "prefer": {"series": series},
            "fields": {
                "indicator": {"required": True},
                "country": {"required": True},
                "period": {"required": True},
                "value": {"type": "float"},
                "email": {"class": "personal"},
            },
        }
    )


def row(source, indicator, country, period, value=None):
    return {
        "id": f"{source}-{indicator}-{country}-{period}",
        "indicator": indicator,
        "country": country,
        "period": period,
        "value": value,
        "_source": {"source_id": source},
    }


def test_prefer_keeps_each_series_from_the_first_source_that_has_it():
    rows = [
        row("oecd", "cpi", "DEU", "2026-08"),
        row("oecd", "cpi", "DEU", "2026-09"),
        row("imf", "cpi", "DEU", "2026-07"),  # older period, still OECD's series
        row("imf", "cpi", "CHN", "2026-09"),
        {"id": "x", "indicator": "cpi", "_source": {"source_id": "imf"}},
    ]
    kept = prefer_rows(rows, schema(["indicator", "country"]))
    assert [item["id"] for item in kept] == [
        "oecd-cpi-DEU-2026-08",
        "oecd-cpi-DEU-2026-09",
        "imf-cpi-CHN-2026-09",
        "x",
    ]


def test_prefer_fields_must_exist_and_never_be_personal():
    with pytest.raises(ValidationError, match="unknown fields: region"):
        schema(["indicator", "region"])
    with pytest.raises(ValidationError, match="personal"):
        schema(["email"])
