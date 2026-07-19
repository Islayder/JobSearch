from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar_vagas.calendar.service import (
    cancel_event,
    complete_event,
    confirm_event,
    create_event,
    list_events_by_application,
    list_events_by_job,
    list_upcoming_events,
    update_event,
)
from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_title
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import (
    ApplicationStage,
    ApplicationStatus,
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
    EmploymentType,
    JobStatus,
    WorkModel,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import Application, CareerEvent, CareerEventAudit, Company, Job


def test_create_manual_event_defaults_confirmed_and_event_key_is_idempotent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session)
        first = create_event(
            session,
            job_id=job.id,
            event_key="manual:interview:1",
            event_type=CareerEventType.INTERVIEW,
            title="Entrevista tecnica",
            starts_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            ends_at=datetime(2026, 7, 21, 11, 0, tzinfo=UTC),
            timezone="America/Sao_Paulo",
        )
        repeated = create_event(
            session,
            job_id=job.id,
            event_key="manual:interview:1",
            event_type=CareerEventType.INTERVIEW,
            title="Entrevista tecnica",
            starts_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            ends_at=datetime(2026, 7, 21, 11, 0, tzinfo=UTC),
            timezone="America/Sao_Paulo",
        )

        assert repeated.id == first.id
        assert repeated.title == "Entrevista tecnica"
        assert first.confirmation_status is CareerEventConfirmationStatus.CONFIRMED
        assert session.scalar(select(func.count(CareerEvent.id))) == 1
        assert session.scalar(select(func.count(CareerEventAudit.id))) == 1


def test_calendar_event_validation_blocks_unsafe_or_inconsistent_data(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        first_job = _create_job(session)
        second_job = _create_job(session, title="Estagio em BI")
        application = Application(
            job_id=first_job.id,
            application_key="job:first",
            status=ApplicationStatus.SUBMITTED,
            stage=ApplicationStage.APPLIED,
        )
        session.add(application)
        session.flush()

        with pytest.raises(RadarError, match="timezone"):
            create_event(
                session,
                event_type=CareerEventType.INTERVIEW,
                title="Sem timezone",
                starts_at=datetime(2026, 7, 21, 10, 0),
            )
        with pytest.raises(RadarError, match="timezone invalido"):
            create_event(
                session,
                event_type=CareerEventType.INTERVIEW,
                title="Timezone invalido",
                timezone="Nao/Existe",
            )
        with pytest.raises(RadarError, match="ends_at"):
            create_event(
                session,
                event_type=CareerEventType.INTERVIEW,
                title="Fim invalido",
                starts_at=datetime(2026, 7, 21, 11, 0, tzinfo=UTC),
                ends_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            )
        with pytest.raises(RadarError, match="host local"):
            create_event(
                session,
                event_type=CareerEventType.INTERVIEW,
                title="URL local",
                meeting_url="https://localhost/meet",
            )
        with pytest.raises(RadarError, match="estimado"):
            create_event(
                session,
                event_type=CareerEventType.FOLLOW_UP,
                title="Estimado confirmado",
                source=CareerEventSource.ESTIMATED,
                confirmation_status=CareerEventConfirmationStatus.CONFIRMED,
            )
        with pytest.raises(RadarError, match="mesma vaga"):
            create_event(
                session,
                event_type=CareerEventType.INTERVIEW,
                title="Job errado",
                job_id=second_job.id,
                application_id=application.id,
            )


def test_event_key_reuse_requires_canonical_identity(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session)
        create_event(
            session,
            job_id=job.id,
            event_key="email:interview:fixed",
            event_type=CareerEventType.INTERVIEW,
            title="Entrevista tecnica",
            starts_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
            timezone="UTC",
            source=CareerEventSource.EMAIL,
        )

        with pytest.raises(RadarError, match="event_key"):
            create_event(
                session,
                job_id=job.id,
                event_key="email:interview:fixed",
                event_type=CareerEventType.INTERVIEW,
                title="Entrevista com RH",
                starts_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
                timezone="UTC",
                source=CareerEventSource.EMAIL,
            )


def test_calendar_update_lifecycle_lists_and_audits_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    now = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    with session_scope(settings) as session:
        job = _create_job(session)
        application = Application(
            job_id=job.id,
            application_key="job:calendar",
            status=ApplicationStatus.SUBMITTED,
            stage=ApplicationStage.APPLIED,
        )
        session.add(application)
        session.flush()
        interview = create_event(
            session,
            application_id=application.id,
            event_type=CareerEventType.INTERVIEW,
            title="Entrevista",
            starts_at=now + timedelta(days=2),
            timezone="UTC",
        )
        deadline = create_event(
            session,
            job_id=job.id,
            event_type=CareerEventType.ASSESSMENT_DEADLINE,
            title="Prazo teste",
            starts_at=now + timedelta(days=5),
            timezone="UTC",
        )

        update_event(session, interview.id, title="Entrevista tecnica")
        complete_event(session, interview.id)
        cancel_event(session, deadline.id)

        assert interview.confirmation_status is CareerEventConfirmationStatus.COMPLETED
        assert deadline.confirmation_status is CareerEventConfirmationStatus.CANCELLED
        assert list_upcoming_events(session, days=30, now=now) == []
        assert [event.id for event in list_events_by_job(session, job.id)] == [
            interview.id,
            deadline.id,
        ]
        assert [event.id for event in list_events_by_application(session, application.id)] == [
            interview.id
        ]
        assert session.scalar(select(func.count(CareerEventAudit.id))) == 5


def test_calendar_lifecycle_blocks_invalid_terminal_transitions_and_is_idempotent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        suggested = create_event(
            session,
            event_type=CareerEventType.ASSESSMENT,
            title="Teste sugerido",
            source=CareerEventSource.JOB_DESCRIPTION,
        )
        with pytest.raises(RadarError, match="Transicao"):
            complete_event(session, suggested.id)

        confirm_event(session, suggested.id)
        confirm_event(session, suggested.id)
        complete_event(session, suggested.id)
        completed_at = suggested.completed_at
        complete_event(session, suggested.id)
        assert suggested.completed_at == completed_at
        assert suggested.cancelled_at is None

        with pytest.raises(RadarError, match="Transicao"):
            cancel_event(session, suggested.id)
        with pytest.raises(RadarError, match="terminal"):
            update_event(session, suggested.id, title="Outro titulo")
        assert session.scalar(select(func.count(CareerEventAudit.id))) == 3


def test_non_manual_event_starts_suggested_and_can_be_confirmed_except_estimated(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        suggested = create_event(
            session,
            event_type=CareerEventType.ASSESSMENT,
            title="Teste extraido",
            source=CareerEventSource.JOB_DESCRIPTION,
        )
        assert suggested.confirmation_status is CareerEventConfirmationStatus.SUGGESTED

        confirm_event(session, suggested.id)
        assert suggested.confirmation_status is CareerEventConfirmationStatus.CONFIRMED

        estimated = create_event(
            session,
            event_type=CareerEventType.FOLLOW_UP,
            title="Follow-up estimado",
            source=CareerEventSource.ESTIMATED,
        )
        with pytest.raises(RadarError, match="estimado"):
            confirm_event(session, estimated.id)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )


def _create_job(
    session,
    *,
    title: str = "Estagio em Dados",
) -> Job:
    normalized_company = normalize_company_name("Acme Dados")
    company = session.scalar(select(Company).where(Company.normalized_name == normalized_company))
    if company is None:
        company = Company(
            canonical_name="Acme Dados",
            normalized_name=normalized_company,
        )
        session.add(company)
        session.flush()
    job = Job(
        company_id=company.id,
        canonical_title=title,
        normalized_title=normalize_title(title),
        description="Vaga sintetica.",
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        status=JobStatus.RECOMMENDED,
    )
    session.add(job)
    session.flush()
    return job
