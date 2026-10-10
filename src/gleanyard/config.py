"""Source and project configuration.

A source file has two independent keys: ``delivery`` says how the data
arrives, ``extract`` says how its fields map to the project schema. The
extraction mode is pinned here; scheduled runs never switch it on their own.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, HttpUrl, model_validator

from gleanyard.rights import Coverage, Retention, SourceRights
from gleanyard.schema import StrictModel

SOURCE_ID = r"^[a-z0-9][a-z0-9-]*$"


class Delivery(StrEnum):
    HTML = "html"
    ICAL = "ical"
    JSON = "json"
    RSS = "rss"
    CSV = "csv"
    PDF = "pdf"
    SITEMAP = "sitemap"


# Which extraction modes make sense for which delivery. Feeds carry named keys,
# so they map fields directly; documents need markup, selectors or a model.
MODES_FOR_DELIVERY: dict[Delivery, frozenset[str]] = {
    Delivery.ICAL: frozenset({"fields"}),
    Delivery.JSON: frozenset({"fields"}),
    Delivery.RSS: frozenset({"fields"}),
    Delivery.CSV: frozenset({"fields"}),
    Delivery.HTML: frozenset({"jsonld", "selectors", "llm"}),
    Delivery.PDF: frozenset({"llm"}),
    Delivery.SITEMAP: frozenset({"jsonld", "selectors", "llm"}),
}


class IcalReader(StrictModel):
    timezone: str = "UTC"
    max_occurrences: int = Field(default=5000, ge=1)
    horizon_days: int = Field(default=366, ge=1, description="Expand recurrences this far ahead.")


class JsonReader(StrictModel):
    items_path: str = Field(default="", description="Dot path to the list of records.")


class CsvReader(StrictModel):
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = "utf-8"


class RssReader(StrictModel):
    pass


class HtmlReader(StrictModel):
    pass


class PdfReader(StrictModel):
    pass


class SitemapReader(StrictModel):
    include: str | None = Field(default=None, description="Regex a page URL must match.")
    max_pages: int = Field(default=50, ge=1, le=1000)


READERS: dict[Delivery, type[StrictModel]] = {
    Delivery.ICAL: IcalReader,
    Delivery.JSON: JsonReader,
    Delivery.CSV: CsvReader,
    Delivery.RSS: RssReader,
    Delivery.HTML: HtmlReader,
    Delivery.PDF: PdfReader,
    Delivery.SITEMAP: SitemapReader,
}


class FetchLimits(StrictModel):
    max_requests: int = Field(default=20, ge=1, le=1000)
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    delay_seconds: float = Field(default=1.0, ge=0)
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class _ExtractBase(StrictModel):
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Constant values for schema fields the source does not carry (venue, city).",
    )


class FieldsExtract(_ExtractBase):
    mode: Literal["fields"]
    map: dict[str, str] = Field(description="Schema field to source key or dot path.")


class JsonLdExtract(_ExtractBase):
    mode: Literal["jsonld"]
    types: list[str] = Field(min_length=1, description="schema.org types to read, e.g. Event.")
    map: dict[str, str] = Field(default_factory=dict, description="Schema field to JSON-LD path.")


class SelectorsExtract(_ExtractBase):
    mode: Literal["selectors"]
    selectors: str = Field(description="Path to a crawl4ai JSON CSS schema, relative to the repo.")


class LlmLimits(StrictModel):
    model: str
    max_pages_per_run: int = Field(default=20, ge=1, le=500)
    max_cost_per_run_eur: float = Field(default=0.5, gt=0, le=50)
    only_if_changed: bool = True


class LlmExtract(_ExtractBase):
    mode: Literal["llm"]
    llm: LlmLimits
    instructions: str | None = Field(default=None, description="Extra guidance for this source.")


Extract = Annotated[
    FieldsExtract | JsonLdExtract | SelectorsExtract | LlmExtract,
    Field(discriminator="mode"),
]


class SourceConfig(StrictModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=SOURCE_ID)
    name: str | None = None
    schema_name: str = Field(alias="schema", description="Name of the project schema.")
    enabled: bool = False
    delivery: Delivery
    url: HttpUrl
    homepage: HttpUrl | None = None
    reader: dict[str, Any] = Field(
        default_factory=dict, description="Options for the delivery reader."
    )
    extract: Extract
    fetch: FetchLimits = Field(default_factory=FetchLimits)
    expect_empty: Literal["never", "seasonal"] = Field(
        default="never", description="seasonal: an empty run is healthy, not a failure."
    )
    coverage: Coverage = Field(default_factory=Coverage)
    rights: SourceRights
    retention: Retention = Field(default_factory=Retention)

    model_config = {"extra": "forbid", "populate_by_name": True}

    @model_validator(mode="after")
    def check_delivery(self):
        allowed = MODES_FOR_DELIVERY[self.delivery]
        if self.extract.mode not in allowed:
            raise ValueError(
                f"extract mode {self.extract.mode!r} does not fit delivery {self.delivery.value!r};"
                f" use one of: {', '.join(sorted(allowed))}"
            )
        self.reader = READERS[self.delivery].model_validate(self.reader).model_dump()
        return self

    @property
    def reader_options(self) -> StrictModel:
        return READERS[self.delivery].model_validate(self.reader)


class StorageConfig(StrictModel):
    root: str = "data"


class ProjectConfig(StrictModel):
    """Optional ``gleanyard.yaml`` at the repo root; every key has a default."""

    schema_version: Literal[1] = 1
    schema_file: str = Field(default="schema.yaml", alias="schema")
    sources: str = "sources"
    user_agent: str = "gleanyard (+https://github.com/kausalflow/gleanyard)"
    storage: StorageConfig = Field(default_factory=StorageConfig)

    model_config = {"extra": "forbid", "populate_by_name": True}
