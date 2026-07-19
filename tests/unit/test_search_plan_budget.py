from __future__ import annotations

from datetime import UTC, datetime

import pytest

from radar_vagas.cli.app import SearchPlanBudget, _SearchPlanBudgetState
from radar_vagas.collection.result import CollectionExecutionReport, CollectionSummary
from radar_vagas.config.schemas import SearchQueryConfig


def test_search_plan_budget_marks_execution_partial_when_request_limit_is_hit() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=1, max_total_items=10, max_duration_seconds=900)
    )
    query = _query()
    execution = _execution(requests=1, found=2)

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_total_requests"
    assert limited.metadata["partial"] is True
    assert limited.metadata["truncated"] is True
    assert limited.metadata["budget_limited_by"] == "max_total_requests"


def test_search_plan_budget_marks_execution_partial_when_item_limit_is_hit() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=10, max_total_items=2, max_duration_seconds=900)
    )
    query = _query()
    execution = _execution(requests=1, found=2)

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_total_items"
    assert limited.metadata["budget_limited_by"] == "max_total_items"


def test_search_plan_budget_marks_execution_partial_when_duration_limit_is_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import radar_vagas.cli.app as cli_app

    monkeypatch.setattr(cli_app.time, "monotonic", lambda: 12.0)
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=10, max_total_items=10, max_duration_seconds=2),
        started_at=10.0,
    )
    query = _query()
    execution = _execution(requests=1, found=1)

    limited = budget.record_execution(query, execution)

    assert budget.exhausted_by == "max_duration_seconds"
    assert limited.metadata["budget_limited_by"] == "max_duration_seconds"


def test_search_plan_budget_stops_before_starting_when_already_exhausted() -> None:
    budget = _SearchPlanBudgetState(
        SearchPlanBudget(max_total_requests=1, max_total_items=10, max_duration_seconds=900),
        requests_used=1,
    )

    assert budget.should_stop_before_query() is True
    assert budget.exhausted_by == "max_total_requests"


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
