from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select

from radar_vagas.collection.contracts import CollectionContext, CollectionResult
from radar_vagas.collection.orchestrator import (
    build_collection_context,
    record_failed_collection,
    run_collection_persistence,
)
from radar_vagas.config.schemas import BoardConfig, CollectionConfig
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import ApplicationStatus, JobStatus, PostingStatus, WorkModel
from radar_vagas.ingestion.import_schema import ImportedPosting
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    Application,
    CompanyBoard,
    Decision,
    Job,
    Posting,
    PostingRevision,
    Source,
)


def test_collection_creates_then_updates_unchanged_and_changed_posting(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context()

    with session_scope(settings) as session:
        first = run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )
        assert first.summary.new == 1

        second = run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )
        assert second.summary.unchanged == 1

        changed = run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001", title="Estagio em Dados e BI")]),
            board_config=_board(),
        )

        assert changed.summary.changed == 1
        assert session.scalar(select(func.count(Posting.id))) == 1
        assert session.scalar(select(func.count(PostingRevision.id))) == 1
        job = session.scalar(select(Job))
        assert job is not None
        assert job.canonical_title == "Estagio em Dados e BI"


def test_collection_dry_run_does_not_write_database(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context(dry_run=True)

    with session_scope(settings) as session:
        report = run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )

        assert report.summary.new == 1
        assert session.scalar(select(func.count(Posting.id))) == 0
        assert session.scalar(select(func.count(CompanyBoard.id))) == 0


def test_complete_snapshots_close_after_two_absences_and_reopen(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context()

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001"), _posting("1002", title="Trainee em Produto")]),
            board_config=_board(),
        )

        first_absence = run_collection_persistence(
            session, settings, context, _result([]), board_config=_board()
        )
        posting = session.scalar(select(Posting).where(Posting.external_id == "1001"))
        assert posting is not None
        assert first_absence.summary.closed == 0
        assert posting.is_active is True
        assert posting.missing_count == 1

        second_absence = run_collection_persistence(
            session, settings, context, _result([]), board_config=_board()
        )
        assert second_absence.summary.closed == 2
        assert posting.is_active is False
        assert posting.status is PostingStatus.CLOSED
        assert posting.job is not None
        assert posting.job.status is JobStatus.CLOSED

        reappeared = run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )
        assert reappeared.summary.reopened == 1
        assert posting.is_active is True
        assert posting.missing_count == 0
        assert posting.job.status is JobStatus.RECOMMENDED


def test_partial_error_and_304_do_not_increment_absence(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context()

    with session_scope(settings) as session:
        run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )
        run_collection_persistence(
            session,
            settings,
            context,
            _result([], partial=True),
            board_config=_board(),
        )
        run_collection_persistence(
            session,
            settings,
            context,
            _result([], not_modified=True),
            board_config=_board(),
        )
        record_failed_collection(
            session,
            context,
            RuntimeError("falha controlada"),
            board_config=_board(),
            settings=settings,
        )

        posting = session.scalar(select(Posting).where(Posting.external_id == "1001"))
        assert posting is not None
        assert posting.is_active is True
        assert posting.missing_count == 0


def test_truncated_partial_snapshots_do_not_close_until_later_complete_snapshot(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context(close_after=1)

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001"), _posting("1002")]),
            board_config=_board(),
        )

        first_partial = run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001")], partial=True, complete_snapshot=False),
            board_config=_board(),
        )
        second_partial = run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001")], partial=True, complete_snapshot=False),
            board_config=_board(),
        )
        missing_from_partial = session.scalar(select(Posting).where(Posting.external_id == "1002"))
        assert missing_from_partial is not None
        assert first_partial.summary.closed == 0
        assert second_partial.summary.closed == 0
        assert missing_from_partial.is_active is True
        assert missing_from_partial.missing_count == 0

        complete = run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001")]),
            board_config=_board(),
        )
        assert complete.summary.closed == 1
        assert missing_from_partial.is_active is False


def test_greenhouse_boards_with_same_company_are_isolated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context_a = _context(board_key="same-company-gh-a", board_token="empresa-a", close_after=1)
    context_b = _context(board_key="same-company-gh-b", board_token="empresa-b", close_after=1)
    board_a = _board(key="same-company-gh-a", board_token="empresa-a")
    board_b = _board(key="same-company-gh-b", board_token="empresa-b")

    with session_scope(settings) as session:
        run_collection_persistence(
            session, settings, context_a, _result([_posting("a1")]), board_config=board_a
        )
        run_collection_persistence(
            session, settings, context_b, _result([_posting("b1")]), board_config=board_b
        )

        report = run_collection_persistence(
            session, settings, context_a, _result([]), board_config=board_a
        )

        posting_a = session.scalar(select(Posting).where(Posting.external_id == "a1"))
        posting_b = session.scalar(select(Posting).where(Posting.external_id == "b1"))
        db_board_a = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board_a.key))
        db_board_b = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board_b.key))
        assert posting_a is not None
        assert posting_b is not None
        assert db_board_a is not None
        assert db_board_b is not None
        assert report.summary.closed == 1
        assert posting_a.is_active is False
        assert posting_b.is_active is True
        assert posting_b.missing_count == 0
        assert db_board_a.collection_scope_key != db_board_b.collection_scope_key
        assert db_board_a.source_id != db_board_b.source_id


def test_lever_boards_with_same_company_are_isolated(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context_a = _context(
        collector="lever",
        board_key="same-company-lever-a",
        board_token="lever-a",
        close_after=1,
    )
    context_b = _context(
        collector="lever",
        board_key="same-company-lever-b",
        board_token="lever-b",
        close_after=1,
    )
    board_a = _board(key="same-company-lever-a", collector="lever", board_token="lever-a")
    board_b = _board(key="same-company-lever-b", collector="lever", board_token="lever-b")

    with session_scope(settings) as session:
        run_collection_persistence(
            session, settings, context_a, _result([_posting("la1")]), board_config=board_a
        )
        run_collection_persistence(
            session, settings, context_b, _result([_posting("lb1")]), board_config=board_b
        )
        run_collection_persistence(session, settings, context_a, _result([]), board_config=board_a)

        posting_a = session.scalar(select(Posting).where(Posting.external_id == "la1"))
        posting_b = session.scalar(select(Posting).where(Posting.external_id == "lb1"))
        assert posting_a is not None
        assert posting_b is not None
        assert posting_a.is_active is False
        assert posting_b.is_active is True
        assert posting_b.missing_count == 0


def test_company_rename_for_same_board_scope_does_not_duplicate(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    first_context = _context(company_name="Empresa Antiga")
    renamed_context = _context(company_name="Empresa Renomeada")

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            first_context,
            _result([_posting("1001", company="Empresa Antiga")]),
            board_config=_board(company_name="Empresa Antiga"),
        )
        run_collection_persistence(
            session,
            settings,
            renamed_context,
            _result([_posting("1001", company="Empresa Renomeada")]),
            board_config=_board(company_name="Empresa Renomeada"),
        )

        assert session.scalar(select(func.count(Posting.id))) == 1
        assert session.scalar(select(func.count(CompanyBoard.id))) == 1
        assert session.scalar(select(func.count(Source.id))) == 1


def test_absence_does_not_update_last_seen_at(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context(close_after=2)
    current_time = datetime(2026, 7, 1, 10, tzinfo=UTC)

    def fake_now() -> datetime:
        return current_time

    monkeypatch.setattr("radar_vagas.collection.orchestrator.utc_now", fake_now)

    with session_scope(settings) as session:
        run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )
        posting = session.scalar(select(Posting).where(Posting.external_id == "1001"))
        assert posting is not None
        first_seen = posting.last_seen_at

        current_time = datetime(2026, 7, 2, 10, tzinfo=UTC)
        run_collection_persistence(session, settings, context, _result([]), board_config=_board())

        assert posting.missing_count == 1
        assert posting.last_seen_at == first_seen
        board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == "empresa-greenhouse"))
        assert board is not None
        assert board.last_checked_at == current_time.replace(tzinfo=None)


def test_reopened_postings_are_re_evaluated_by_current_rules(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    cases = [
        ("recommended", _posting("r1"), JobStatus.RECOMMENDED),
        (
            "eligible",
            _posting(
                "e1",
                title="Trainee em Dados",
                employment_type="trainee",
                work_model="onsite",
                location="Belo Horizonte, MG",
                city="Belo Horizonte",
                state="MG",
                remote_country_scope=None,
                hours_per_day=6,
            ),
            JobStatus.ELIGIBLE,
        ),
        (
            "manual",
            _posting(
                "m1",
                work_model="remote",
                location="Remote",
                country=None,
                remote_country_scope=None,
            ),
            JobStatus.PENDING_REVIEW,
        ),
        (
            "ineligible",
            _posting(
                "i1",
                title="Junior Data Analyst",
                employment_type="junior",
                work_model="onsite",
                location="Sao Paulo, SP",
                city="Sao Paulo",
                state="SP",
                remote_country_scope=None,
            ),
            JobStatus.ARCHIVED,
        ),
    ]

    with session_scope(settings) as session:
        for suffix, posting, expected_status in cases:
            context = _context(
                board_key=f"reopen-{suffix}",
                board_token=f"reopen-{suffix}",
                close_after=1,
            )
            board = _board(key=f"reopen-{suffix}", board_token=f"reopen-{suffix}")
            run_collection_persistence(
                session, settings, context, _result([posting]), board_config=board
            )
            run_collection_persistence(session, settings, context, _result([]), board_config=board)
            reappeared = run_collection_persistence(
                session, settings, context, _result([posting]), board_config=board
            )

            job = session.scalar(
                select(Job).where(Job.postings.any(Posting.external_id == posting.external_id))
            )
            assert job is not None
            assert reappeared.summary.reopened == 1
            assert job.status is expected_status


def test_reopening_preserves_applied_and_dismissed_jobs_without_duplicate_decisions(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        applied_context = _context(
            board_key="preserve-applied",
            board_token="preserve-applied",
            close_after=1,
        )
        applied_board = _board(key="preserve-applied", board_token="preserve-applied")
        run_collection_persistence(
            session,
            settings,
            applied_context,
            _result([_posting("applied")]),
            board_config=applied_board,
        )
        applied_job = session.scalar(
            select(Job).where(Job.postings.any(Posting.external_id == "applied"))
        )
        assert applied_job is not None
        applied_job.status = JobStatus.APPLIED
        session.add(Application(job_id=applied_job.id, status=ApplicationStatus.SUBMITTED))
        applied_decision_count = session.scalar(select(func.count(Decision.id)))

        run_collection_persistence(
            session, settings, applied_context, _result([]), board_config=applied_board
        )
        run_collection_persistence(
            session,
            settings,
            applied_context,
            _result([_posting("applied")]),
            board_config=applied_board,
        )

        assert applied_job.status is JobStatus.APPLIED
        assert len(applied_job.applications) == 1
        assert session.scalar(select(func.count(Decision.id))) == applied_decision_count

        dismissed_context = _context(
            board_key="preserve-dismissed",
            board_token="preserve-dismissed",
            close_after=1,
        )
        dismissed_board = _board(key="preserve-dismissed", board_token="preserve-dismissed")
        run_collection_persistence(
            session,
            settings,
            dismissed_context,
            _result([_posting("dismissed")]),
            board_config=dismissed_board,
        )
        dismissed_job = session.scalar(
            select(Job).where(Job.postings.any(Posting.external_id == "dismissed"))
        )
        assert dismissed_job is not None
        dismissed_job.status = JobStatus.DISMISSED
        dismissed_decision_count = session.scalar(select(func.count(Decision.id)))

        run_collection_persistence(
            session, settings, dismissed_context, _result([]), board_config=dismissed_board
        )
        run_collection_persistence(
            session,
            settings,
            dismissed_context,
            _result([_posting("dismissed")]),
            board_config=dismissed_board,
        )

        assert dismissed_job.status is JobStatus.DISMISSED
        assert session.scalar(select(func.count(Decision.id))) == dismissed_decision_count


def test_existing_application_is_preserved_when_posting_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context()

    with session_scope(settings) as session:
        run_collection_persistence(
            session, settings, context, _result([_posting("1001")]), board_config=_board()
        )
        job = session.scalar(select(Job))
        assert job is not None
        job.status = JobStatus.APPLIED
        session.add(Application(job_id=job.id, status=ApplicationStatus.SUBMITTED))
        session.flush()

        report = run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001", title="Estagio em Dados Atualizado")]),
            board_config=_board(),
        )

        assert report.summary.changed == 1
        assert job.status is JobStatus.APPLIED
        assert job.canonical_title == "Estagio em Dados"


def test_board_cache_headers_are_persisted(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    context = _context()

    with session_scope(settings) as session:
        run_collection_persistence(
            session,
            settings,
            context,
            _result([_posting("1001")], etag='"abc"', last_modified="Wed, 01 Jul 2026 GMT"),
            board_config=_board(),
        )

        board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == "empresa-greenhouse"))
        assert board is not None
        assert board.last_etag == '"abc"'
        assert board.last_modified == "Wed, 01 Jul 2026 GMT"
        assert board.last_complete_snapshot_at is not None


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )


def _context(
    *,
    dry_run: bool = False,
    collector: str = "greenhouse",
    company_name: str = "Empresa Exemplo",
    board_key: str = "empresa-greenhouse",
    board_token: str = "empresa",
    close_after: int = 2,
) -> CollectionContext:
    context = build_collection_context(
        collector=collector,
        company_name=company_name,
        board_key=board_key,
        board_token=board_token,
        dry_run=dry_run,
    )
    return replace(
        context,
        collection_config=CollectionConfig(close_after_missing_successful_runs=close_after),
    )


def _result(
    items: list[ImportedPosting],
    *,
    partial: bool = False,
    complete_snapshot: bool = True,
    not_modified: bool = False,
    etag: str | None = None,
    last_modified: str | None = None,
) -> CollectionResult:
    return CollectionResult(
        collector="greenhouse",
        items=items,
        requests=1,
        bytes_received=100,
        complete_snapshot=complete_snapshot,
        partial=partial,
        not_modified=not_modified,
        status_code=304 if not_modified else 200,
        cache_etag=etag,
        cache_last_modified=last_modified,
    )


def _posting(
    external_id: str,
    *,
    title: str = "Estagio em Dados",
    company: str = "Empresa Exemplo",
    location: str = "Remote - Brazil",
    employment_type: str = "internship",
    work_model: str | WorkModel = "remote",
    country: str | None = "Brasil",
    state: str | None = None,
    city: str | None = None,
    remote_country_scope: str | None = "Brasil",
    hours_per_day: float | None = None,
) -> ImportedPosting:
    return ImportedPosting(
        source_name="Greenhouse: Empresa Exemplo",
        source_type="greenhouse",
        external_id=external_id,
        url=f"https://boards.greenhouse.io/empresa/jobs/{external_id}",
        title=title,
        company=company,
        location=location,
        description=f"{title}. Trabalhar com dados e Python.",
        employment_type=employment_type,
        work_model=work_model,
        country=country,
        state=state,
        city=city,
        remote_country_scope=remote_country_scope,
        hours_per_day=hours_per_day,
        application_url=f"https://boards.greenhouse.io/empresa/jobs/{external_id}",
    )


def _board(
    *,
    key: str = "empresa-greenhouse",
    company_name: str = "Empresa Exemplo",
    collector: str = "greenhouse",
    board_token: str = "empresa",
) -> BoardConfig:
    return BoardConfig(
        key=key,
        company_name=company_name,
        collector=collector,
        board_token=board_token,
        enabled=True,
    )
