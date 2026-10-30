import json

import pytest
from test_html import make_project, run, site

from gleanyard.extract.explode import explode_item, explode_items
from gleanyard.extract.selectors import SelectorSchema, run_selectors
from gleanyard.project import load_project

GROUPED = b"""<html><body>
<div class="group"><strong>Monthly updates</strong>
  <ul class="series">
    <li><b>Foldables</b><ul><li>Fold5, Fold6</li><li>Flip5</li></ul></li>
    <li><b>S series</b><ul><li>S25, S25+ , </li></ul></li>
  </ul>
</div>
<div class="group"><p>Footnotes, no list</p></div>
</body></html>"""
GROUPED_MAPPING = {
    "baseSelector": "div.group",
    "fields": [
        {"name": "group", "selector": "strong"},
        {
            "name": "series_list",
            "selector": "ul.series > li",
            "type": "nested_list",
            "fields": [
                {"name": "series", "selector": "b"},
                {"name": "models", "selector": "ul > li", "type": "list", "split": r",\s*"},
            ],
        },
    ],
}


def test_split_turns_text_into_a_list():
    schema = SelectorSchema.model_validate(
        {
            "baseSelector": "p",
            "fields": [
                {"name": "tags", "split": r"\s*/\s*"},
                {"name": "none", "selector": "i", "split": ",", "default": ["x"]},
            ],
        }
    )
    assert run_selectors("s", b"<p>a / b /</p>", schema) == [{"tags": ["a", "b"], "none": ["x"]}]


def test_list_field_splits_each_value():
    schema = SelectorSchema.model_validate(GROUPED_MAPPING)
    items = run_selectors("s", GROUPED, schema)
    assert items[0]["series_list"][0] == {
        "series": "Foldables",
        "models": ["Fold5", "Fold6", "Flip5"],
    }
    assert items[0]["series_list"][1]["models"] == ["S25", "S25+"]


@pytest.mark.parametrize(
    "field",
    [
        {"name": "a", "split": ""},
        {"name": "a", "split": "x*"},
        {"name": "a", "type": "nested", "split": ",", "fields": [{"name": "b"}]},
    ],
)
def test_bad_split_is_rejected(field):
    with pytest.raises(ValueError):
        SelectorSchema.model_validate({"baseSelector": "p", "fields": [field]})


def test_explode_walks_nested_lists_and_copies_outer_keys():
    item = {
        "group": "Monthly",
        "series": [
            {"series": "Fold", "models": ["Fold5", "Fold6"]},
            {"series": "S", "models": "S25"},
            {"series": "none"},
            "not an object",
        ],
    }
    assert explode_item(item, "series.models") == [
        {"group": "Monthly", "series": "Fold", "models": "Fold5"},
        {"group": "Monthly", "series": "Fold", "models": "Fold6"},
        {"group": "Monthly", "series": "S", "models": "S25"},
    ]


def test_explode_of_objects_and_missing_paths():
    assert explode_item({"a": 1, "dates": [{"d": "x"}, {"d": "y", "a": 2}]}, "dates") == [
        {"a": 1, "d": "x"},
        {"a": 2, "d": "y"},
    ]
    assert explode_item({"a": 1}, "dates") == []
    assert explode_items([{"a": [1, 2]}, "junk"], "a") == [{"a": 1}, {"a": 2}]
    assert explode_items([{"a": [1]}], None) == [{"a": [1]}]


def test_collect_emits_one_record_per_model(tmp_path):
    mapping = {
        "baseSelector": "div.group",
        "fields": [
            {"name": "venue", "selector": "strong"},
            {
                **GROUPED_MAPPING["fields"][1],
                "fields": [{"name": "title", "selector": "ul > li", "type": "list", "split": ","}],
            },
        ],
    }
    source = {
        "delivery": "html",
        "url": "https://shop.example/devices",
        "extract": {
            "mode": "selectors",
            "selectors": "mappings/m.json",
            "explode": "series_list.title",
            "defaults": {"start_date": "2026-10-20", "source_url": "/devices"},
        },
    }
    root = make_project(tmp_path / "p", source, mapping)
    project, result = run(root, site({"/devices": GROUPED}))
    assert result.ok, result.error
    titles = sorted(record["values"]["title"] for record in _records(project))
    assert titles == ["Flip5", "Fold5", "Fold6", "S25", "S25+"]
    assert {record["values"]["venue"] for record in _records(project)} == {"Monthly updates"}


def _records(project):
    path = project.root / "data" / "records" / "s.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("mode", ["jsonld", "llm"])
def test_explode_only_fits_fields_and_selectors(tmp_path, mode):
    extract = {"mode": mode, "explode": "a"}
    extract |= {"types": ["Event"]} if mode == "jsonld" else {"llm": {"model": "test:m"}}
    root = make_project(
        tmp_path, {"delivery": "html", "url": "https://x.example/", "extract": extract}
    )
    project = load_project(root)
    assert not project.ok
    assert any("explode" in str(issue) for issue in project.issues)
