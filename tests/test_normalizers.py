from datetime import date, datetime

import pytest

from gleanyard.normalizers import (
    NormalizeError,
    clean_text,
    text_block,
    to_bool,
    to_date,
    to_datetime,
    to_money,
    to_postcode,
    to_time_text,
    to_url,
)


def test_clean_text_strips_tags_and_entities():
    assert clean_text("<b>Konzert</b>&nbsp; im\n Park") == "Konzert im Park"


def test_text_block_keeps_paragraphs():
    assert text_block("<p>One</p><p>Two<br>three</p>") == "One\nTwo\nthree"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-12", "2026-10-12"),
        ("2026-10-12T19:30:00+02:00", "2026-10-12"),
        ("12.10.2026", "2026-10-12"),
        ("1.2.26", "2026-02-01"),
        (date(2026, 1, 3), "2026-01-03"),
        (datetime(2026, 1, 3, 9, 0), "2026-01-03"),
    ],
)
def test_to_date(raw, expected):
    assert to_date(raw) == expected


@pytest.mark.parametrize("raw", ["31.02.2026", "next Friday", "2026/10/12"])
def test_to_date_rejects(raw):
    with pytest.raises(NormalizeError):
        to_date(raw)


def test_to_datetime_keeps_offset_and_naive():
    assert to_datetime("2026-10-12T19:30:00Z") == "2026-10-12T19:30:00+00:00"
    assert to_datetime("2026-10-12 19:30") == "2026-10-12T19:30:00"
    with pytest.raises(NormalizeError):
        to_datetime(date(2026, 10, 12))


def test_to_time_text():
    assert to_time_text(" ab 18&nbsp;Uhr ") == "ab 18 Uhr"
    assert to_time_text(datetime(2026, 1, 1, 9, 5)) == "09:05"


def test_to_postcode():
    assert to_postcode("D-50667", "DE") == "50667"
    assert to_postcode("1067", "DE") == "01067"
    assert to_postcode("sw1a 1aa") == "SW1A 1AA"
    with pytest.raises(NormalizeError):
        to_postcode("506", "DE")


def test_to_url_resolves_relative_and_rejects_other_schemes():
    assert to_url("/termine/1", "https://example.org/a/") == "https://example.org/termine/1"
    with pytest.raises(NormalizeError):
        to_url("javascript:alert(1)")
    with pytest.raises(NormalizeError):
        to_url("/relative-without-base")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,50 €", {"amount": "12.50", "currency": "EUR"}),
        ("€ 1.234,5", {"amount": "1234.50", "currency": "EUR"}),
        ("$1,234.50", {"amount": "1234.50", "currency": "USD"}),
        ("10 - 15 EUR", {"amount": "10.00", "currency": "EUR"}),
        ("Eintritt frei", {"amount": "0.00", "currency": None}),
        (8, {"amount": "8.00", "currency": None}),
    ],
)
def test_to_money(raw, expected):
    assert to_money(raw) == expected


def test_to_money_default_currency_and_failure():
    assert to_money("5", "EUR") == {"amount": "5.00", "currency": "EUR"}
    with pytest.raises(NormalizeError):
        to_money("on request")


def test_to_bool():
    assert to_bool("ja") is True
    assert to_bool("No") is False
    with pytest.raises(NormalizeError):
        to_bool("maybe")


def test_rfc2822_dates_from_rss_and_http():
    assert to_datetime("Tue, 01 Sep 2026 10:00:00 GMT") == "2026-09-01T10:00:00+00:00"
    assert to_datetime("Mon, 17 Aug 2026 09:15:00 +0200") == "2026-08-17T09:15:00+02:00"
    assert to_date("Mon, 17 Aug 2026 09:15:00 +0200") == "2026-08-17"
    with pytest.raises(NormalizeError):
        to_datetime("Someday, 99 Foo 2026")
