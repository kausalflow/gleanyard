import yaml

from gleanyard.config import FieldsExtract
from gleanyard.extract.fields import join_tables, map_item
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


def test_where_skips_items_whose_text_does_not_match():
    extract = FieldsExtract.model_validate(
        {"mode": "fields", "map": {"country_code": "COUNTRY"}, "where": {"COUNTRY": "[A-Z]{3}"}}
    )
    assert map_item({"COUNTRY": "DEU"}, extract) == {"country_code": "DEU"}
    assert map_item({"COUNTRY": "G001"}, extract) is None  # the whole text must match
    assert map_item({}, extract) is None


def test_map_takes_the_first_path_with_a_value():
    extract = FieldsExtract.model_validate(
        {"mode": "fields", "map": {"country": ["name", "COUNTRY"]}}
    )
    assert map_item({"name": "Germany", "COUNTRY": "DEU"}, extract) == {"country": "Germany"}
    assert map_item({"name": "", "COUNTRY": "ECB"}, extract) == {"country": "ECB"}


def test_join_adds_a_lookup_files_columns(tmp_path):
    (tmp_path / "geo.csv").write_text("geo,country_code,country\nEL,GRC,Greece\nDE,DEU,\n")
    extract = FieldsExtract.model_validate(
        {
            "mode": "fields",
            "map": {"country_code": "country_code", "country": ["country", "geo"]},
            "join": [{"file": "geo.csv", "on": "geo", "unknown": "drop"}],
        }
    )
    tables = join_tables(tmp_path, extract)
    assert map_item({"geo": "EL"}, extract, tables) == {"country_code": "GRC", "country": "Greece"}
    # An empty cell adds nothing, so the next path in the list is used.
    assert map_item({"geo": "DE"}, extract, tables) == {"country_code": "DEU", "country": "DE"}
    assert map_item({"geo": "XK"}, extract, tables) is None
    keep = extract.model_copy(
        update={"join": [extract.join[0].model_copy(update={"unknown": "keep"})]}
    )
    assert map_item({"geo": "XK", "country_code": "XKX"}, keep, tables)["country_code"] == "XKX"


def test_join_files_are_checked(tmp_path):
    (tmp_path / "sources").mkdir()
    schema = {"name": "obs", "identity": ["code"], "fields": {"code": {"required": True}}}
    (tmp_path / "schema.yaml").write_text(yaml.safe_dump(schema))
    source = {
        "id": "s",
        "schema": "obs",
        "delivery": "csv",
        "url": "https://x.example/a.csv",
        "extract": {
            "mode": "fields",
            "map": {"code": "code"},
            "join": [{"file": "lookups/names.csv", "on": "code"}],
        },
        "rights": {},
    }
    (tmp_path / "sources" / "s.yaml").write_text(yaml.safe_dump(source))
    assert "not found" in load_project(tmp_path).issues[0].message
    (tmp_path / "lookups").mkdir()
    (tmp_path / "lookups" / "names.csv").write_text("id,name\nA,a\n")
    assert "no column 'code'" in load_project(tmp_path).issues[0].message
    (tmp_path / "lookups" / "names.csv").write_text("code,name\nA,a\n")
    assert load_project(tmp_path).ok
