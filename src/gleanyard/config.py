"""Source and project configuration.

A source file has two independent keys: ``delivery`` says how the data
arrives, ``extract`` says how its fields map to the project schema. The
extraction mode is pinned here; scheduled runs never switch it on their own.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, HttpUrl, field_validator, model_validator

from gleanyard.rights import Coverage, Retention, RightsPolicy, SourceRights
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
    past_days: int = Field(default=7, ge=0, description="Keep recurrences this far back.")
    horizon_days: int = Field(default=366, ge=1, description="Expand recurrences this far ahead.")


class JsonReader(StrictModel):
    items_path: str = Field(default="", description="Dot path to the list of records.")


class CsvReader(StrictModel):
    delimiter: str = Field(default=",", min_length=1, max_length=1)
    encoding: str = "utf-8"


class RssReader(StrictModel):
    include: str | None = Field(
        default=None,
        description="Regex an item's title or text must match; other items are skipped.",
    )


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


class SourceFilter(StrictModel):
    """What the source's own query filter is meant to leave in, checked on the result.

    A URL like ``?city=aachen`` can silently stop working and return everything. The
    check reads the filtered schema field on every extracted record.
    """

    field: str = Field(description="Schema field the URL filter constrains, e.g. city.")
    one_of: list[str | int | float | bool] = Field(
        min_length=1, description="Values that count as inside the filter (strings ignore case)."
    )
    max_outside_share: float = Field(
        default=0.2,
        ge=0,
        le=1,
        description="Fail with filter_ignored when more of the records fall outside; "
        "fewer are moved to the review file.",
    )


class Pagination(StrictModel):
    """Walk a source that serves its records in pages, via one query parameter."""

    param: str = Field(description="Query parameter that selects the page, e.g. page or offset.")
    start: int = Field(default=1, ge=0, description="Value of the parameter on the first page.")
    step: int = Field(default=1, ge=1, description="1 for page numbers, the page size for offsets.")
    max_pages: int = Field(
        default=10,
        ge=2,
        le=200,
        description="Pages to fetch; a source still returning new records after this fails.",
    )


PAGED_DELIVERIES = frozenset({Delivery.HTML, Delivery.JSON, Delivery.RSS, Delivery.CSV})


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
    input_price_per_mtok_eur: float = Field(
        default=5.0, ge=0, description="Used to estimate cost against max_cost_per_run_eur."
    )
    output_price_per_mtok_eur: float = Field(default=25.0, ge=0)
    max_input_chars: int = Field(
        default=60_000, ge=1000, description="Page text beyond this is not sent."
    )


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
    filter: SourceFilter | None = None
    pagination: Pagination | None = None
    fetch: FetchLimits = Field(default_factory=FetchLimits)
    expect_empty: Literal["never", "seasonal"] = Field(
        default="never", description="seasonal: an empty run is healthy, not a failure."
    )
    expect_change_days: int | None = Field(
        default=None,
        ge=1,
        description="Warn when the records have not changed for this many days (off by default).",
    )
    coverage: Coverage = Field(default_factory=Coverage)
    rights: SourceRights
    evidence: str | None = Field(
        default=None,
        pattern=SOURCE_ID,
        description=(
            "Folder under evidence/ that holds this source's terms review; default: the source "
            "id. Sources that share one site and one review name the same folder."
        ),
    )
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
        if self.pagination is not None and self.delivery not in PAGED_DELIVERIES:
            allowed = ", ".join(sorted(d.value for d in PAGED_DELIVERIES))
            raise ValueError(
                f"pagination does not fit delivery {self.delivery.value!r}; use one of: {allowed}"
            )
        if self.pagination is not None and self.pagination.max_pages + 1 > self.fetch.max_requests:
            raise ValueError(
                f"pagination.max_pages {self.pagination.max_pages} needs fetch.max_requests of "
                f"at least {self.pagination.max_pages + 1} (robots.txt counts as a request)"
            )
        self.reader = READERS[self.delivery].model_validate(self.reader).model_dump()
        return self

    @property
    def reader_options(self) -> StrictModel:
        return READERS[self.delivery].model_validate(self.reader)


class StorageConfig(StrictModel):
    root: str = "data"
    changes_days: int = Field(
        default=90, ge=1, description="Days of change logs (changes/<date>.jsonl) to keep."
    )


_ISO_DURATION = re.compile(
    r"P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+(\.\d+)?S)?)?"
)


class DataherbConfig(StrictModel):
    """Describe the project for a DataHerb catalog (https://dataherb.github.io)."""

    id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="Dataset and job id in the catalog; defaults to the project folder name.",
    )
    name: str | None = Field(default=None, description="Title; defaults to the schema name.")
    description: str | None = Field(default=None, description="Defaults to the schema description.")
    tags: list[str] = Field(default_factory=list)
    owner: str | None = Field(default=None, description="Who to ask about the data.")
    license: str | None = Field(
        default=None,
        description="Licence of the export; defaults to the one licence its sources share.",
    )
    uri: str | None = Field(
        default=None,
        description="Repo URL; defaults to the GitHub repository when run in GitHub Actions.",
    )
    metadata: str = Field(default="dataherb.json", description="Dataset metadata path.")
    status_dir: str = Field(
        default="_dataherb/status",
        description="Status prefix; the job status is written to <status_dir>/<id>/latest.json.",
    )
    expected_interval: str = Field(
        default="P1D",
        description="ISO 8601 duration between scheduled runs; the catalog flags staleness.",
    )
    max_duration: str | None = Field(
        default=None,
        description="ISO 8601 duration after which a running job counts as stuck.",
    )

    @field_validator("expected_interval", "max_duration")
    @classmethod
    def check_duration(cls, value: str | None) -> str | None:
        if value is not None and not _ISO_DURATION.fullmatch(value):
            raise ValueError(f"{value!r} is not an ISO 8601 duration such as P1D or PT6H")
        return value


class ProjectConfig(StrictModel):
    """Optional ``gleanyard.yaml`` at the repo root; every key has a default."""

    schema_version: Literal[1] = 1
    schema_file: str = Field(default="schema.yaml", alias="schema")
    sources: str = "sources"
    user_agent: str = "gleanyard (+https://github.com/kausalflow/gleanyard)"
    storage: StorageConfig = Field(default_factory=StorageConfig)
    rights_policy: RightsPolicy = Field(default_factory=RightsPolicy)
    export: str = Field(default="exports/records.jsonl", description="Public export path.")
    changes_feed: str | None = Field(
        default="exports/changes.json",
        description="JSON Feed of recent record changes, written on export; null turns it off.",
    )
    excluded_hosts: list[str] = Field(
        default_factory=list, description="Hosts research must not propose (checked by `check`)."
    )
    status: str = Field(
        default="status.json", description="Health report path, read by the status hub."
    )
    dataherb: DataherbConfig | None = Field(
        default=None,
        description="Also write DataHerb dataset metadata on export and a job status on collect.",
    )

    model_config = {"extra": "forbid", "populate_by_name": True}
