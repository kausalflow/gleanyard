"""``selectors`` mode: a CSS-selector mapping applied to an HTML page.

The mapping file is a crawl4ai-style JSON CSS schema, so the same file can be drafted by
crawl4ai at setup and then run here with no crawl4ai (and no browser) installed::

    {
      "name": "events",
      "baseSelector": "ul.events > li",
      "baseFields": [{"name": "url", "type": "attribute", "attribute": "data-url"}],
      "fields": [
        {"name": "title", "selector": "h2", "type": "text"},
        {"name": "start_date", "selector": "time", "type": "attribute", "attribute": "datetime"},
        {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"}
      ]
    }

Field types: ``text``, ``attribute``, ``html``, ``regex``, ``nested`` (an object),
``list`` (a list of values) and ``nested_list`` (a list of objects). A field may set
``default``, ``transform`` (``lowercase``, ``uppercase``, ``strip``) and ``split``, a
regular expression that cuts a text value into a list ("A, B, C" with ``",\\s*"``); a
``list`` field splits each of its values. An empty ``selector`` means the matched element
itself.
"""

from __future__ import annotations

import re
from typing import Any, Literal

import lxml.html
from lxml import etree
from pydantic import Field, model_validator

from gleanyard.errors import StructureChanged
from gleanyard.readers import Item
from gleanyard.schema import StrictModel

FieldKind = Literal["text", "attribute", "html", "regex", "nested", "list", "nested_list"]


class SelectorField(StrictModel):
    name: str
    selector: str = ""
    type: FieldKind = "text"
    attribute: str | None = None
    pattern: str | None = None
    default: Any = None
    transform: Literal["lowercase", "uppercase", "strip"] | None = None
    split: str | None = None
    fields: list[SelectorField] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_kind(self):
        if self.type == "attribute" and not self.attribute:
            raise ValueError(f"field {self.name!r}: attribute fields need `attribute`")
        if self.type == "regex":
            if not self.pattern:
                raise ValueError(f"field {self.name!r}: regex fields need `pattern`")
            re.compile(self.pattern)
        if self.split is not None:
            if self.type in {"nested", "nested_list"}:
                raise ValueError(f"field {self.name!r}: {self.type} fields cannot `split`")
            if re.compile(self.split).search(""):
                raise ValueError(f"field {self.name!r}: `split` must not match an empty string")
        if self.type in {"nested", "nested_list"} and not self.fields:
            raise ValueError(f"field {self.name!r}: {self.type} fields need `fields`")
        if self.selector:
            _compile(self.selector, self.name)
        return self


class SelectorSchema(StrictModel):
    name: str | None = None
    baseSelector: str  # noqa: N815 - crawl4ai's key name
    baseFields: list[SelectorField] = Field(default_factory=list)  # noqa: N815
    fields: list[SelectorField] = Field(min_length=1)

    @model_validator(mode="after")
    def check_selectors(self):
        _compile(self.baseSelector, "baseSelector")
        names = [field.name for field in (*self.baseFields, *self.fields)]
        if len(set(names)) != len(names):
            raise ValueError("selector field names must be unique")
        return self

    @property
    def field_names(self) -> list[str]:
        return [field.name for field in (*self.baseFields, *self.fields)]


def _compile(selector: str, where: str) -> None:
    from cssselect import SelectorSyntaxError

    try:
        lxml.html.HtmlElement().cssselect(selector)
    except SelectorSyntaxError as error:
        raise ValueError(f"{where}: invalid CSS selector {selector!r}: {error}") from error


def parse_html(source_id: str, body: bytes) -> lxml.html.HtmlElement:
    if not body.strip():
        raise StructureChanged(source_id, "the page is empty")
    try:
        return lxml.html.document_fromstring(body)
    except (etree.ParserError, etree.XMLSyntaxError) as error:
        raise StructureChanged(source_id, f"the page is not HTML: {error}") from error


def run_selectors(source_id: str, body: bytes, schema: SelectorSchema) -> list[Item]:
    """One item per element matched by ``baseSelector``, keyed by the mapping's field names."""
    root = parse_html(source_id, body)
    return [_item(element, schema) for element in root.cssselect(schema.baseSelector)]


def _item(element: lxml.html.HtmlElement, schema: SelectorSchema) -> Item:
    item: Item = {}
    for field in (*schema.baseFields, *schema.fields):
        value = _value(element, field)
        if value is not None and value != "" and value != []:
            item[field.name] = value
    return item


def _value(element: lxml.html.HtmlElement, field: SelectorField) -> Any:
    if field.type in {"list", "nested_list"}:
        targets = element.cssselect(field.selector) if field.selector else [element]
        if field.type == "list":
            values = [_split(_scalar(target, field), field) for target in targets]
            return [value for part in values for value in part] or field.default
        return [_nested(target, field) for target in targets] or field.default
    target = _first(element, field.selector)
    if target is None:
        return field.default
    if field.type == "nested":
        return _nested(target, field)
    value = _scalar(target, field)
    if field.split is not None:
        return _split(value, field) or field.default
    return field.default if value in (None, "") else value


def _split(value: str | None, field: SelectorField) -> list[str]:
    if value in (None, ""):
        return []
    if field.split is None:
        return [value]
    parts = (part.strip() for part in re.split(field.split, value))
    return [part for part in parts if part]


def _first(element: lxml.html.HtmlElement, selector: str) -> lxml.html.HtmlElement | None:
    if not selector:
        return element
    matches = element.cssselect(selector)
    return matches[0] if matches else None


def _nested(element: lxml.html.HtmlElement, field: SelectorField) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for child in field.fields:
        value = _value(element, child)
        if value is not None and value != "" and value != []:
            nested[child.name] = value
    return nested


def _scalar(element: lxml.html.HtmlElement, field: SelectorField) -> str | None:
    if field.type == "attribute":
        value = element.get(field.attribute or "")
    elif field.type == "html":
        value = "".join(
            [element.text or "", *(etree.tostring(c, encoding="unicode") for c in element)]
        )
    elif field.type == "regex":
        match = re.search(field.pattern or "", " ".join(element.text_content().split()))
        value = (match.group(1) if match.groups() else match.group(0)) if match else None
    else:
        value = " ".join(element.text_content().split())
    if value is None:
        return None
    if field.transform == "lowercase":
        value = value.lower()
    elif field.transform == "uppercase":
        value = value.upper()
    elif field.transform == "strip":
        value = value.strip()
    return value


SelectorField.model_rebuild()
