from datetime import date, datetime
from pathlib import Path

import pytest

from gleanyard.config import SourceConfig
from gleanyard.errors import BoundsExceeded, StructureChanged
from gleanyard.readers import lookup, read_items, read_sitemap

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 10)


def source(delivery: str, reader: dict | None = None) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "id": "demo",
            "schema": "event",
            "delivery": delivery,
            "url": "https://example.org/feed",
            "reader": reader or {},
            "extract": (
                {"mode": "fields", "map": {"title": "t"}}
                if delivery not in {"html", "sitemap"}
                else {"mode": "jsonld", "types": ["Event"]}
            ),
            "rights": {},
        }
    )


def test_lookup_paths():
    item = {"a": {"b": [{"c": 1}]}, "dc.date": "x"}
    assert lookup(item, "a.b.0.c") == 1
    assert lookup(item, "dc.date") == "x"
    assert lookup(item, "a.missing.c") is None
    assert lookup(item, "a.b.5") is None


def test_json_items_path():
    body = b'{"data": {"events": [{"name": "A"}, {"name": "B"}]}}'
    items = read_items(source("json", {"items_path": "data.events"}), body)
    assert [item["name"] for item in items] == ["A", "B"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<html>", "not valid JSON"),
        (b'{"data": {}}', "expected a list of records at 'data.events'"),
        (b'{"data": {"events": [1]}}', "must be an object"),
    ],
)
def test_json_structure_changes(body, message):
    with pytest.raises(StructureChanged, match=message):
        read_items(source("json", {"items_path": "data.events"}), body)


def test_csv_with_bom_and_delimiter():
    body = "﻿title;date\nA;2026-10-12\nB;2026-10-13\n".encode()
    items = read_items(source("csv", {"delimiter": ";"}), body)
    assert items == [{"title": "A", "date": "2026-10-12"}, {"title": "B", "date": "2026-10-13"}]


def test_csv_extra_columns_fail():
    with pytest.raises(StructureChanged, match="extra columns"):
        read_items(source("csv"), b"a,b\n1,2,3\n")


def test_rss_items():
    body = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>A</title><link>https://example.org/a</link><category>x</category>
<category>y</category><dc:date>2026-10-12</dc:date></item>
</channel></rss>"""
    assert read_items(source("rss"), body) == [
        {
            "title": "A",
            "link": "https://example.org/a",
            "category": ["x", "y"],
            "date": "2026-10-12",
        }
    ]


def test_atom_entries():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A</title>
<link rel="alternate" href="https://example.org/a"/><updated>2026-10-12T10:00:00Z</updated>
<category term="music"/></entry></feed>"""
    assert read_items(source("rss"), body) == [
        {
            "title": "A",
            "link": "https://example.org/a",
            "updated": "2026-10-12T10:00:00Z",
            "category": ["music"],
        }
    ]


def test_rss_include_keeps_matching_items():
    body = """<rss version="2.0"><channel>
<item><title>Einbruch in Wohnung</title><description>Unbekannte brachen ein.</description></item>
<item><title>Großeinsatz am Samstag</title><description>Polizei bereit.</description></item>
<item><title>Hinweis für Samstag</title><description>Eine Versammlung.</description></item>
</channel></rss>""".encode()
    items = read_items(source("rss", {"include": "(?i)versammlung|großeinsatz"}), body)
    assert [item["title"] for item in items] == ["Großeinsatz am Samstag", "Hinweis für Samstag"]


def test_atom_include_reads_summary():
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>A</title><summary>road closed</summary></entry>
<entry><title>B</title><summary>open</summary></entry></feed>"""
    assert [i["title"] for i in read_items(source("rss", {"include": "closed"}), body)] == ["A"]


def test_rss_wrong_root():
    with pytest.raises(StructureChanged, match="expected an RSS or Atom feed"):
        read_items(source("rss"), b"<html><body/></html>")


def ical_items(**reader):
    body = (FIXTURES / "calendar.ics").read_bytes()
    src = source("ical", {"timezone": "Europe/Berlin", **reader})
    return read_items(src, body, today=TODAY)


def test_ical_keeps_newest_revision_and_converts_timezones():
    items = {item["UID"]: item for item in ical_items() if item["UID"] != "weekly-1"}
    concert = items["concert-1"]
    assert concert["SUMMARY"] == "Jazz im Park"
    assert concert["CATEGORIES"] == ["music", "outdoor"]
    assert concert["TIME"] == "19:30–21:30"
    assert concert["DTSTART"] == datetime.fromisoformat("2026-10-12T19:30:00+02:00")

    fair = items["fair-1"]
    assert fair["ALL_DAY"] is True
    assert (fair["DTSTART"], fair["DTEND"]) == (date(2026, 10, 17), date(2026, 10, 18))

    # 22:30 UTC is already the next day in Berlin.
    assert items["utc-1"]["DTSTART"].date() == date(2026, 10, 21)
    assert items["utc-1"]["TIME"] == "00:30"


def test_ical_expands_recurrence_within_window():
    starts = [
        item["DTSTART"].date() for item in ical_items(horizon_days=28) if item["UID"] == "weekly-1"
    ]
    assert starts[0] == date(2026, 10, 6)  # within past_days=7
    assert starts[-1] == date(2026, 11, 3)
    assert len(starts) == 5


def test_ical_occurrence_limit():
    with pytest.raises(BoundsExceeded, match="more than 3 occurrences"):
        ical_items(max_occurrences=3)


def test_ical_rejects_other_content():
    with pytest.raises(StructureChanged):
        read_items(source("ical"), b"<html></html>", today=TODAY)


def test_sitemap_filter_and_cap():
    body = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.org/events/1</loc></url>
<url><loc>https://example.org/about</loc></url>
<url><loc>https://example.org/events/2</loc></url></urlset>"""
    src = source("sitemap", {"include": "/events/"})
    assert read_sitemap(src, body, src.reader_options) == [
        "https://example.org/events/1",
        "https://example.org/events/2",
    ]
    capped = source("sitemap", {"max_pages": 1})
    with pytest.raises(BoundsExceeded):
        read_sitemap(capped, body, capped.reader_options)


def test_lookup_star_reads_every_list_element():
    item = {"resources": [{"format": "CSV"}, {"other": 1}, {"format": "JSON"}], "none": []}
    assert lookup(item, "resources.*.format") == ["CSV", "JSON"]
    assert lookup(item, "resources.*.missing") is None
    assert lookup(item, "none.*.x") is None
    assert lookup({"a": {"b": 1}}, "a.*.b") is None  # not a list


WIDE = b"surveyDate,gacdisa,nocdisa,other\n2026-09-01,-8.7,ND,1\n2026-10-01,10.7,,2\n"


def test_csv_unpivot_gives_one_item_per_listed_column():
    reader = {
        "unpivot": {
            "keep": ["surveyDate"],
            "columns": {"gacdisa": "business_conditions", "nocdisa": "new_orders"},
            "name": "indicator",
        }
    }
    assert read_items(source("csv", reader), WIDE) == [
        {"surveyDate": "2026-09-01", "indicator": "business_conditions", "value": "-8.7"},
        {"surveyDate": "2026-09-01", "indicator": "new_orders", "value": "ND"},
        {"surveyDate": "2026-10-01", "indicator": "business_conditions", "value": "10.7"},
    ]


def test_json_unpivot_and_missing_columns():
    reader = {"unpivot": {"columns": {"a": "A"}, "keep": ["d"]}}
    body = b'[{"d": 1, "a": 5}, {"d": 2}]'
    assert read_items(source("json", reader), body) == [{"d": 1, "name": "A", "value": 5}]
    with pytest.raises(StructureChanged, match="unpivot"):
        read_items(source("json", reader), b'[{"d": 1, "b": 5}]')
