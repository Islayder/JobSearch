from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from radar_vagas.config.schemas import CollectionConfig
from radar_vagas.domain.errors import RadarError
from radar_vagas.http.client import HttpClient
from radar_vagas.ingestion.import_schema import ImportedPosting

CollectedPosting = ImportedPosting


class CollectorError(RadarError):
    """Base error raised by collectors."""


@dataclass(frozen=True)
class CollectionContext:
    collector: str
    source_name: str
    source_type: str
    company_name: str | None = None
    board_key: str | None = None
    board_token: str | None = None
    url: str | None = None
    dry_run: bool = False
    max_items: int | None = None
    since: datetime | None = None
    http_client: HttpClient | None = None
    collection_config: CollectionConfig = field(default_factory=CollectionConfig)
    cache_etag: str | None = None
    cache_last_modified: str | None = None
    include_all: bool = False
    selected_index: int | None = None


class Collector(Protocol):
    slug: str

    def collect(self, context: CollectionContext) -> CollectionResult:
        """Collect public postings without persisting them."""


@dataclass(frozen=True)
class CollectorMetadata:
    slug: str
    name: str
    version: str
    collector_type: str
    supports_complete_snapshot: bool
    requires_board_token: bool
    accepts_single_url: bool
    authentication: str
    status: str
    expected_fields: tuple[str, ...]
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class CollectionResult:
    collector: str
    items: Sequence[CollectedPosting]
    requests: int
    bytes_received: int
    warnings: list[str] = field(default_factory=list)
    recoverable_errors: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    complete_snapshot: bool = False
    partial: bool = False
    not_modified: bool = False
    status_code: int | None = None
    cache_etag: str | None = None
    cache_last_modified: str | None = None

    @property
    def found(self) -> int:
        return len(self.items)
