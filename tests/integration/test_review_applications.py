from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar_vagas.applications.guard import ApplicationGuard
from radar_vagas.applications.history_import import (
    import_application_history,
    validate_application_history_file,
)
from radar_vagas.applications.review import (
    add_application_event,
    dismiss_job,
    mark_applied,
    mark_seen,
    restore_job,
    review_queue,
    shortlist_job,
)
from radar_vagas.canonicalization.normalize import (
    normalize_company_name,
    normalize_title,
    normalize_url,
)
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationGuardDecision,
    ApplicationMatchKind,
    ApplicationMatchStatus,
    ApplicationStage,
    ApplicationStatus,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    RelevanceStatus,
    ReviewEventType,
    ReviewState,
    WorkModel,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    Application,
    ApplicationEvent,
    ApplicationMatch,
    Company,
    Decision,
    Job,
    JobReviewEvent,
    Posting,
    Source,
)


def test_review_queue_manual_states_and_application_guard(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session, provider_identity_key="gupy:1001")
        _create_job(
            session,
            company_name="Empresa Dispensada",
            title="Estagio em BI",
            provider_identity_key="gupy:1002",
            status=JobStatus.DISMISSED,
        )
        session.flush()

        rows = review_queue(session)
        assert [row.job.id for row in rows] == [job.id]

        seen = mark_seen(session, job.id)
        assert seen.state is ReviewState.SEEN
        assert job.status is JobStatus.SEEN

        shortlisted = shortlist_job(session, job.id)
        assert shortlisted.state is ReviewState.SHORTLISTED

        dismissed = dismiss_job(session, job.id, reason_code="manual", notes="fora de foco")
        assert dismissed.state is ReviewState.DISMISSED
        assert job.status is JobStatus.DISMISSED
        assert review_queue(session) == []

        restored = restore_job(session, settings, job.id)
        assert restored.status in {JobStatus.ELIGIBLE, JobStatus.RECOMMENDED}
        assert restored.review_state is not None
        assert restored.review_state.state is ReviewState.UNREVIEWED

        application = mark_applied(
            session,
            settings,
            job.id,
            applied_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            platform="gupy",
            external_reference="APP-1001",
            notes="manual",
        )
        session.flush()

        guard = ApplicationGuard().evaluate(job)
        assert guard.decision is ApplicationGuardDecision.BLOCK_ALREADY_APPLIED
        assert application.status is ApplicationStatus.SUBMITTED
        assert application.stage is ApplicationStage.APPLIED
        assert job.status is JobStatus.APPLIED
        assert job.review_state is not None
        assert job.review_state.state is ReviewState.APPLIED
        assert session.scalar(select(func.count(JobReviewEvent.id))) == 5
        assert [event.event_type for event in application.events] == [
            ApplicationEventType.SUBMITTED
        ]


def test_application_history_import_dry_run_then_links_exact_match(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    history_path = tmp_path / "history.json"
    history_path.write_text(
        json.dumps(
            [
                {
                    "provider_identity_key": "gupy:2001",
                    "application_url": "https://jobs.gupy.io/job/2001",
                    "company": "Acme Dados",
                    "title": "Estagio em Dados",
                    "platform": "gupy",
                    "applied_at": "2026-07-18T15:00:00+00:00",
                    "status": "INTERVIEW",
                    "external_reference": "APP-2001",
                    "notes": "historico",
                }
            ]
        ),
        encoding="utf-8",
    )

    with session_scope(settings) as session:
        _create_job(session, provider_identity_key="gupy:2001")

    with session_scope(settings) as session:
        dry_run = import_application_history(session, settings, history_path, dry_run=True)
        assert dry_run.linked == 1
        assert dry_run.created_applications == 0
        assert session.scalar(select(func.count(Application.id))) == 0

    with session_scope(settings) as session:
        imported = import_application_history(session, settings, history_path, dry_run=False)
        assert imported.linked == 1
        assert imported.created_applications == 1
        application = session.scalar(select(Application))
        assert application is not None
        assert application.status is ApplicationStatus.INTERVIEW
        assert application.stage is ApplicationStage.INTERVIEW_SCHEDULED
        assert application.external_reference == "APP-2001"
        assert application.job.status is JobStatus.APPLIED
        assert [event.event_type for event in application.events] == [
            ApplicationEventType.SUBMITTED,
            ApplicationEventType.INTERVIEW_INVITED,
        ]
        match = session.scalar(select(ApplicationMatch))
        assert match is not None
        assert match.match_kind is ApplicationMatchKind.EXACT
        assert match.status is ApplicationMatchStatus.LINKED

        imported_again = import_application_history(session, settings, history_path, dry_run=False)
        assert imported_again.created_applications == 0
        assert imported_again.updated_applications == 0
        assert imported_again.created_matches == 0
        assert imported_again.unchanged == 1
        assert session.scalar(select(func.count(Application.id))) == 1
        assert session.scalar(select(func.count(ApplicationEvent.id))) == 2
        assert session.scalar(select(func.count(ApplicationMatch.id))) == 1


def test_application_history_validation_rejects_rows_without_identity(tmp_path: Path) -> None:
    history_path = tmp_path / "history.csv"
    history_path.write_text(
        "provider_identity_key,application_url,company,title,platform,applied_at,status,"
        "external_reference,notes\n"
        ",,,,,,SUBMITTED,,sem identidade\n",
        encoding="utf-8",
    )

    result = validate_application_history_file(history_path)

    assert result.total == 1
    assert result.invalid == 1
    assert "empresa+titulo" in result.items[0].errors[0]


def test_review_transitions_block_invalid_states_and_keep_events_idempotent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        dismissed = _create_job(session, provider_identity_key="gupy:3001")
        dismiss_job(session, dismissed.id, reason_code="manual", notes="fora de foco")
        dismiss_job(session, dismissed.id, reason_code="manual", notes="fora de foco")
        assert session.scalar(select(func.count(JobReviewEvent.id))) == 1
        assert dismissed.review_state is not None
        assert dismissed.review_state.reason_code == "manual"

        with pytest.raises(RadarError, match="restore-job"):
            mark_seen(session, dismissed.id)
        with pytest.raises(RadarError, match="restore-job"):
            shortlist_job(session, dismissed.id)
        with pytest.raises(RadarError, match="Restaure"):
            mark_applied(session, settings, dismissed.id)

        restored = restore_job(session, settings, dismissed.id)
        assert restored.review_state is not None
        assert restored.review_state.state is ReviewState.UNREVIEWED
        assert restored.review_state.reason_code is None
        assert restored.review_state.notes is None

        mark_applied(session, settings, restored.id)
        with pytest.raises(RadarError, match="aplicada"):
            mark_seen(session, restored.id)
        with pytest.raises(RadarError, match="aplicada"):
            shortlist_job(session, restored.id)
        with pytest.raises(RadarError, match="aplicada"):
            dismiss_job(session, restored.id)
        with pytest.raises(RadarError, match="aplicada"):
            restore_job(session, settings, restored.id)

        closed = _create_job(
            session,
            provider_identity_key="gupy:3002",
            status=JobStatus.CLOSED,
        )
        with pytest.raises(RadarError, match="fechada"):
            mark_seen(session, closed.id)


def test_shortlist_can_return_to_seen_without_contradicting_job_status(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session, provider_identity_key="gupy:3003")
        shortlist_job(session, job.id)
        state = mark_seen(session, job.id)

        assert state.state is ReviewState.SEEN
        assert job.status is JobStatus.SEEN
        assert [event.event_type for event in job.review_events] == [
            ReviewEventType.SHORTLISTED,
            ReviewEventType.SEEN,
        ]


def test_application_timeline_reducer_ignores_old_and_informative_regressions(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session, provider_identity_key="gupy:3004")
        application = mark_applied(
            session,
            settings,
            job.id,
            applied_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        )
        add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.ASSESSMENT_COMPLETED,
            occurred_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
            event_key="external:assessment-completed",
        )
        add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.CONFIRMATION_RECEIVED,
            occurred_at=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
            event_key="external:confirmation-old",
        )
        repeated = add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.ASSESSMENT_COMPLETED,
            occurred_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
            event_key="external:assessment-completed",
        )
        assert repeated.event_key == "external:assessment-completed"
        assert application.stage is ApplicationStage.ASSESSMENT_COMPLETED
        assert application.status is ApplicationStatus.TEST
        assert session.scalar(select(func.count(ApplicationEvent.id))) == 3

        add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.INTERVIEW_COMPLETED,
            occurred_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
            event_key="external:interview-completed",
        )
        add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.INTERVIEW_INVITED,
            occurred_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            event_key="external:interview-invited-old",
        )
        assert application.stage is ApplicationStage.INTERVIEW_COMPLETED

        add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.REJECTED,
            occurred_at=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
            event_key="external:rejected",
        )
        assert application.stage is ApplicationStage.REJECTED
        add_application_event(
            session,
            application.id,
            event_type=ApplicationEventType.INTERVIEW_INVITED,
            occurred_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
            event_key="external:new-interview",
        )
        assert application.stage is ApplicationStage.INTERVIEW_SCHEDULED


def test_application_history_import_dedupes_csv_rows_and_preserves_dry_run_session(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    history_path = tmp_path / "history.csv"
    history_path.write_text(
        "provider_identity_key,application_url,company,title,platform,applied_at,status,"
        "external_reference,notes\n"
        "gupy:4001,https://jobs.gupy.io/job/4001,Acme Dados,Estagio em Dados,gupy,"
        "2026-07-18T15:00:00+00:00,INTERVIEW,APP-4001,historico\n"
        "gupy:4001,https://jobs.gupy.io/job/4001,Acme Dados,Estagio em Dados,gupy,"
        "2026-07-18T15:00:00+00:00,INTERVIEW,APP-4001,historico\n",
        encoding="utf-8",
    )

    with session_scope(settings) as session:
        _create_job(session, provider_identity_key="gupy:4001")
        dry_run = import_application_history(session, settings, history_path, dry_run=True)
        assert dry_run.unchanged == 1
        assert session.scalar(select(func.count(Job.id))) == 1
        assert session.scalar(select(func.count(Application.id))) == 0

    with session_scope(settings) as session:
        first = import_application_history(session, settings, history_path, dry_run=False)
        second = import_application_history(session, settings, history_path, dry_run=False)
        assert first.created_applications == 1
        assert first.unchanged == 1
        assert second.created_applications == 0
        assert second.updated_applications == 0
        assert second.created_matches == 0
        assert second.unchanged == 2
        assert session.scalar(select(func.count(Application.id))) == 1
        assert session.scalar(select(func.count(ApplicationEvent.id))) == 2
        assert session.scalar(select(func.count(ApplicationMatch.id))) == 1


def test_application_history_import_dedupes_probable_conflict_and_unmatched(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    probable_path = tmp_path / "probable.json"
    probable_path.write_text(
        json.dumps(
            [
                {
                    "company": "Acme Dados",
                    "title": "Estagio em Dados",
                    "status": "SUBMITTED",
                    "applied_at": "2026-07-18T15:00:00+00:00",
                },
                {
                    "company": "Empresa Ausente",
                    "title": "Estagio em Dados",
                    "status": "SUBMITTED",
                    "applied_at": "2026-07-18T15:00:00+00:00",
                },
            ]
        ),
        encoding="utf-8",
    )
    conflict_path = tmp_path / "conflict.json"
    conflict_path.write_text(
        json.dumps(
            [
                {
                    "company": "Conflito Dados",
                    "title": "Estagio em Dados",
                    "status": "SUBMITTED",
                    "applied_at": "2026-07-18T15:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    with session_scope(settings) as session:
        _create_job(session, provider_identity_key="gupy:5001")
        _create_job(session, company_name="Conflito Dados", provider_identity_key="gupy:5002")
        _create_job(session, company_name="Conflito Dados", provider_identity_key="gupy:5003")
        first = import_application_history(session, settings, probable_path, dry_run=False)
        second = import_application_history(session, settings, probable_path, dry_run=False)
        conflict_first = import_application_history(session, settings, conflict_path, dry_run=False)
        conflict_second = import_application_history(
            session,
            settings,
            conflict_path,
            dry_run=False,
        )

        assert first.probable == 1
        assert first.unmatched == 1
        assert first.needs_review == 2
        assert second.created_matches == 0
        assert second.unchanged == 2
        assert conflict_first.conflicts == 1
        assert conflict_second.created_matches == 0
        assert conflict_second.unchanged == 1
        assert session.scalar(select(func.count(ApplicationMatch.id))) == 3


def test_application_history_rejects_unsupported_status_and_reduces_out_of_order(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(
        json.dumps([{"provider_identity_key": "gupy:6001", "status": "READY"}]),
        encoding="utf-8",
    )
    out_of_order_path = tmp_path / "out-of-order.json"
    out_of_order_path.write_text(
        json.dumps(
            [
                {
                    "provider_identity_key": "gupy:6001",
                    "status": "INTERVIEW",
                    "applied_at": "2026-07-20T15:00:00+00:00",
                },
                {
                    "provider_identity_key": "gupy:6001",
                    "status": "SUBMITTED",
                    "applied_at": "2026-07-18T15:00:00+00:00",
                },
            ]
        ),
        encoding="utf-8",
    )

    invalid = validate_application_history_file(invalid_path)
    assert invalid.invalid == 1
    assert "nao suportado" in invalid.items[0].errors[0]

    with session_scope(settings) as session:
        _create_job(session, provider_identity_key="gupy:6001")
        imported = import_application_history(session, settings, out_of_order_path, dry_run=False)
        application = session.scalar(select(Application))
        assert imported.created_applications == 1
        assert application is not None
        assert application.stage is ApplicationStage.INTERVIEW_SCHEDULED


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )


def _create_job(
    session,
    *,
    company_name: str = "Acme Dados",
    title: str = "Estagio em Dados",
    provider_identity_key: str,
    status: JobStatus = JobStatus.RECOMMENDED,
) -> Job:
    source = session.scalar(select(Source).where(Source.slug == "gupy-tests"))
    if source is None:
        source = Source(
            name="Gupy Tests",
            slug="gupy-tests",
            source_type="gupy",
            base_url="https://jobs.gupy.io",
        )
        session.add(source)
        session.flush()

    normalized_company = normalize_company_name(company_name)
    company = session.scalar(select(Company).where(Company.normalized_name == normalized_company))
    if company is None:
        company = Company(
            canonical_name=company_name,
            normalized_name=normalized_company,
        )
        session.add(company)
        session.flush()

    external_id = provider_identity_key.split(":", 1)[1]
    application_url = f"https://jobs.gupy.io/job/{external_id}"
    job = Job(
        company_id=company.id,
        canonical_title=title,
        normalized_title=normalize_title(title),
        description="Estagio em dados com SQL, Python e dashboards.",
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        hours_per_day=6,
        application_url=application_url,
        status=status,
    )
    session.add(job)
    session.flush()

    posting = Posting(
        source_id=source.id,
        collection_scope_key="gupy-tests",
        provider="gupy",
        provider_scope="public",
        provider_external_id=external_id,
        provider_identity_key=provider_identity_key,
        external_id=external_id,
        original_url=application_url,
        normalized_url=normalize_url(application_url),
        raw_title=title,
        raw_company=company_name,
        raw_location="Remote - Brazil",
        raw_description=job.description,
        content_hash=hashlib.sha256(provider_identity_key.encode("utf-8")).hexdigest(),
        job_id=job.id,
    )
    session.add(posting)
    session.add(
        Decision(
            job_id=job.id,
            eligibility_status=EligibilityStatus.ELIGIBLE,
            reason_code="TEST_ELIGIBLE",
            reason_text="Elegivel para o teste.",
            ranking_score=90,
            ranking_breakdown_json="{}",
            rules_version="test",
            relevance_status=RelevanceStatus.CORE,
            relevance_score=90,
            relevance_reason_json="{}",
            relevance_rules_version="test",
        )
    )
    session.flush()
    return job
