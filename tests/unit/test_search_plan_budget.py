from __future__ import annotations

from datetime import UTC, datetime

from radar_vagas.cli.app import SearchPlanBudget, _SearchPlanBudgetState
from radar_vagas.collection.result import CollectionExecutionReport, CollectionSummary
from radar_vagas.config.schemas import SearchQueryConfig
from radar_vagas.http.client import HttpRequestBudget


def test_search_plan_budget_keeps_completed_execution_complete_at_request_limit() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=1, max_total_items=10, max_duration_seconds=900)
    )
    assert budget.request_budget is not None
    budget.request_budget.consume_request_attempt()
    query = _query()
    execution = _execution(requests=1, found=2)

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_total_requests"
    assert limited.metadata["partial"] is False
    assert limited.metadata["truncated"] is False
    assert "budget_limited_by" not in limited.metadata
    assert budget.should_stop_before_query() is True


def test_search_plan_budget_keeps_completed_execution_complete_at_item_limit() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=10, max_total_items=2, max_duration_seconds=900)
    )
    query = _query()
    execution = _execution(requests=1, found=2)

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_total_items"
    assert limited.metadata["partial"] is False
    assert limited.metadata["truncated"] is False
    assert "budget_limited_by" not in limited.metadata
    assert budget.should_stop_before_query() is True


def test_search_plan_budget_keeps_completed_execution_complete_at_duration_limit() -> None:
    clock_value = 10.0

    def clock() -> float:
        return clock_value

    request_budget = HttpRequestBudget(max_duration_seconds=2, monotonic=clock)
    clock_value = 12.0
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=10, max_total_items=10, max_duration_seconds=2),
        request_budget=request_budget,
    )
    query = _query()
    execution = _execution(requests=1, found=1)

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_duration_seconds"
    assert limited.metadata["partial"] is False
    assert limited.metadata["truncated"] is False
    assert "budget_limited_by" not in limited.metadata
    assert budget.should_stop_before_query() is True


def test_search_plan_budget_stops_before_starting_when_already_exhausted() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=1, max_total_items=10, max_duration_seconds=900),
    )
    assert budget.request_budget is not None
    budget.request_budget.consume_request_attempt()

    assert budget.should_stop_before_query() is True
    assert budget.exhausted_by == "max_total_requests"


def test_search_plan_budget_preserves_interrupted_execution_metadata() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=3, max_total_items=10, max_duration_seconds=900)
    )
    query = _query()
    execution = _execution(requests=3, found=1)
    execution.metadata.update(
        partial=True,
        truncated=True,
        budget_limited_by="max_total_requests",
    )

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_total_requests"
    assert limited.metadata["partial"] is True
    assert limited.metadata["truncated"] is True
    assert limited.metadata["budget_limited_by"] == "max_total_requests"


def _query() -> SearchQueryConfig:
    query = SearchQueryConfig(
        key="gupy-estagio-dados",
        collector="gupy",
        mode="public_portal",
        search_text="estagio dados",
        max_pages=1,
        max_items=10,
    )
    return query


def _execution(*, requests: int, found: int) -> CollectionExecutionReport:
    return CollectionExecutionReport(
        collector="gupy",
        board=None,
        started_at=datetime(2026, 7, 19, tzinfo=UTC),
        finished_at=datetime(2026, 7, 19, tzinfo=UTC),
        dry_run=True,
        network={"requests": requests, "bytes_received": 10, "retries": 0},
        summary=CollectionSummary(found=found),
        metadata={"partial": False, "truncated": False},
    )
