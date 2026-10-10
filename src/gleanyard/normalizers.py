"""Value normalizers shared by every project schema.

Each normalizer takes a raw value from a reader or extractor and returns a
JSON-ready value, or raises ``NormalizeError`` with a short reason. ``None`` and
blank strings mean "missing" and are handled by the caller, not here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlsplit


class NormalizeError(ValueError):
    """A value could not be normalized to its field type."""


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict):
        return not value
    return False


def clean_text(value: Any) -> str:
    """Strip tags, unescape entities and collapse whitespace to one line."""
    text = re.sub(r"<[^>]+>", " ", str(value))
    return re.sub(r"\s+", " ", unescape(text)).strip()


def text_block(value: Any) -> str:
    """Like ``clean_text`` but keeps paragraph breaks for long text."""
    text = re.sub(r"<\s*(br|/p|/div|/li)\s*/?>", "\n", str(value), flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_DMY = re.compile(r"^(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2}|\d{4})$")


def to_date(value: Any) -> str:
    """ISO date from a date, datetime, ISO string or German ``d.m.yyyy``."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    match = _DMY.match(text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError as error:
            raise NormalizeError(f"invalid date {text!r}") from error
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as error:
        raise NormalizeError(f"unrecognised date {text!r}") from error


def to_datetime(value: Any) -> str:
    """ISO datetime; naive values stay naive rather than inventing a timezone."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        raise NormalizeError("date without a time")
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError as error:
        raise NormalizeError(f"unrecognised datetime {text!r}") from error


def to_time_text(value: Any) -> str:
    """Human time text such as ``19:30`` or ``ab 18 Uhr``, kept as written."""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, date):
        return ""  # an all-day date carries no time
    text = clean_text(value)
    if len(text) > 80:
        raise NormalizeError("time text longer than 80 characters")
    return text


def to_postcode(value: Any, country: str | None = None) -> str:
    text = clean_text(value).upper()
    if country == "DE":
        digits = re.sub(r"\D", "", text)
        if len(digits) == 4 and text.isdigit():
            digits = "0" + digits
        if len(digits) != 5:
            raise NormalizeError(f"not a German postcode: {text!r}")
        return digits
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9 -]{1,9}", text):
        raise NormalizeError(f"not a postcode: {text!r}")
    return text


def to_url(value: Any, base_url: str | None = None) -> str:
    text = clean_text(value)
    url = urljoin(base_url, text) if base_url else text
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise NormalizeError(f"not an absolute http(s) URL: {text!r}")
    return url


_FREE = {"free", "frei", "kostenlos", "kostenfrei", "eintritt frei", "gratis", "0"}
_CURRENCY = {"€": "EUR", "eur": "EUR", "euro": "EUR", "$": "USD", "usd": "USD", "£": "GBP"}


def to_money(value: Any, currency: str | None = None) -> dict[str, str | None]:
    """``{"amount": "12.50", "currency": "EUR"}``; the lowest amount if a range."""
    if isinstance(value, int | float | Decimal) and not isinstance(value, bool):
        return {"amount": str(Decimal(str(value)).quantize(Decimal("0.01"))), "currency": currency}
    text = clean_text(value)
    lowered = text.lower()
    if lowered in _FREE:
        return {"amount": "0.00", "currency": currency}
    found = currency
    for symbol, code in _CURRENCY.items():
        if symbol in lowered:
            found = code
            break
    numbers = re.findall(r"\d+(?:[.,]\d{3})*(?:[.,]\d{1,2})?", text)
    if not numbers:
        raise NormalizeError(f"no amount in {text!r}")
    amounts = [_decimal(number) for number in numbers]
    return {"amount": str(min(amounts).quantize(Decimal("0.01"))), "currency": found}


def _decimal(number: str) -> Decimal:
    """Read ``1.234,50``, ``1,234.50``, ``12,5`` and ``1.000`` the way people write them."""
    decimals = re.search(r"[.,](\d{1,2})$", number)
    whole = number[: decimals.start()] if decimals else number
    whole = whole.replace(".", "").replace(",", "")
    return Decimal(f"{whole}.{decimals.group(1)}" if decimals else whole)


def to_int(value: Any) -> int:
    if isinstance(value, bool):
        raise NormalizeError("boolean is not an integer")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise NormalizeError(f"not an integer: {value!r}") from error
    if number != number.to_integral_value():
        raise NormalizeError(f"not an integer: {value!r}")
    return int(number)


def to_float(value: Any) -> float:
    if isinstance(value, bool):
        raise NormalizeError("boolean is not a number")
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError as error:
        raise NormalizeError(f"not a number: {value!r}") from error
    if number != number or number in {float("inf"), float("-inf")}:
        raise NormalizeError("number must be finite")
    return number


_TRUE = {"true", "yes", "ja", "1", "y"}
_FALSE = {"false", "no", "nein", "0", "n"}


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise NormalizeError(f"not a boolean: {value!r}")


Normalizer = Callable[..., Any]

NORMALIZERS: dict[str, Normalizer] = {
    "string": clean_text,
    "text": text_block,
    "date": to_date,
    "datetime": to_datetime,
    "time_text": to_time_text,
    "postcode": to_postcode,
    "url": to_url,
    "money": to_money,
    "int": to_int,
    "float": to_float,
    "bool": to_bool,
}
