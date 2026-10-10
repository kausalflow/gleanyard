"""The Berlin family-events seed: its schema is valid and enforces what the brief promises."""

import shutil
from datetime import date
from pathlib import Path

import pytest

from gleanyard.init import init_project
from gleanyard.project import load_project

EXAMPLE = Path(__file__).parents[1] / "examples" / "berlin-family"
TODAY = date(2026, 10, 10)
GOOD = {
    "title": "Puppentheater: Der kleine Drache",
    "start_date": "2026-10-17",
    "time_text": "15 Uhr",
    "venue": "Kinderkulturhaus",
    "postal_code": "10405",
    "age_min": 3,
    "age_max": 8,
    "free": False,
    "price": "6,50 €",
    "indoor": True,
    "source_url": "/events/drache",
}


@pytest.fixture
def project(tmp_path: Path):
    root = tmp_path / "berlin-family-events"
    init_project(root, "berlin-family-events", "family-event")
    for name in ("schema.yaml", "research.md"):
        shutil.copy(EXAMPLE / name, root / name)
    loaded = load_project(root)
    assert loaded.ok, loaded.issues
    return loaded


def check(project, **changes):
    values = {**GOOD, **changes}
    return project.schema.validate_record(
        values, source_id="s", base_url="https://venue.example/", today=TODAY
    )


def test_a_family_event_validates(project):
    result = check(project)
    assert result.record is not None, result.errors
    assert result.record.values["age_max"] == 8
    assert result.record.values["indoor"] is True


@pytest.mark.parametrize("postal_code", ["10001", "14199", "10117", "13599", "12489"])
def test_berlin_postcodes(project, postal_code):
    result = check(project, postal_code=postal_code)
    assert result.record is not None, result.errors


@pytest.mark.parametrize("postal_code", ["14200", "15230", "09999", "20095", "50667"])
def test_postcodes_outside_berlin_are_rejected(project, postal_code):
    assert check(project, postal_code=postal_code).record is None


def test_free_events_may_have_no_price(project):
    values = {key: value for key, value in GOOD.items() if key != "price"}
    result = project.schema.validate_record(
        {**values, "free": "ja"}, source_id="s", base_url="https://venue.example/", today=TODAY
    )
    assert result.record is not None, result.errors
    assert result.record.values["free"] is True


def test_ages_are_bounded(project):
    assert check(project, age_max=25).record is None
    assert check(project, age_min=-1).record is None


def test_past_events_are_rejected(project):
    assert check(project, start_date="2026-09-01").record is None
