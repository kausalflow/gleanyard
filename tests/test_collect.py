from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from gleanyard.collect import extract
from gleanyard.config import SourceConfig
from gleanyard.fetch import Response
from gleanyard.schema import ProjectSchema

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 10)


def load(path: Path):
    return yaml.safe_load(path.read_text())


def response(body: bytes) -> Response:
    return Response(
        url="https://example.org/feed",
        status_code=200,
        content_type=None,
        sha256="0" * 64,
        size=len(body),
        retrieved_at=datetime(2026, 10, 10, tzinfo=UTC),
        body=body,
    )


def test_ical_source_end_to_end():
    schema = ProjectSchema.model_validate(load(ROOT / "examples/events/schema.yaml"))
    document = load(ROOT / "examples/events/sources/aachen-city-events.yaml")
    document["extract"]["map"]["time_text"] = "TIME"
    document["extract"]["map"]["categories"] = "CATEGORIES"
    source = SourceConfig.model_validate(document)

    result = extract(source, schema, response((FIXTURES / "calendar.ics").read_bytes()), TODAY)

    by_title = {record.values["title"]: record for record in result.records}
    concert = by_title["Jazz im Park"].values
    assert concert == {
        "title": "Jazz im Park",
        "start_date": "2026-10-12",
        "end_date": "2026-10-12",
        "time_text": "19:30–21:30",
        "venue": "Stadtgarten",
        "city": "Aachen",
        "source_url": "https://example.org/e/1",
        "categories": ["music", "outdoor"],
        "description": "Open air",
    }
    assert "time_text" not in by_title["Herbstmarkt"].values
    assert by_title["Herbstmarkt"].provenance.mode == "fields"

    # Past dates are outside the schema's scope (min: today-1), including the
    # weekly occurrence the reader keeps from its look-back window.
    assert sorted(r.values["title"] for r in result.rejections) == ["Already over", "Lauftreff"]
    assert {r.errors[0].field for r in result.rejections} == {"start_date"}
    # Each weekly occurrence is its own record.
    assert sum(1 for r in result.records if r.values["title"] == "Lauftreff") > 40
    assert result.field_fill_rates["title"] == 1.0


def test_json_source_with_defaults_and_paths():
    schema = ProjectSchema.model_validate(load(ROOT / "examples/events/schema.yaml"))
    source = SourceConfig.model_validate(
        {
            "id": "demo",
            "schema": "event",
            "delivery": "json",
            "url": "https://example.org/api/events",
            "reader": {"items_path": "items"},
            "extract": {
                "mode": "fields",
                "map": {
                    "title": "name",
                    "start_date": "starts",
                    "source_url": "link",
                    "price": "offers.0.price",
                    "postal_code": "place.zip",
                },
                "defaults": {"city": "Köln"},
            },
            "rights": {},
        }
    )
    body = b"""{"items": [
      {"name": "A", "starts": "12.10.2026", "link": "/a", "offers": [{"price": "9,90 EUR"}],
       "place": {"zip": "50667"}},
      {"name": "B", "starts": "2026-10-13", "link": "/b", "place": {"zip": "10115"}},
      {"name": "A", "starts": "12.10.2026", "link": "/a-duplicate"}
    ]}"""
    result = extract(source, schema, response(body), TODAY)
    assert [record.values for record in result.records] == [
        {
            "title": "A",
            "start_date": "2026-10-12",
            "postal_code": "50667",
            "city": "Köln",
            "source_url": "https://example.org/a",
            "price": {"amount": "9.90", "currency": "EUR"},
        }
    ]
    # Berlin postcode is outside the NRW scope.
    assert result.rejections[0].errors[0].field == "postal_code"
