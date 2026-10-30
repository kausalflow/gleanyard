from datetime import UTC, date, datetime

import pytest

from gleanyard.collect import OutputRecord, Provenance
from gleanyard.export import csv_text, public_record
from gleanyard.rights import RightsPolicy, SourceRights
from gleanyard.schema import ProjectSchema

TODAY = date(2026, 10, 10)
SCHEMA = ProjectSchema.model_validate(
    {
        "name": "event",
        "identity": ["title"],
        "fields": {
            "title": {"required": True},
            "source_url": {"type": "url", "required": True},
            "description": {"type": "text", "class": "protected_text"},
            "image_url": {"type": "url", "class": "image"},
            "organizer_email": {"class": "personal"},
        },
    }
)
RECORD = OutputRecord(
    id="rec_1",
    values={
        "title": "Jazz",
        "source_url": "https://example.org/e/1",
        "description": "Long text",
        "image_url": "https://example.org/i.jpg",
        "organizer_email": "a@example.org",
    },
    provenance=Provenance(
        source_id="src",
        url="https://example.org/feed",
        retrieved_at=datetime(2026, 10, 10, tzinfo=UTC),
        raw_sha256="0" * 64,
        mode="fields",
        schema_version=1,
    ),
)
ALL_FIELDS = ["title", "source_url", "description", "image_url", "organizer_email"]


def rights(**overrides) -> SourceRights:
    document = {
        "review_status": "approved",
        "effective_tier": "facts_only",
        "allowed_actions": {"redistribute_facts": True},
        "public_fields": ALL_FIELDS,
    }
    document.update(overrides)
    return SourceRights.model_validate(document)


def export(source_rights):
    return public_record(RECORD, SCHEMA, {"src": source_rights}, RightsPolicy(), TODAY)


def test_facts_only_exports_fact_fields():
    row = export(rights())
    assert row == {
        "id": "rec_1",
        "title": "Jazz",
        "source_url": "https://example.org/e/1",
        "_source": {
            "source_id": "src",
            "url": "https://example.org/feed",
            "retrieved_at": "2026-10-10T00:00:00+00:00",
            "mode": "fields",
        },
    }


def test_text_and_images_need_their_own_permission_and_personal_never_exports():
    row = export(
        rights(
            allowed_actions={
                "redistribute_facts": True,
                "redistribute_text": True,
                "redistribute_images": True,
            }
        )
    )
    assert row["description"] == "Long text"
    assert row["image_url"] == "https://example.org/i.jpg"
    assert "organizer_email" not in row


def test_field_must_be_listed_public():
    assert export(rights(public_fields=["title", "source_url"])).keys() == {
        "id",
        "title",
        "source_url",
        "_source",
    }


def test_missing_required_public_field_drops_record():
    assert export(rights(public_fields=["title"])) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"review_status": "pending"},
        {"effective_tier": "internal_only"},
        {"expires_at": "2026-10-09"},
        {"attribution_required": True},
        {"effective_tier": "open"},
    ],
)
def test_source_level_blocks(overrides):
    assert export(rights(**overrides)) is None


def test_removed_source_drops_records():
    assert public_record(RECORD, SCHEMA, {}, RightsPolicy(), TODAY) is None


def test_open_licence_is_carried():
    row = export(
        rights(
            effective_tier="open_attribution",
            license_id="dl-de/by-2-0",
            license_url="https://www.govdata.de/dl-de/by-2-0",
            attribution_required=True,
            attribution_text="Stadt Köln",
        )
    )
    assert row["_source"]["license"] == "dl-de/by-2-0"
    assert row["_source"]["attribution"] == "Stadt Köln"


def test_csv_has_one_column_per_public_field_and_flattens_values():
    row = export(rights(license_id="CC0-1.0", license_url="https://example.org/cc0"))
    row["tags"] = ["a", "b"]
    schema = SCHEMA.model_copy(deep=True)
    schema.fields["tags"] = schema.fields["title"].model_copy(update={"required": False})
    lines = csv_text([row, {"id": "rec_2", "title": 'Say "hi", all'}], schema).splitlines()
    assert lines[0] == (
        "id,title,source_url,description,image_url,tags,_source,_source_url,_attribution,_license"
    )
    assert lines[1] == (
        "rec_1,Jazz,https://example.org/e/1,,,a; b,src,https://example.org/feed,,CC0-1.0"
    )
    assert lines[2] == 'rec_2,"Say ""hi"", all",,,,,,,,'
