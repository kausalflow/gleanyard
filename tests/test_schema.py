from datetime import date

import pytest
from pydantic import ValidationError

from gleanyard.schema import ProjectSchema

TODAY = date(2026, 10, 10)


@pytest.fixture
def schema() -> ProjectSchema:
    return ProjectSchema.model_validate(
        {
            "name": "event",
            "identity": ["title", "start_date"],
            "fields": {
                "title": {"type": "string", "required": True, "max_length": 50},
                "start_date": {"type": "date", "required": True, "scope": {"min": "today"}},
                "postal_code": {
                    "type": "postcode",
                    "country": "DE",
                    "scope": {"pattern": "^5[0-3]"},
                },
                "source_url": {"type": "url", "required": True},
                "price": {"type": "money", "currency": "EUR"},
                "categories": {"type": "string", "many": True, "scope": {"enum": ["music", "art"]}},
                "description": {"type": "text", "class": "protected_text"},
            },
        }
    )


def validate(schema, values):
    return schema.validate_record(
        values, source_id="src", base_url="https://example.org/", today=TODAY
    )


def test_valid_record_is_normalized(schema):
    result = validate(
        schema,
        {
            "title": " <b>Jazz</b> night ",
            "start_date": "12.10.2026",
            "postal_code": "50667",
            "source_url": "/e/1",
            "price": "12 €",
            "categories": ["music", "music"],
        },
    )
    assert result.ok
    assert result.record.values == {
        "title": "Jazz night",
        "start_date": "2026-10-12",
        "postal_code": "50667",
        "source_url": "https://example.org/e/1",
        "price": {"amount": "12.00", "currency": "EUR"},
        "categories": ["music"],
    }
    assert result.record.id.startswith("rec_")


def test_missing_required_field_rejects(schema):
    result = validate(schema, {"title": "Jazz", "source_url": "https://x.org/"})
    assert not result.ok
    assert [issue.field for issue in result.errors] == ["start_date"]


def test_optional_field_failure_is_a_warning(schema):
    result = validate(
        schema,
        {
            "title": "Jazz",
            "start_date": "2026-10-12",
            "source_url": "https://x.org/",
            "price": "ask at the door",
        },
    )
    assert result.ok
    assert "price" not in result.record.values
    assert [issue.field for issue in result.warnings] == ["price"]


@pytest.mark.parametrize(
    "override",
    [
        {"start_date": "2026-10-09"},
        {"postal_code": "10115"},
        {"categories": ["sport"]},
    ],
)
def test_out_of_scope_rejects_even_optional_fields(schema, override):
    values = {"title": "Jazz", "start_date": "2026-10-12", "source_url": "https://x.org/"}
    result = validate(schema, values | override)
    assert not result.ok
    assert result.errors[0].field == next(iter(override))


def test_unknown_key_rejects(schema):
    result = validate(
        schema,
        {"title": "Jazz", "start_date": "2026-10-12", "source_url": "https://x.org/", "x": 1},
    )
    assert not result.ok
    assert result.errors[0].reason == "not a field in the schema"


def test_record_id_is_stable_and_case_insensitive(schema):
    first = schema.record_id("src", {"title": "Jazz", "start_date": "2026-10-12"})
    second = schema.record_id("src", {"title": "JAZZ", "start_date": "2026-10-12"})
    other_source = schema.record_id("other", {"title": "Jazz", "start_date": "2026-10-12"})
    assert first == second != other_source


def test_relative_date_bounds():
    loose = ProjectSchema.model_validate(
        {
            "name": "e",
            "identity": ["d"],
            "fields": {"d": {"type": "date", "required": True, "scope": {"min": "today-7"}}},
        }
    )
    assert loose.validate_record({"d": "2026-10-03"}, source_id="s", today=TODAY).ok
    assert not loose.validate_record({"d": "2026-10-02"}, source_id="s", today=TODAY).ok


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"identity": ["missing"], "fields": {"a": {}}}, "identity names unknown fields"),
        ({"identity": ["a"], "fields": {"a": {}}}, "at least one required field"),
        ({"identity": ["a"], "fields": {"A": {"required": True}}}, "lower_snake_case"),
        (
            {"identity": ["a"], "fields": {"a": {"required": True, "scope": {"min": 1}}}},
            "scope min/max only apply",
        ),
        (
            {
                "identity": ["a"],
                "fields": {"a": {"type": "date", "required": True, "scope": {"min": "soon"}}},
            },
            "Invalid isoformat",
        ),
    ],
)
def test_schema_rejects_bad_definitions(document, message):
    with pytest.raises(ValidationError, match=message):
        ProjectSchema.model_validate({"name": "e", **document})
