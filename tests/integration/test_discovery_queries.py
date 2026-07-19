from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar_vagas.collection.contracts import CollectionContext, CollectionResult
from radar_vagas.collection.orchestrator import build_collection_context, run_collection_persistence
from radar_vagas.config.loaders import load_relevance_rules
from radar_vagas.config.schemas import BoardConfig, CollectionConfig, SearchQueryConfig
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import (
    CollectionAuthority,
    EligibilityStatus,
    JobStatus,
    PostingStatus,
    RelevanceStatus,
    WorkModel,
)
from radar_vagas.eligibility.workflow import evaluate_job_record, reevaluate_jobs
from radar_vagas.ingestion.file_import_service import _analyze_items
from radar_vagas.ingestion.file_parser import ParsedImportFile, ParsedImportItem
from radar_vagas.ingestion.import_schema import ImportedPosting
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    Decision,
    DiscoveryHit,
    Job,
    Posting,
    PostingRevision,
    SearchQuery,
)
from radar_vagas.relevance.service import (
    build_role_relevance_input_from_posting,
    evaluate_role_relevance,
)


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


@pytest.mark.parametrize(
    "case_slug",
    [
        "skills_only_technical",
        "department_only_adjacent",
        "generic_title_technical_description",
        "technical_title_negative_description",
        "gupy_department_and_skills",
        "greenhouse_metadata",
        "lever_categories_and_lists",
        "local_file_without_structured_metadata",
    ],
)
def test_relevance_parity_between_dry_analysis_and_persisted_decision(
    tmp_path: Path,
    case_slug: str,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    posting = _parity_posting(case_slug)
    context, board_config, query = _parity_context(case_slug, posting)

    with session_scope(settings) as session:
        dry_analysis = _analyze_items(session, _parsed_fixture(posting), settings)[0]
        expected_relevance = evaluate_role_relevance(
            build_role_relevance_input_from_posting(posting),
            load_relevance_rules(settings.config_dir),
        )

        run_collection_persistence(
            session,
            settings,
            context,
            _result(context.collector, [posting]),
            board_config=board_config,
            search_query_config=query,
        )
        persisted_posting = session.scalars(select(Posting)).one()
        job = persisted_posting.job
        assert job is not None
        decision = job.decision
        assert decision is not None
        assert decision.relevance_status is dry_analysis.relevance_status
        assert decision.relevance_score == dry_analysis.relevance_score
        assert decision.eligibility_status is dry_analysis.eligibility_status
        assert decision.reason_code == dry_analysis.reason_code
        assert decision.relevance_status is expected_relevance.status
        assert decision.relevance_score == expected_relevance.score
        assert decision.relevance_rules_version == expected_relevance.rules_version
        assert decision.relevance_reason_json is not None
        assert json.loads(decision.relevance_reason_json) == expected_relevance.reason
        if decision.eligibility_status is EligibilityStatus.ELIGIBLE:
            assert decision.ranking_score is not None
        before_ranking_score = decision.ranking_score

        evaluate_job_record(session, job, settings)

        assert session.scalar(select(func.count(Decision.id))) == 1
        assert job.decision is not None
        assert job.decision.ranking_score == before_ranking_score


def test_discovery_observation_does_not_reset_authoritative_lifecycle(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    board_context = _board_context(close_after=2)
    query = _query("gupy-estagio-dados", "estagio dados")
    provider_key = "synthetic:shared-1001"

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            board_context,
            _result(
                "greenhouse",
                [_board_posting("1001", provider_identity_key=provider_key)],
                complete_snapshot=True,
            ),
            board_config=_board(),
        )
        posting = session.scalar(
            select(Posting).where(Posting.provider_identity_key == provider_key)
        )
        assert posting is not None
        authoritative_source_id = posting.source_id
        authoritative_run_id = posting.source_run_id
        authoritative_scope = posting.collection_scope_key
        authoritative_last_seen = posting.last_seen_at

        run_collection_persistence(
            session,
            settings,
            board_context,
            _result("greenhouse", [], complete_snapshot=True),
            board_config=_board(),
        )
        assert posting.missing_count == 1

        run_collection_persistence(
            session,
            settings,
            _query_context(query),
            _result(
                "gupy",
                [
                    _gupy_posting(
                        "1001",
                        title="Estagio em Dados Atualizado",
                        provider_identity_key=provider_key,
                    )
                ],
            ),
            search_query_config=query,
        )
        assert posting.source_id == authoritative_source_id
        assert posting.source_run_id == authoritative_run_id
        assert posting.collection_scope_key == authoritative_scope
        assert posting.last_seen_at == authoritative_last_seen
        assert posting.missing_count == 1
        assert posting.is_active is True
        assert session.scalar(select(func.count(DiscoveryHit.id))) == 1
        assert session.scalar(select(func.count(PostingRevision.id))) == 1

        run_collection_persistence(
            session,
            settings,
            board_context,
            _result("greenhouse", [], complete_snapshot=True),
            board_config=_board(),
        )
        assert posting.is_active is False
        assert posting.status is PostingStatus.CLOSED

        run_collection_persistence(
            session,
            settings,
            _query_context(_query("gupy-estagio-tecnologia", "estagio tecnologia")),
            _result("gupy", [_gupy_posting("1001", provider_identity_key=provider_key)]),
            search_query_config=_query("gupy-estagio-tecnologia", "estagio tecnologia"),
        )
        assert posting.is_active is False
        latest_hit = session.scalars(
            select(DiscoveryHit).order_by(DiscoveryHit.id.desc()).limit(1)
        ).one()
        assert latest_hit.match_status == "lifecycle_conflict"


def test_reevaluate_jobs_preserves_applied_and_dismissed_statuses(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    query = _query("gupy-estagio-dados", "estagio dados")

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            _query_context(query),
            _result("gupy", [_gupy_posting("9101"), _gupy_posting("9102")]),
            search_query_config=query,
        )
        jobs = session.scalars(select(Job).order_by(Job.id.asc())).all()
        assert len(jobs) == 2
        jobs[0].status = JobStatus.APPLIED
        jobs[1].status = JobStatus.DISMISSED
        session.flush()

        summary = reevaluate_jobs(session, settings, dry_run=False)

        assert summary.total == 2
        assert jobs[0].status is JobStatus.APPLIED
        assert jobs[1].status is JobStatus.DISMISSED
        assert jobs[0].decision is not None
        assert jobs[0].decision.relevance_status is RelevanceStatus.CORE


def test_reevaluate_jobs_recomputes_archived_content_without_history_reason(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    query = _query("gupy-estagio-dados", "estagio dados")

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            _query_context(query),
            _result(
                "gupy",
                [
                    _gupy_posting(
                        "9201",
                        city="Sao Paulo",
                        state="SP",
                        location="Sao Paulo, SP, Brasil",
                        work_model="onsite",
                    )
                ],
            ),
            search_query_config=query,
        )
        job = session.scalars(select(Job)).one()
        assert job.status is JobStatus.ARCHIVED
        assert job.decision is not None
        assert job.decision.reason_code == "LOCATION_NOT_BELO_HORIZONTE"
        job_id = job.id
        session.commit()

        summary = reevaluate_jobs(session, settings, provider="gupy", dry_run=True)
        job = session.get(Job, job_id)
        assert job is not None

        assert summary.total == 1
        assert summary.changed == 0
        assert job.status is JobStatus.ARCHIVED
        assert job.decision.reason_code == "LOCATION_NOT_BELO_HORIZONTE"


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


def _gupy_posting(
    external_id: str,
    *,
    title: str = "Estagio em Dados",
    description: str | None = None,
    department: str | None = None,
    technologies: list[str] | None = None,
    provider_identity_key: str | None = None,
    city: str | None = None,
    state: str | None = None,
    location: str = "Remote - Brazil",
    work_model: str | WorkModel = "remote",
) -> ImportedPosting:
    return ImportedPosting(
        source_name="Gupy query teste",
        source_type="gupy",
        provider="gupy",
        provider_external_id=external_id,
        provider_identity_key=provider_identity_key or f"gupy:{external_id}",
        external_id=external_id,
        url=f"https://empresa.gupy.io/jobs/{external_id}",
        title=title,
        company="Empresa Exemplo",
        location=location,
        description=description or f"{title}. Trabalhar com SQL e Python.",
        department=department,
        technologies=technologies or [],
        employment_type="internship",
        work_model=work_model,
        country="Brasil",
        state=state,
        city=city,
        remote_country_scope="Brasil",
        application_url=f"https://empresa.gupy.io/jobs/{external_id}",
        metadata={"page_number": 1, "position_in_results": 1},
    )


def _board_posting(
    external_id: str,
    *,
    provider_identity_key: str | None = None,
) -> ImportedPosting:
    return ImportedPosting(
        source_name="Greenhouse: Empresa Exemplo",
        source_type="greenhouse",
        provider="greenhouse",
        provider_scope="empresa",
        provider_external_id=external_id,
        provider_identity_key=provider_identity_key or f"greenhouse:empresa:{external_id}",
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


def _parity_posting(case_slug: str) -> ImportedPosting:
    base = {
        "source_name": f"Parity {case_slug}",
        "source_type": "gupy",
        "provider": "gupy",
        "provider_external_id": f"parity-{case_slug}",
        "provider_identity_key": f"gupy:parity-{case_slug}",
        "external_id": f"parity-{case_slug}",
        "url": f"https://empresa.gupy.io/jobs/parity-{case_slug}",
        "title": "Estagio",
        "company": "Empresa Exemplo",
        "location": "Remote - Brazil",
        "description": "Apoio a areas internas.",
        "employment_type": "internship",
        "work_model": "remote",
        "country": "Brasil",
        "remote_country_scope": "Brasil",
        "application_url": f"https://empresa.gupy.io/jobs/parity-{case_slug}",
    }
    cases = {
        "skills_only_technical": {
            "title": "Estagio",
            "description": "Apoio ao time.",
            "technologies": ["SQL"],
        },
        "department_only_adjacent": {
            "title": "Estagio",
            "description": "Apoio ao time.",
            "department": "Credito e Risco",
        },
        "generic_title_technical_description": {
            "title": "Estagio",
            "description": "Trabalhar com dados, SQL e Python.",
        },
        "technical_title_negative_description": {
            "title": "Estagio em Dados",
            "description": "Cadastro de dados e atendimento ao cliente.",
        },
        "gupy_department_and_skills": {
            "title": "Estagio em Operacoes",
            "description": "Apoio a indicadores.",
            "department": "Dados e Analytics",
            "technologies": ["SQL", "Power BI"],
        },
        "greenhouse_metadata": {
            "source_type": "greenhouse",
            "provider": "greenhouse",
            "provider_scope": "empresa",
            "provider_identity_key": "greenhouse:empresa:parity-greenhouse",
            "url": "https://boards.greenhouse.io/empresa/jobs/parity-greenhouse",
            "application_url": "https://boards.greenhouse.io/empresa/jobs/parity-greenhouse",
            "title": "Estagio",
            "description": "Apoio ao time.",
            "metadata": {"departments": ["Dados"], "skills": ["SQL"]},
        },
        "lever_categories_and_lists": {
            "source_type": "lever",
            "provider": "lever",
            "provider_scope": "empresa",
            "provider_identity_key": "lever:empresa:parity-lever",
            "url": "https://jobs.lever.co/empresa/parity-lever",
            "application_url": "https://jobs.lever.co/empresa/parity-lever/apply",
            "title": "Estagio",
            "description": "Programa de estagio.",
            "department": "Dados",
            "requirements": "SQL",
            "responsibilities": "Dashboards e indicadores.",
            "metadata": {"team": "Analytics"},
        },
        "local_file_without_structured_metadata": {
            "source_type": "manual",
            "provider": None,
            "provider_external_id": None,
            "provider_identity_key": None,
            "external_id": "local-file-parity",
            "url": "https://empresa.example/jobs/local-file-parity",
            "application_url": "https://empresa.example/jobs/local-file-parity",
            "title": "Estagio em Dados",
            "description": "Trabalhar com dashboards e indicadores.",
        },
    }
    return ImportedPosting(**{**base, **cases[case_slug]})


def _parity_context(
    case_slug: str,
    posting: ImportedPosting,
) -> tuple[CollectionContext, BoardConfig | None, SearchQueryConfig | None]:
    if posting.provider == "gupy":
        query = _query(f"gupy-{case_slug}", posting.title)
        return _query_context(query), None, query
    if posting.provider in {"greenhouse", "lever"}:
        board_key = f"{posting.provider}-{case_slug}"
        board_token = posting.provider_scope or "empresa"
        context = build_collection_context(
            collector=posting.provider,
            company_name=posting.company,
            board_key=board_key,
            board_token=board_token,
        )
        board = BoardConfig(
            key=board_key,
            company_name=posting.company,
            collector=posting.provider,
            board_token=board_token,
            enabled=True,
        )
        return context, board, None
    context = build_collection_context(
        collector="jobposting",
        company_name=posting.company,
        url=posting.url,
        authority=CollectionAuthority.SINGLE_PAGE,
    )
    return context, None, None


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


def _parsed_fixture(posting: ImportedPosting) -> ParsedImportFile:
    return ParsedImportFile(
        input_file=Path("dry-analysis.json"),
        file_format="collection",
        schema_version="1.0",
        items=[
            ParsedImportItem(
                line_number=None,
                item_index=1,
                raw_fields=posting.model_dump(mode="json"),
                posting=posting,
                errors=[],
            )
        ],
    )
