import yaml

from gleanyard.config import FieldsExtract
from gleanyard.extract.fields import map_item
from gleanyard.project import load_project

EXTRACT = FieldsExtract.model_validate(
    {
        "mode": "fields",
        "map": {"country_code": "REF_AREA", "value": "OBS_VALUE"},
        "defaults": {"source": "OECD"},
        "cases": [
            {"when": {"MEASURE": "CPI", "TRANSFORMATION": "GY"}, "set": {"indicator": "cpi_yoy"}},
            {
                "when": {"MEASURE": ["CPI", "HICP"], "TRANSFORMATION": "_Z"},
                "set": {"indicator": "cpi_index", "unit": "Index"},
            },
        ],
    }
)


def test_the_first_matching_case_sets_its_fields():
    row = {"REF_AREA": "DEU", "OBS_VALUE": "2.1", "MEASURE": "CPI", "TRANSFORMATION": "GY"}
    assert map_item(row, EXTRACT) == {
        "source": "OECD",
        "country_code": "DEU",
        "value": "2.1",
        "indicator": "cpi_yoy",
    }
    row |= {"MEASURE": "HICP", "TRANSFORMATION": "_Z"}
    assert map_item(row, EXTRACT)["indicator"] == "cpi_index"


def test_an_item_no_case_matches_is_skipped():
    assert map_item({"MEASURE": "PPI", "TRANSFORMATION": "GY"}, EXTRACT) is None
    assert map_item({"MEASURE": "CPI"}, EXTRACT) is None


def test_without_cases_every_item_maps():
    plain = FieldsExtract.model_validate({"mode": "fields", "map": {"a": "b"}})
    assert map_item({"b": 1}, plain) == {"a": 1}


def test_rewrite_substitutes_in_mapped_text():
    extract = FieldsExtract.model_validate(
        {
            "mode": "fields",
            "map": {"year_month": "TIME_PERIOD", "value": "OBS_VALUE"},
            "rewrite": {"year_month": {"pattern": r"^(\d{4})-M(\d{2})$", "replace": r"\1-\2"}},
        }
    )
    assert map_item({"TIME_PERIOD": "2026-M08", "OBS_VALUE": 1.5}, extract) == {
        "year_month": "2026-08",
        "value": 1.5,
    }
    assert map_item({"TIME_PERIOD": "2026-08"}, extract) == {"year_month": "2026-08"}


def test_cases_and_rewrite_fields_are_checked(tmp_path):
    (tmp_path / "sources").mkdir()
    schema = {"name": "obs", "identity": ["indicator"], "fields": {"indicator": {"required": True}}}
    (tmp_path / "schema.yaml").write_text(yaml.safe_dump(schema))
    source = {
        "id": "s",
        "schema": "obs",
        "delivery": "csv",
        "url": "https://x.example/a.csv",
        "extract": {
            "mode": "fields",
            "map": {},
            "cases": [{"when": {"M": "A"}, "set": {"indicator": "a"}}],
        },
        "rights": {},
    }
    (tmp_path / "sources" / "s.yaml").write_text(yaml.safe_dump(source))
    assert load_project(tmp_path).ok
    source["extract"]["cases"].append({"when": {"M": "B"}, "set": {"nope": 1}})
    source["extract"]["rewrite"] = {"other": {"pattern": "a", "replace": "b"}}
    (tmp_path / "sources" / "s.yaml").write_text(yaml.safe_dump(source))
    messages = " ".join(issue.message for issue in load_project(tmp_path).issues)
    assert "nope" in messages and "other" in messages and "indicator" in messages
