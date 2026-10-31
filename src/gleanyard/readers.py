"""Delivery readers: turn a fetched body into items or documents.

Feed deliveries (ical, json, rss, csv) produce *items*: flat dicts keyed the
way the feed names things (``SUMMARY``, ``title``, a CSV header), which
``fields`` mode maps onto schema fields. A sitemap produces the page URLs that
document modes (``jsonld``, ``selectors``, ``llm``) then fetch. A reader
never guesses: a body that does not have the expected shape raises
``StructureChanged`` so the source goes to repair instead of producing nothing.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gleanyard.config import (
    CsvReader,
    Delivery,
    IcalReader,
    JsonReader,
    RssReader,
    SitemapReader,
    SourceConfig,
    Unpivot,
)
from gleanyard.errors import BoundsExceeded, StructureChanged

Item = dict[str, Any]


def lookup(item: Any, path: str) -> Any:
    """Follow a dot path (``location.name``, ``offers.0.price``); missing is None.

    A key that itself contains dots (``dc.date``) is tried whole first. ``*`` reads the rest
    of the path in every element of a list (``resources.*.format``) and returns the list.
    """
    if isinstance(item, dict) and path in item:
        return item[path]
    return _follow(item, path.split("."))


def _follow(current: Any, parts: list[str]) -> Any:
    for index, part in enumerate(parts):
        if part == "*" and isinstance(current, list):
            values = [_follow(element, parts[index + 1 :]) for element in current]
            return [value for value in values if value is not None] or None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
        if current is None:
            return None
    return current


# JSON


def read_json(source: SourceConfig, body: bytes, options: JsonReader) -> list[Item]:
    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError) as error:
        raise StructureChanged(source.id, f"response is not valid JSON: {error}") from error
    items = lookup(document, options.items_path) if options.items_path else document
    if not isinstance(items, list):
        where = f"at {options.items_path!r}" if options.items_path else "at the top level"
        raise StructureChanged(source.id, f"expected a list of records {where}")
    if any(not isinstance(item, dict) for item in items):
        raise StructureChanged(source.id, "every record in the JSON list must be an object")
    return items


# CSV


def read_csv(source: SourceConfig, body: bytes, options: CsvReader) -> list[Item]:
    try:
        text = body.decode(options.encoding).lstrip("﻿")
    except UnicodeDecodeError as error:
        raise StructureChanged(source.id, f"CSV is not {options.encoding}") from error
    reader = csv.DictReader(io.StringIO(text), delimiter=options.delimiter)
    if not reader.fieldnames:
        raise StructureChanged(source.id, "CSV has no header row")
    rows = []
    for row in reader:
        if None in row:
            raise StructureChanged(source.id, f"CSV row {reader.line_num} has extra columns")
        rows.append({key.strip(): value for key, value in row.items()})
    return rows


def unpivot(source: SourceConfig, items: list[Item], options: Unpivot | None) -> list[Item]:
    """One item per listed column of each wide item; an empty cell gives no item."""
    if options is None:
        return items
    if items and not any(column in items[0] for column in options.columns):
        raise StructureChanged(source.id, "none of the unpivot columns is in the data")
    long = []
    for item in items:
        kept = {key: item[key] for key in options.keep if key in item}
        for column, label in options.columns.items():
            value = item.get(column)
            if value not in (None, ""):
                long.append({**kept, options.name: label, options.value: value})
    return long


# RSS and Atom

_ATOM = "{http://www.w3.org/2005/Atom}"


def read_rss(source: SourceConfig, body: bytes, options: RssReader | None = None) -> list[Item]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise StructureChanged(source.id, f"feed is not valid XML: {error}") from error
    if root.tag == "rss" or root.tag.endswith("RDF"):
        entries = root.iter("item") if root.tag == "rss" else root.iter("{*}item")
        items = [_rss_item(entry) for entry in entries]
    elif root.tag == f"{_ATOM}feed":
        items = [_atom_entry(entry) for entry in root.iter(f"{_ATOM}entry")]
    else:
        raise StructureChanged(source.id, f"expected an RSS or Atom feed, found <{root.tag}>")
    if options and options.include:
        # A general feed (all of a police station's releases) narrowed to the topic: items that
        # do not match are skipped, not rejected, so they never reach review or the raw records.
        pattern = re.compile(options.include)
        items = [
            item
            for item in items
            if any(pattern.search(str(item.get(key) or "")) for key in _RSS_TEXT)
        ]
    return items


# The item keys ``include`` looks at: the title and the text, as RSS and Atom name them.
_RSS_TEXT = ("title", "description", "summary", "content", "encoded")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _rss_item(entry: ET.Element) -> Item:
    item: Item = {}
    for child in entry:
        name = _local(child.tag)
        value = (child.text or "").strip()
        if name == "category":
            item.setdefault("category", []).append(value)
        elif name == "enclosure":
            item["enclosure"] = child.get("url")
        elif name not in item:
            item[name] = value
    return item


def _atom_entry(entry: ET.Element) -> Item:
    item: Item = {}
    for child in entry:
        name = _local(child.tag)
        if name == "link":
            if child.get("rel", "alternate") == "alternate":
                item.setdefault("link", child.get("href"))
        elif name == "category":
            item.setdefault("category", []).append(child.get("term"))
        elif name == "author":
            item["author"] = (child.findtext(f"{_ATOM}name") or "").strip()
        elif name not in item:
            item[name] = (child.text or "").strip()
    return item


# iCalendar


def read_ical(source: SourceConfig, body: bytes, options: IcalReader, today: date) -> list[Item]:
    """One item per occurrence, keyed by iCalendar property names.

    Datetimes are converted to the reader's timezone; all-day ``DTEND`` is made
    inclusive (the last day of the event). ``TIME`` holds ``19:30`` or
    ``19:30–21:00`` for timed events. Recurring series are expanded from
    ``past_days`` ago to ``horizon_days`` ahead and never silently truncated.
    """
    import recurring_ical_events
    from icalendar import Calendar

    try:
        zone = ZoneInfo(options.timezone)
    except ZoneInfoNotFoundError as error:
        raise StructureChanged(source.id, f"unknown timezone {options.timezone!r}") from error
    try:
        calendar = Calendar.from_ical(body)
    except ValueError as error:
        raise StructureChanged(source.id, f"not a valid iCalendar file: {error}") from error
    if calendar.name != "VCALENDAR":
        raise StructureChanged(source.id, "expected a VCALENDAR")
    cancelled = str(calendar.get("METHOD", "")).upper() == "CANCEL"
    families: dict[str, list[Any]] = defaultdict(list)
    for index, component in enumerate(calendar.walk("VEVENT")):
        families[str(component.get("UID", f"missing-uid-{index}"))].append(component)

    lower = today - timedelta(days=options.past_days)
    upper = today + timedelta(days=options.horizon_days)
    items: list[Item] = []
    for family in families.values():
        recurring = any(
            key in component for component in family for key in ("RRULE", "RDATE", "RECURRENCE-ID")
        )
        if not recurring:
            # Repeated components with one UID are revisions; keep the newest.
            latest = max(
                family,
                key=lambda c: (int(c.get("SEQUENCE", 0)), str(c.get("DTSTAMP", ""))),
            )
            items.append(_ical_item(latest, zone, cancelled))
        else:
            series = Calendar()
            for component in family:
                series.add_component(component)
            for occurrence in recurring_ical_events.of(series).after(lower):
                item = _ical_item(occurrence, zone, cancelled)
                if _day(item["DTSTART"]) >= upper:
                    break
                items.append(item)
                if len(items) > options.max_occurrences:
                    break
        if len(items) > options.max_occurrences:
            raise BoundsExceeded(
                source.id, f"calendar has more than {options.max_occurrences} occurrences"
            )
    return items


def _day(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _ical_value(component: Any, name: str, zone: ZoneInfo) -> date | datetime | None:
    value = component.get(name)
    decoded = getattr(value, "dt", None)
    if isinstance(decoded, datetime) and decoded.tzinfo is not None:
        return decoded.astimezone(zone)
    return decoded if isinstance(decoded, date) else None


def _ical_item(component: Any, zone: ZoneInfo, cancelled: bool) -> Item:
    start = _ical_value(component, "DTSTART", zone)
    end = _ical_value(component, "DTEND", zone)
    duration = getattr(component.get("DURATION"), "dt", None)
    if end is None and start is not None and isinstance(duration, timedelta):
        end = start + duration
    all_day = start is not None and not isinstance(start, datetime)
    if all_day and isinstance(end, date) and not isinstance(end, datetime):
        end = end - timedelta(days=1) if end > start else start
    time = None
    if isinstance(start, datetime):
        time = start.strftime("%H:%M")
        if isinstance(end, datetime) and end > start:
            time += "–" + end.strftime("%H:%M")
    categories = component.get("CATEGORIES", [])
    if not isinstance(categories, list):
        categories = [categories]
    geo = component.get("GEO")
    recurrence_id = _ical_value(component, "RECURRENCE-ID", zone)
    status = "CANCELLED" if cancelled else str(component.get("STATUS", "")).upper()
    return {
        "UID": str(component.get("UID", "")),
        "SUMMARY": str(component.get("SUMMARY", "")),
        "DESCRIPTION": str(component.get("DESCRIPTION", "")),
        "LOCATION": str(component.get("LOCATION", "")),
        "URL": str(component.get("URL", "")),
        "STATUS": status,
        "CATEGORIES": [str(c) for value in categories for c in getattr(value, "cats", [])],
        "DTSTART": start,
        "DTEND": end,
        "ALL_DAY": all_day,
        "TIME": time,
        "RECURRENCE-ID": recurrence_id.isoformat() if recurrence_id else None,
        "GEO_LAT": float(geo.latitude) if geo is not None else None,
        "GEO_LON": float(geo.longitude) if geo is not None else None,
    }


# Sitemap


_SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def read_sitemap(source: SourceConfig, body: bytes, options: SitemapReader) -> list[str]:
    """Page URLs listed in a sitemap, filtered by ``include`` and capped by ``max_pages``."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as error:
        raise StructureChanged(source.id, f"sitemap is not valid XML: {error}") from error
    if _local(root.tag) not in {"urlset", "sitemapindex"}:
        raise StructureChanged(source.id, f"expected a sitemap, found <{root.tag}>")
    urls = [(loc.text or "").strip() for loc in root.iter(f"{_SITEMAP}loc")]
    if options.include:
        pattern = re.compile(options.include)
        urls = [url for url in urls if pattern.search(url)]
    if len(urls) > options.max_pages:
        raise BoundsExceeded(
            source.id, f"sitemap lists {len(urls)} pages, more than max_pages {options.max_pages}"
        )
    return urls


def read_items(source: SourceConfig, body: bytes, today: date | None = None) -> list[Item]:
    """Items from a feed delivery; raises for document deliveries."""
    options = source.reader_options
    if source.delivery == Delivery.JSON:
        return unpivot(source, read_json(source, body, options), options.unpivot)
    if source.delivery == Delivery.CSV:
        return unpivot(source, read_csv(source, body, options), options.unpivot)
    if source.delivery == Delivery.RSS:
        return read_rss(source, body, options)
    if source.delivery == Delivery.ICAL:
        return read_ical(source, body, options, today or date.today())
    raise ValueError(f"{source.delivery.value} is a document delivery, not a feed")
