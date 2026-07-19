from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from radar_vagas.collection.contracts import CollectionContext, CollectionResult
from radar_vagas.collection.orchestrator import record_failed_collection, run_collection_persistence
from radar_vagas.config.schemas import BoardConfig, CollectionConfig
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import ApplicationStatus, JobStatus, PostingStatus
from radar_vagas.ingestion.import_schema import ImportedPosting
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import Application, CompanyBoard, Job, Posting, PostingRevision


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
        assert posting.job.status is JobStatus.NEW


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


def _context(*, dry_run: bool = False) -> CollectionContext:
    return CollectionContext(
        collector="greenhouse",
        source_name="Greenhouse: Empresa Exemplo",
        source_type="greenhouse",
        company_name="Empresa Exemplo",
        board_key="empresa-greenhouse",
        board_token="empresa",
        dry_run=dry_run,
        collection_config=CollectionConfig(close_after_missing_successful_runs=2),
    )


def _result(
    items: list[ImportedPosting],
    *,
    partial: bool = False,
    not_modified: bool = False,
    etag: str | None = None,
    last_modified: str | None = None,
) -> CollectionResult:
    return CollectionResult(
        collector="greenhouse",
        items=items,
        requests=1,
        bytes_received=100,
        complete_snapshot=True,
        partial=partial,
        not_modified=not_modified,
        status_code=304 if not_modified else 200,
        cache_etag=etag,
        cache_last_modified=last_modified,
    )


def _posting(external_id: str, *, title: str = "Estagio em Dados") -> ImportedPosting:
    return ImportedPosting(
        source_name="Greenhouse: Empresa Exemplo",
        source_type="greenhouse",
        external_id=external_id,
        url=f"https://boards.greenhouse.io/empresa/jobs/{external_id}",
        title=title,
        company="Empresa Exemplo",
        location="Remote - Brazil",
        description=f"{title}. Trabalhar com dados e Python.",
        employment_type="internship",
        work_model="remote",
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
