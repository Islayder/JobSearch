from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from sqlalchemy import func, select

from radar_vagas.collection.contracts import CollectionContext, CollectionResult
from radar_vagas.collection.orchestrator import build_collection_context, run_collection_persistence
from radar_vagas.config.schemas import BoardConfig, CollectionConfig, SearchQueryConfig
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import CollectionAuthority, PostingStatus, WorkModel
from radar_vagas.ingestion.import_schema import ImportedPosting
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import DiscoveryHit, Job, Posting, PostingRevision, SearchQuery


def test_discovery_query_never_closes_existing_posting(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    board_context = _board_context(close_after=1)

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            board_context,
            _result("greenhouse", [_board_posting("1001")], complete_snapshot=True),
            board_config=_board(),
        )
        query = _query("gupy-estagio-dados", "estagio dados")
        query_context = _query_context(query)
        first = run_collection_persistence(
            session,
            settings,
            query_context,
            _result("gupy", [], complete_snapshot=True),
            search_query_config=query,
        )
        second = run_collection_persistence(
            session,
            settings,
            query_context,
            _result("gupy", [], complete_snapshot=True),
            search_query_config=query,
        )

        posting = session.scalar(select(Posting).where(Posting.external_id == "1001"))
        assert posting is not None
        assert first.summary.closed == 0
        assert second.summary.closed == 0
        assert posting.is_active is True
        assert posting.status is not PostingStatus.CLOSED
        assert posting.missing_count == 0


def test_gupy_identity_deduplicates_across_queries_and_records_hits(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    queries = [
        _query("gupy-estagio-dados", "estagio dados"),
        _query("gupy-estagio-tecnologia", "estagio tecnologia"),
        _query("gupy-estagio-analytics", "estagio analytics"),
    ]

    with session_scope(settings) as session:
        for query in queries:
            run_collection_persistence(
                session,
                settings,
                _query_context(query),
                _result("gupy", [_gupy_posting("9001")]),
                search_query_config=query,
            )

        assert session.scalar(select(func.count(Posting.id))) == 1
        assert session.scalar(select(func.count(Job.id))) == 1
        assert session.scalar(select(func.count(SearchQuery.id))) == 3
        assert session.scalar(select(func.count(DiscoveryHit.id))) == 3


def test_gupy_identity_changed_content_updates_same_posting_and_revision(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    query_a = _query("gupy-estagio-dados", "estagio dados")
    query_b = _query("gupy-estagio-analytics", "estagio analytics")

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            _query_context(query_a),
            _result("gupy", [_gupy_posting("9001")]),
            search_query_config=query_a,
        )
        changed = run_collection_persistence(
            session,
            settings,
            _query_context(query_b),
            _result("gupy", [_gupy_posting("9001", title="Estagio em Dados e BI")]),
            search_query_config=query_b,
        )

        posting = session.scalar(
            select(Posting).where(Posting.provider_identity_key == "gupy:9001")
        )
        assert changed.summary.changed == 1
        assert posting is not None
        assert posting.raw_title == "Estagio em Dados e BI"
        assert session.scalar(select(func.count(Posting.id))) == 1
        assert session.scalar(select(func.count(PostingRevision.id))) == 1


def test_query_dry_run_writes_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    query = _query("gupy-estagio-dados", "estagio dados")
    context = replace(_query_context(query), dry_run=True)

    with session_scope(settings) as session:
        before = _counts(session)
        report = run_collection_persistence(
            session,
            settings,
            context,
            _result("gupy", [_gupy_posting("9001")]),
            search_query_config=query,
        )
        after = _counts(session)

        assert report.summary.new == 1
        assert before == after


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )


def _query(key: str, search_text: str) -> SearchQueryConfig:
    return SearchQueryConfig(
        key=key,
        collector="gupy",
        mode="public_portal",
        enabled=True,
        priority=10,
        tags=["data"],
        search_text=search_text,
        filters={"country": "Brasil"},
        max_pages=1,
        max_items=10,
    )


def _query_context(query: SearchQueryConfig) -> CollectionContext:
    context = build_collection_context(
        collector="gupy",
        company_name=None,
        dry_run=False,
        max_items=query.max_items,
        max_pages=query.max_pages,
        authority=CollectionAuthority.DISCOVERY_QUERY,
        query_key=query.key,
        query_mode=query.mode,
        query_parameters={"search_text": query.search_text, "filters": query.filters},
    )
    return replace(
        context,
        source_name=f"Gupy query {query.key}",
        source_type="gupy",
        collection_scope_key=query.collection_scope_key,
    )


def _board_context(*, close_after: int) -> CollectionContext:
    context = build_collection_context(
        collector="greenhouse",
        company_name="Empresa Exemplo",
        board_key="empresa-greenhouse",
        board_token="empresa",
    )
    return replace(
        context,
        collection_config=CollectionConfig(close_after_missing_successful_runs=close_after),
    )


def _result(
    collector: str,
    items: list[ImportedPosting],
    *,
    complete_snapshot: bool = False,
) -> CollectionResult:
    return CollectionResult(
        collector=collector,
        items=items,
        requests=1,
        bytes_received=100,
        complete_snapshot=complete_snapshot,
        status_code=200,
        metadata={"truncated": False},
    )


def _gupy_posting(external_id: str, *, title: str = "Estagio em Dados") -> ImportedPosting:
    return ImportedPosting(
        source_name="Gupy query teste",
        source_type="gupy",
        provider="gupy",
        provider_external_id=external_id,
        provider_identity_key=f"gupy:{external_id}",
        external_id=external_id,
        url=f"https://empresa.gupy.io/jobs/{external_id}",
        title=title,
        company="Empresa Exemplo",
        location="Remote - Brazil",
        description=f"{title}. Trabalhar com SQL e Python.",
        employment_type="internship",
        work_model="remote",
        country="Brasil",
        remote_country_scope="Brasil",
        application_url=f"https://empresa.gupy.io/jobs/{external_id}",
        metadata={"page_number": 1, "position_in_results": 1},
    )


def _board_posting(external_id: str) -> ImportedPosting:
    return ImportedPosting(
        source_name="Greenhouse: Empresa Exemplo",
        source_type="greenhouse",
        provider="greenhouse",
        provider_scope="empresa",
        provider_external_id=external_id,
        provider_identity_key=f"greenhouse:empresa:{external_id}",
        external_id=external_id,
        url=f"https://boards.greenhouse.io/empresa/jobs/{external_id}",
        title="Estagio em Dados",
        company="Empresa Exemplo",
        location="Remote - Brazil",
        description="Trabalhar com dados e Python.",
        employment_type="internship",
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        application_url=f"https://boards.greenhouse.io/empresa/jobs/{external_id}",
    )


def _board() -> BoardConfig:
    return BoardConfig(
        key="empresa-greenhouse",
        company_name="Empresa Exemplo",
        collector="greenhouse",
        board_token="empresa",
        enabled=True,
    )


def _counts(session) -> dict[str, int]:
    return {
        "search_queries": session.scalar(select(func.count(SearchQuery.id))) or 0,
        "discovery_hits": session.scalar(select(func.count(DiscoveryHit.id))) or 0,
        "postings": session.scalar(select(func.count(Posting.id))) or 0,
        "jobs": session.scalar(select(func.count(Job.id))) or 0,
    }
