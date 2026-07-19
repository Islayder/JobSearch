from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from radar_vagas.collection.contracts import CollectionContext, CollectorError
from radar_vagas.collectors.greenhouse.collector import GreenhouseCollector
from radar_vagas.collectors.lever.collector import LeverCollector
from radar_vagas.config.schemas import CollectionConfig, HttpConfig
from radar_vagas.config.settings import PROJECT_ROOT
from radar_vagas.domain.enums import EmploymentType, WorkModel
from radar_vagas.http.client import HttpClient


class FakeResolver:
    def resolve(self, hostname: str) -> Sequence[str]:
        assert hostname in {"boards-api.greenhouse.io", "api.lever.co"}
        return ["93.184.216.34"]


def test_greenhouse_collector_maps_public_board_fixture() -> None:
    result = _collect_greenhouse("list.json")

    assert result.complete_snapshot is True
    assert result.requests == 1
    assert len(result.items) == 2
    first = result.items[0]
    assert first.external_id == "1001"
    assert first.company == "Empresa Exemplo"
    assert first.employment_type is EmploymentType.INTERNSHIP
    assert first.work_model is WorkModel.REMOTE
    assert first.remote_country_scope == "Brasil"
    assert "Trabalhar com dados" in (first.description or "")
    assert first.metadata["departments"] == ["Data"]


def test_greenhouse_collector_rejects_invalid_schema() -> None:
    with pytest.raises(CollectorError):
        _collect_greenhouse("invalid_schema.json")


def test_greenhouse_collector_handles_304() -> None:
    client = _http_client(lambda _request: httpx.Response(304, headers={"ETag": '"abc"'}))
    context = _context("greenhouse", client)

    result = GreenhouseCollector().collect(context)

    assert result.not_modified is True
    assert result.items == []
    client.close()


def test_lever_collector_maps_public_postings_fixture() -> None:
    result = _collect_lever("list.json")

    assert result.complete_snapshot is True
    assert len(result.items) == 2
    first = result.items[0]
    assert first.external_id == "lever-1"
    assert first.application_url == "https://jobs.lever.co/empresa/lever-1/apply"
    assert first.employment_type is EmploymentType.INTERNSHIP
    assert first.work_model is WorkModel.REMOTE
    assert first.published_at is not None
    assert "Requisitos" in (first.description or "")


def test_lever_collector_rejects_invalid_schema() -> None:
    with pytest.raises(CollectorError):
        _collect_lever("invalid_schema.json")


def _collect_greenhouse(filename: str):
    client = _http_client(
        lambda _request: httpx.Response(
            200,
            content=_fixture("greenhouse", filename).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    )
    try:
        return GreenhouseCollector().collect(_context("greenhouse", client))
    finally:
        client.close()


def _collect_lever(filename: str):
    client = _http_client(
        lambda _request: httpx.Response(
            200,
            content=_fixture("lever", filename).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    )
    try:
        return LeverCollector().collect(_context("lever", client))
    finally:
        client.close()


def _context(collector: str, client: HttpClient) -> CollectionContext:
    return CollectionContext(
        collector=collector,
        source_name=f"{collector.title()}: Empresa Exemplo",
        source_type=collector,
        company_name="Empresa Exemplo",
        board_key=f"empresa-{collector}",
        board_token="empresa",
        http_client=client,
        collection_config=CollectionConfig(default_max_items=10),
    )


def _http_client(handler) -> HttpClient:
    return HttpClient(
        HttpConfig(),
        resolver=FakeResolver(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )


def _fixture(collector: str, name: str) -> str:
    return (PROJECT_ROOT / "tests" / "fixtures" / "http" / collector / name).read_text(
        encoding="utf-8"
    )
