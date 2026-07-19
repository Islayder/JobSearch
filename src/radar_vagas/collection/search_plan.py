from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, replace

from sqlalchemy.orm import Session

from radar_vagas.collection.orchestrator import (
    build_collection_context,
    record_failed_collection,
    run_collection_persistence,
)
from radar_vagas.collection.result import CollectionExecutionReport
from radar_vagas.collectors.registry import get_collector
from radar_vagas.config.loaders import load_network_config, load_search_queries
from radar_vagas.config.schemas import NetworkConfig, SearchQueryConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import CollectionAuthority
from radar_vagas.domain.errors import RadarError
from radar_vagas.http.client import HttpClient, HttpRequestBudget
from radar_vagas.persistence.database import session_scope


@dataclass(frozen=True)
class SearchPlanBudget:
    max_total_requests: int
    max_total_items: int
    max_duration_seconds: int


@dataclass
class SearchPlanBudgetState:
    budget: SearchPlanBudget
    request_budget: HttpRequestBudget | None = None
    started_at: float = 0.0
    items_used: int = 0
    exhausted_by: str | None = None

    def __post_init__(self) -> None:
        if self.started_at == 0.0:
            self.started_at = time.monotonic()
        if self.request_budget is None:
            self.request_budget = HttpRequestBudget(
                max_requests=self.budget.max_total_requests,
                max_duration_seconds=self.budget.max_duration_seconds,
            )

    @property
    def exhausted(self) -> bool:
        return self.exhausted_by is not None

    def should_stop_before_query(self) -> bool:
        if self.requests_used >= self.budget.max_total_requests:
            self.exhausted_by = "max_total_requests"
            return True
        if self.items_used >= self.budget.max_total_items:
            self.exhausted_by = "max_total_items"
            return True
        if self.elapsed_seconds >= self.budget.max_duration_seconds:
            self.exhausted_by = "max_duration_seconds"
            return True
        return False

    @property
    def elapsed_seconds(self) -> float:
        if self.request_budget is not None:
            return self.request_budget.elapsed_seconds
        return time.monotonic() - self.started_at

    @property
    def requests_used(self) -> int:
        if self.request_budget is not None:
            return self.request_budget.requests_used
        return 0

    def max_pages_for_query(self, requested: int) -> int:
        return requested

    def max_items_for_query(self, requested: int) -> int:
        remaining_items = self.budget.max_total_items - self.items_used
        return min(requested, remaining_items)

    def record_execution(
        self,
        _query: SearchQueryConfig,
        execution: CollectionExecutionReport,
    ) -> CollectionExecutionReport:
        self.items_used += execution.summary.found
        limited_by = execution.metadata.get("budget_limited_by")
        if isinstance(limited_by, str) and limited_by:
            self.exhausted_by = limited_by
            return execution
        if self.requests_used >= self.budget.max_total_requests:
            self.exhausted_by = "max_total_requests"
        elif self.items_used >= self.budget.max_total_items:
            self.exhausted_by = "max_total_items"
        elif self.elapsed_seconds >= self.budget.max_duration_seconds:
            self.exhausted_by = "max_duration_seconds"
        return execution


@dataclass(frozen=True)
class SearchPlanRunResult:
    executions: list[tuple[SearchQueryConfig, CollectionExecutionReport]]
    errors: list[str]
    budget_state: SearchPlanBudgetState


def run_search_plan(
    settings: Settings,
    *,
    collector: str | None = "gupy",
    tags: Iterable[str] = (),
    dry_run: bool = False,
    max_queries: int | None = None,
    max_pages_per_query: int | None = None,
    max_items_per_query: int | None = None,
    max_total_requests: int | None = None,
    max_total_items: int | None = None,
    max_duration_seconds: int | None = None,
    continue_on_error: bool = True,
    http_client: HttpClient | None = None,
) -> SearchPlanRunResult:
    network = load_network_config(settings.config_dir)
    budget = search_plan_budget(
        network,
        max_total_requests=max_total_requests,
        max_total_items=max_total_items,
        max_duration_seconds=max_duration_seconds,
    )
    queries = filtered_queries(
        settings,
        collector=collector,
        tags=list(tags),
        max_queries=max_queries,
    )
    executions: list[tuple[SearchQueryConfig, CollectionExecutionReport]] = []
    errors: list[str] = []
    budget_state = SearchPlanBudgetState(budget)
    client = http_client or http_client_for_search_plan(network, budget_state)
    try:
        for query in queries:
            if budget_state.should_stop_before_query():
                break
            query_max_pages = budget_state.max_pages_for_query(
                positive_override(max_pages_per_query, "max-pages-per-query") or query.max_pages
            )
            query_max_items = budget_state.max_items_for_query(
                positive_override(max_items_per_query, "max-items-per-query") or query.max_items
            )
            if query_max_pages <= 0 or query_max_items <= 0:
                break
            try:
                execution = collect_single_query(
                    settings,
                    query,
                    dry_run=dry_run,
                    max_pages=query_max_pages,
                    max_items=query_max_items,
                    http_client=client,
                )
                execution = budget_state.record_execution(query, execution)
                executions.append((query, execution))
            except Exception as exc:
                errors.append(f"{query.key}: {exc}")
                if not continue_on_error:
                    raise
            if budget_state.exhausted:
                break
    finally:
        if http_client is None:
            client.close()
    return SearchPlanRunResult(executions=executions, errors=errors, budget_state=budget_state)


def collect_single_query(
    settings: Settings,
    query: SearchQueryConfig,
    *,
    dry_run: bool,
    max_pages: int | None,
    max_items: int | None,
    http_client: HttpClient | None = None,
) -> CollectionExecutionReport:
    if not query.enabled:
        raise RadarError(f"Consulta desativada: {query.key}")
    effective_max_pages = positive_override(max_pages, "max-pages") or query.max_pages
    effective_max_items = positive_override(max_items, "max-items") or query.max_items
    network = load_network_config(settings.config_dir)
    client = http_client or http_client_for_search_plan(network)
    context = build_collection_context(
        collector=query.collector,
        company_name=None,
        dry_run=dry_run,
        max_items=effective_max_items,
        max_pages=effective_max_pages,
        authority=CollectionAuthority.DISCOVERY_QUERY,
        query_key=query.key,
        query_mode=query.mode,
        query_parameters={
            "search_text": query.search_text,
            "filters": query.filters,
            "hydrate_details": query.hydrate_details,
        },
    )
    context = replace(
        context,
        source_name=f"Gupy query {query.key}",
        source_type=query.collector,
        collection_scope_key=query.collection_scope_key,
        http_client=client,
        collection_config=network.collection,
    )
    try:
        result = get_collector(query.collector).collect(context)
    except Exception as exc:
        with session_scope(settings) as session:
            record_failed_collection(
                session,
                context,
                exc,
                search_query_config=query,
                settings=settings,
            )
        raise
    finally:
        if http_client is None:
            client.close()
    with session_scope(settings) as session:
        return run_collection_persistence(
            session,
            settings,
            context,
            result,
            search_query_config=query,
        )


def http_client_for_search_plan(
    network: NetworkConfig,
    budget_state: SearchPlanBudgetState | None = None,
) -> HttpClient:
    return HttpClient(
        network.http,
        minimum_interval_between_requests_seconds=(
            network.collection.minimum_interval_between_requests_seconds
        ),
        request_budget=budget_state.request_budget if budget_state is not None else None,
    )


def search_plan_budget(
    network: NetworkConfig,
    *,
    max_total_requests: int | None,
    max_total_items: int | None,
    max_duration_seconds: int | None,
) -> SearchPlanBudget:
    return SearchPlanBudget(
        max_total_requests=(
            positive_override(max_total_requests, "max-total-requests")
            or network.search_plan.max_total_requests
        ),
        max_total_items=(
            positive_override(max_total_items, "max-total-items")
            or network.search_plan.max_total_items
        ),
        max_duration_seconds=(
            positive_override(max_duration_seconds, "max-duration-seconds")
            or network.search_plan.max_duration_seconds
        ),
    )


def filtered_queries(
    settings: Settings,
    *,
    collector: str | None,
    tags: list[str],
    max_queries: int | None,
) -> list[SearchQueryConfig]:
    limit = positive_override(max_queries, "max-queries")
    queries = load_search_queries(settings.config_dir).enabled_queries()
    if collector is not None:
        queries = [query for query in queries if query.collector == collector.strip().lower()]
    normalized_tags = {tag.strip().lower() for tag in tags if tag.strip()}
    if normalized_tags:
        queries = [query for query in queries if normalized_tags.issubset(set(query.tags))]
    queries = sorted(queries, key=lambda query: (query.priority, query.key))
    return queries[:limit] if limit is not None else queries


def positive_override(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise RadarError(f"--{label} deve ser um inteiro positivo.")
    return value


def query_report_payload(
    query: SearchQueryConfig,
    execution: CollectionExecutionReport,
) -> dict[str, object]:
    summary = execution.summary
    metadata = execution.metadata
    return {
        "query_key": query.key,
        "collector": query.collector,
        "mode": query.mode,
        "authority": CollectionAuthority.DISCOVERY_QUERY.value.lower(),
        "started_at": execution.started_at.isoformat(),
        "finished_at": execution.finished_at.isoformat(),
        "dry_run": execution.dry_run,
        "query": {
            "search_text": query.search_text,
            "filters": query.filters,
            "fingerprint": query.configuration_fingerprint,
            "collection_scope_key": query.collection_scope_key,
        },
        "network": {
            "requests": execution.network.get("requests", 0),
            "bytes_received": execution.network.get("bytes_received", 0),
            "retries": execution.network.get("retries", 0),
            "pages": int(metadata.get("pages", 0) or 0),
        },
        "summary": {
            "raw_results": int(metadata.get("raw_results", summary.found) or 0),
            "processed": summary.found,
            "invalid": summary.invalid_items,
            "new": summary.new,
            "known": summary.unchanged,
            "changed": summary.changed,
            "exact_duplicates": summary.exact_duplicates,
            "probable_duplicates": summary.probable_duplicates,
            "core": summary.core,
            "adjacent": summary.adjacent,
            "manual_review": summary.manual_review,
            "unrelated": summary.unrelated,
            "eligible": summary.eligible,
            "ineligible": summary.ineligible,
        },
        "partial": bool(metadata.get("partial", False)),
        "truncated": bool(metadata.get("truncated", False)),
        "warnings": execution.warnings,
        "errors": execution.errors,
        "public_interface": {
            "host": metadata.get("host"),
            "path": metadata.get("path"),
            "type": metadata.get("public_interface"),
        },
    }


def source_run_count(session: Session) -> int:
    from sqlalchemy import func, select

    from radar_vagas.persistence.models import SourceRun

    return int(session.scalar(select(func.count(SourceRun.id))) or 0)
