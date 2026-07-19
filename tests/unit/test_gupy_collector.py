from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from radar_vagas.collection.contracts import CollectionContext, CollectorError
from radar_vagas.collectors.gupy.collector import GupyCollector
from radar_vagas.config.schemas import CollectionConfig, HttpConfig
from radar_vagas.config.settings import PROJECT_ROOT
from radar_vagas.domain.enums import CollectionAuthority, EmploymentType, WorkModel
from radar_vagas.http.client import HttpClient, HttpClientError, HttpRequestBudget


class FakeResolver:
    def resolve(self, hostname: str) -> Sequence[str]:
        assert hostname in {"employability-portal.gupy.io", "evil.example.com"}
        return ["93.184.216.34"]


def test_gupy_collector_maps_public_portal_first_page() -> None:
    result = _collect(["page1.json"], max_items=2, max_pages=1)

    assert result.complete_snapshot is False
    assert result.partial is True
    assert result.metadata["host"] == "employability-portal.gupy.io"
    assert result.metadata["path"] == "/api/v1/jobs"
    assert result.metadata["truncated"] is True
    assert len(result.items) == 2
    first = result.items[0]
    assert first.provider_identity_key == "gupy:1001"
    assert first.employment_type is EmploymentType.INTERNSHIP
    assert first.work_model is WorkModel.REMOTE
    assert first.remote_country_scope == "Brasil"
    assert "SQL" in (first.description or "")


def test_gupy_collector_multiple_pages_and_total() -> None:
    result = _collect(["page1.json", "page2.json"], max_items=10, max_pages=2)

    assert len(result.items) == 3
    assert result.metadata["pages"] == 2
    assert result.metadata["total_available"] == 3
    assert result.metadata["truncated"] is False


def test_gupy_collector_skips_invalid_and_fake_domains() -> None:
    result = _collect(["invalid_item.json"], max_items=10, max_pages=1)

    assert len(result.items) == 1
    assert len(result.invalid_items) == 2
    assert result.partial is True


def test_gupy_collector_rejects_bad_limits_and_mode() -> None:
    with pytest.raises(CollectorError, match="max_pages"):
        _collect(["empty.json"], max_items=10, max_pages=0)
    with pytest.raises(CollectorError, match="max_items"):
        _collect(["empty.json"], max_items=0, max_pages=1)
    with pytest.raises(CollectorError, match="public_portal"):
        GupyCollector().collect(_context(_client(lambda _request: httpx.Response(200)), mode="x"))


def test_gupy_collector_invalid_json_and_repeated_page_are_partial() -> None:
    client = _client(
        lambda _request: httpx.Response(200, text=".", headers={"Content-Type": "application/json"})
    )
    with pytest.raises(CollectorError, match="JSON"):
        GupyCollector().collect(_context(client))
    client.close()

    repeated = _collect(["page1.json", "page1.json"], max_items=10, max_pages=2)
    assert repeated.partial is True
    assert repeated.metadata["repeated_page"] is True


def test_gupy_collector_blocks_redirect_outside_allowlist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "employability-portal.gupy.io":
            return httpx.Response(302, headers={"Location": "https://evil.example.com/jobs"})
        return httpx.Response(200, json={})

    client = _client(handler)
    try:
        with pytest.raises(HttpClientError, match="allowlist"):
            GupyCollector().collect(_context(client))
    finally:
        client.close()


def test_gupy_collector_marks_budget_interruption_partial_without_platform_failure() -> None:
    calls = 0
    budget = HttpRequestBudget(max_requests=1)

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            content=_fixture("page1.json").encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    client = _client(handler, request_budget=budget)
    try:
        result = GupyCollector().collect(_context(client, max_items=10, max_pages=2))
    finally:
        client.close()

    assert calls == 1
    assert result.requests == 1
    assert result.partial is True
    assert result.metadata["truncated"] is True
    assert result.metadata["budget_limited_by"] == "max_total_requests"
    assert result.recoverable_errors == []


def _collect(fixture_names: list[str], *, max_items: int, max_pages: int):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        name = fixture_names[min(calls, len(fixture_names) - 1)]
        calls += 1
        return httpx.Response(
            200,
            content=_fixture(name).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    client = _client(handler)
    try:
        return GupyCollector().collect(_context(client, max_items=max_items, max_pages=max_pages))
    finally:
        client.close()


def _context(
    client: HttpClient,
    *,
    mode: str = "public_portal",
    max_items: int = 10,
    max_pages: int = 1,
) -> CollectionContext:
    return CollectionContext(
        collector="gupy",
        source_name="Gupy query teste",
        source_type="gupy",
        authority=CollectionAuthority.DISCOVERY_QUERY,
        query_key="gupy-teste",
        query_mode=mode,
        query_parameters={"search_text": "dados", "filters": {"country": "Brasil"}},
        http_client=client,
        collection_config=CollectionConfig(default_max_items=max(max_items, 1)),
        max_items=max_items,
        max_pages=max_pages,
    )


def _client(handler, *, request_budget: HttpRequestBudget | None = None) -> HttpClient:
    return HttpClient(
        HttpConfig(),
        resolver=FakeResolver(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        request_budget=request_budget,
    )


def _fixture(name: str) -> str:
    return (PROJECT_ROOT / "tests" / "fixtures" / "http" / "gupy" / name).read_text(
        encoding="utf-8"
    )
