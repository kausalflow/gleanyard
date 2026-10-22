import pytest
from pydantic import ValidationError

from gleanyard.merge import merge_rows
from gleanyard.schema import ProjectSchema


def schema(merge):
    return ProjectSchema.model_validate(
        {
            "name": "event",
            "identity": ["title"],
            "merge": merge,
            "fields": {
                "title": {"required": True},
                "start_date": {"type": "date"},
                "venue": {},
                "price": {},
                "email": {"class": "personal"},
            },
        }
    )


def row(id, source, **values):
    return {"id": id, **values, "_source": {"source_id": source, "url": f"https://{source}/"}}


ROWS = [
    row("a1", "a", title="Jazz  im Park", start_date="2026-10-12", venue="Park"),
    row("b1", "b", title="jazz im park", start_date="2026-10-12", venue="park", price="5 EUR"),
    row("b2", "b", title="Lauftreff", start_date="2026-10-13"),
    row("c1", "c", title="Jazz im Park", start_date="2026-10-19", venue="Park"),
]


def test_rows_agreeing_on_the_merge_fields_become_one():
    merged = merge_rows(ROWS, schema(["title", "start_date", "venue"]))
    assert [item["id"][:7] for item in merged] == ["merged_", "b2", "c1"]
    joined = merged[0]
    # Each value comes from the first source that exports the field.
    assert joined["title"] == "Jazz  im Park" and joined["venue"] == "Park"
    assert joined["price"] == "5 EUR"
    assert joined["_source"]["source_id"] == "a"
    assert [(s["source_id"], s["id"]) for s in joined["_sources"]] == [("a", "a1"), ("b", "b1")]
    again = merge_rows(ROWS, schema(["title", "start_date", "venue"]))
    assert again[0]["id"] == joined["id"]  # stable across exports


def test_a_row_missing_a_merge_field_is_left_alone():
    merged = merge_rows(ROWS, schema(["title", "venue"]))
    assert [item["id"] for item in merged][1:] == ["b2"]
    assert len(merged[0]["_sources"]) == 3


def test_without_merge_fields_rows_are_unchanged():
    assert merge_rows(ROWS, schema([])) == ROWS


def test_merge_fields_must_exist_and_never_be_personal():
    with pytest.raises(ValidationError, match="unknown fields: city"):
        schema(["title", "city"])
    with pytest.raises(ValidationError, match="personal"):
        schema(["title", "email"])
