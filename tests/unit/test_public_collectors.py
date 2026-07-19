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
    assert result.partial is False
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
    assert result.partial is False
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


def test_greenhouse_collector_marks_limited_payload_as_partial() -> None:
    payload = {"jobs": [_greenhouse_job(index) for index in range(20)]}

    result = _collect_greenhouse_payload(payload, max_items=10)

    assert len(result.items) == 10
    assert result.partial is True
    assert result.complete_snapshot is False
    assert result.metadata["raw_items"] == 20
    assert result.metadata["considered_items"] == 10
    assert result.metadata["processed_items"] == 10
    assert result.metadata["truncated"] is True


def test_lever_collector_marks_limited_payload_as_partial() -> None:
    payload = [_lever_posting(index) for index in range(20)]

    result = _collect_lever_payload(payload, max_items=10)

    assert len(result.items) == 10
    assert result.partial is True
    assert result.complete_snapshot is False
    assert result.metadata["raw_items"] == 20
    assert result.metadata["considered_items"] == 10
    assert result.metadata["processed_items"] == 10
    assert result.metadata["truncated"] is True


def test_greenhouse_collector_skips_invalid_items_without_artificial_titles() -> None:
    payload = {
        "jobs": [
            _greenhouse_job(1),
            {**_greenhouse_job(2), "title": ""},
            {**_greenhouse_job(3), "id": ""},
            {**_greenhouse_job(4), "absolute_url": "not a url"},
        ]
    }

    result = _collect_greenhouse_payload(payload, max_items=10)

    assert len(result.items) == 1
    assert result.items[0].title == "Estagio em Dados 1"
    assert result.partial is True
    assert result.complete_snapshot is False
    assert result.metadata["invalid_items"] == 3
    assert len(result.invalid_items) == 3
    assert all(item["raw_excerpt"] for item in result.invalid_items)


def test_lever_collector_skips_invalid_items_without_artificial_titles() -> None:
    payload = [
        _lever_posting(1),
        {**_lever_posting(2), "text": ""},
        {**_lever_posting(3), "id": ""},
        {**_lever_posting(4), "hostedUrl": "file:///tmp/local"},
    ]

    result = _collect_lever_payload(payload, max_items=10)

    assert len(result.items) == 1
    assert result.items[0].title == "Estagio em Dados 1"
    assert result.partial is True
    assert result.complete_snapshot is False
    assert result.metadata["invalid_items"] == 3
    assert len(result.invalid_items) == 3
    assert all(item["raw_excerpt"] for item in result.invalid_items)


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


def _collect_greenhouse_payload(payload: dict[str, object], *, max_items: int):
    client = _http_client(
        lambda _request: httpx.Response(
            200,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    )
    try:
        return GreenhouseCollector().collect(_context("greenhouse", client, max_items=max_items))
    finally:
        client.close()


def _collect_lever_payload(payload: list[dict[str, object]], *, max_items: int):
    client = _http_client(
        lambda _request: httpx.Response(
            200,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    )
    try:
        return LeverCollector().collect(_context("lever", client, max_items=max_items))
    finally:
        client.close()


def _context(collector: str, client: HttpClient, *, max_items: int = 10) -> CollectionContext:
    return CollectionContext(
        collector=collector,
        source_name=f"{collector.title()} board empresa-{collector}",
        source_type=collector,
        company_name="Empresa Exemplo",
        board_key=f"empresa-{collector}",
        board_token="empresa",
        http_client=client,
        collection_config=CollectionConfig(default_max_items=max_items),
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


def _greenhouse_job(index: int) -> dict[str, object]:
    return {
        "id": f"gh-{index}",
        "title": f"Estagio em Dados {index}",
        "absolute_url": f"https://boards.greenhouse.io/empresa/jobs/{index}",
        "location": {"name": "Remote - Brazil"},
        "content": "<p>Trabalhar com dados.</p>",
        "metadata": [{"name": "Employment Type", "value": "Internship"}],
    }


def _lever_posting(index: int) -> dict[str, object]:
    return {
        "id": f"lever-{index}",
        "text": f"Estagio em Dados {index}",
        "categories": {
            "location": "Remote - Brazil",
            "commitment": "Internship",
        },
        "hostedUrl": f"https://jobs.lever.co/empresa/{index}",
        "applyUrl": f"https://jobs.lever.co/empresa/{index}/apply",
        "descriptionPlain": "Trabalhar com dados.",
        "workplaceType": "remote",
    }
