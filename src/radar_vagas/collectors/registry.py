from __future__ import annotations

from collections.abc import Callable

from radar_vagas.collection.contracts import Collector, CollectorMetadata
from radar_vagas.collectors.greenhouse.collector import GreenhouseCollector
from radar_vagas.collectors.gupy.collector import GupyCollector
from radar_vagas.collectors.jobposting.collector import JobPostingCollector
from radar_vagas.collectors.lever.collector import LeverCollector

CollectorFactory = Callable[[], Collector]


COLLECTOR_METADATA: dict[str, CollectorMetadata] = {
    "jobposting": CollectorMetadata(
        slug="jobposting",
        name="JSON-LD JobPosting",
        version="0.3",
        collector_type="single_url",
        supports_complete_snapshot=False,
        requires_board_token=False,
        accepts_single_url=True,
        authentication="publica",
        status="ativo",
        expected_fields=("url",),
        capabilities=("json_ld", "single_page", "no_crawl"),
    ),
    "greenhouse": CollectorMetadata(
        slug="greenhouse",
        name="Greenhouse Job Board",
        version="0.3",
        collector_type="ats_board",
        supports_complete_snapshot=True,
        requires_board_token=True,
        accepts_single_url=False,
        authentication="publica",
        status="ativo",
        expected_fields=("board_token", "company_name"),
        capabilities=("complete_snapshot", "cache_headers"),
    ),
    "lever": CollectorMetadata(
        slug="lever",
        name="Lever Postings API",
        version="0.3",
        collector_type="ats_board",
        supports_complete_snapshot=True,
        requires_board_token=True,
        accepts_single_url=False,
        authentication="publica",
        status="ativo",
        expected_fields=("board_token", "company_name"),
        capabilities=("complete_snapshot", "cache_headers"),
    ),
    "gupy": CollectorMetadata(
        slug="gupy",
        name="Gupy Public Portal",
        version="0.4",
        collector_type="discovery_query",
        supports_complete_snapshot=False,
        requires_board_token=False,
        accepts_single_url=False,
        authentication="publica",
        status="ativo",
        expected_fields=("search_text", "mode"),
        capabilities=("public_portal", "discovery_query", "no_post", "no_auth"),
    ),
}

_FACTORIES: dict[str, CollectorFactory] = {
    "jobposting": JobPostingCollector,
    "greenhouse": GreenhouseCollector,
    "lever": LeverCollector,
    "gupy": GupyCollector,
}


def get_collector(slug: str) -> Collector:
    normalized = slug.strip().lower()
    if normalized not in _FACTORIES:
        allowed = ", ".join(sorted(_FACTORIES))
        raise KeyError(f"Coletor desconhecido: {slug}. Opcoes: {allowed}.")
    return _FACTORIES[normalized]()


def get_collector_metadata(slug: str) -> CollectorMetadata:
    normalized = slug.strip().lower()
    return COLLECTOR_METADATA[normalized]


def list_collectors() -> list[CollectorMetadata]:
    return [COLLECTOR_METADATA[key] for key in sorted(COLLECTOR_METADATA)]
