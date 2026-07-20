from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_title
from radar_vagas.config.schemas import GMAIL_READ_ONLY_SCOPE
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationStage,
    ApplicationStatus,
    EmploymentType,
    JobStatus,
    WorkModel,
)
from radar_vagas.gmail_insights.service import (
    classify_gmail_message,
    sync_gmail_application_insights,
)
from radar_vagas.gmail_insights.types import GmailMessage
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    Application,
    ApplicationEvent,
    CareerEvent,
    Company,
    EmailMessage,
    Job,
)


def test_gmail_sync_is_disabled_by_default_and_does_not_fetch(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        result = sync_gmail_application_insights(session, settings, client=_FailingClient())

        assert result.enabled is False
        assert result.fetched == 0
        assert session.scalar(select(func.count(EmailMessage.id))) == 0


def test_gmail_read_only_sync_stores_suggestion_without_mutating_application(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_gmail_config(settings)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session)
        application = Application(
            job_id=job.id,
            application_key="manual:app-5001",
            status=ApplicationStatus.SUBMITTED,
            stage=ApplicationStage.APPLIED,
            platform="gupy",
            external_reference="APP-5001",
        )
        session.add(application)
        session.flush()
        application_id = application.id

    messages = [
        GmailMessage(
            message_id="msg-1",
            thread_id="thread-1",
            sender="recrutamento@empresa.example",
            subject="Convite para entrevista APP-5001",
            received_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
            body=(
                "Ola. Temos uma entrevista para a candidatura APP-5001 "
                "da vaga Estagio em Dados na Acme Dados."
            ),
        )
    ]

    with session_scope(settings) as session:
        result = sync_gmail_application_insights(session, settings, client=_FakeClient(messages))

        assert result.enabled is True
        assert result.fetched == 1
        assert result.imported == 1
        assert result.linked == 1
        assert result.suggestions == 1

        stored = session.scalar(select(EmailMessage))
        assert stored is not None
        assert stored.application_id == application_id
        assert stored.provider == "gmail"
        assert stored.classified_event_type == ApplicationEventType.INTERVIEW_INVITED.value
        assert "entrevista" in stored.body_excerpt
        suggestion = json.loads(stored.suggestion_json)
        assert suggestion["requires_human_confirmation"] is True
        assert (
            suggestion["suggested_application_stage"] == ApplicationStage.INTERVIEW_SCHEDULED.value
        )
        assert suggestion["suggested_career_event_type"] == "INTERVIEW"

        application = session.get(Application, application_id)
        assert application is not None
        assert application.status is ApplicationStatus.SUBMITTED
        assert application.stage is ApplicationStage.APPLIED
        assert session.scalar(select(func.count(ApplicationEvent.id))) == 0
        assert session.scalar(select(func.count(CareerEvent.id))) == 0

    with session_scope(settings) as session:
        repeated = sync_gmail_application_insights(session, settings, client=_FakeClient(messages))
        assert repeated.imported == 0
        assert repeated.updated == 1
        assert session.scalar(select(func.count(EmailMessage.id))) == 1


@pytest.mark.parametrize(
    ("subject", "body", "event_type"),
    [
        ("Teste online", "acesse o assessment", ApplicationEventType.ASSESSMENT_INVITED),
        ("Entrevista", "vamos agendar uma conversa", ApplicationEventType.INTERVIEW_INVITED),
        ("Oferta", "segue carta proposta", ApplicationEventType.OFFER_RECEIVED),
        ("Atualizacao", "infelizmente nao seguiremos", ApplicationEventType.REJECTED),
    ],
)
def test_gmail_classifier_detects_application_events(
    subject: str,
    body: str,
    event_type: ApplicationEventType,
) -> None:
    message = GmailMessage(
        message_id="msg-classifier",
        thread_id=None,
        sender="rh@empresa.example",
        subject=subject,
        received_at=datetime(2026, 7, 20, tzinfo=UTC),
        body=body,
    )

    classification = classify_gmail_message(message)

    assert classification.event_type is event_type


class _FakeClient:
    def __init__(self, messages: Sequence[GmailMessage]) -> None:
        self._messages = list(messages)

    def search_messages(self, query: str, max_results: int) -> Sequence[GmailMessage]:
        _ = query
        return self._messages[:max_results]


class _FailingClient:
    def search_messages(self, query: str, max_results: int) -> Sequence[GmailMessage]:
        _ = query, max_results
        raise AssertionError("cliente Gmail nao deveria ser chamado")


def _create_job(session) -> Job:
    company = Company(
        canonical_name="Acme Dados",
        normalized_name=normalize_company_name("Acme Dados"),
    )
    session.add(company)
    session.flush()
    job = Job(
        company_id=company.id,
        canonical_title="Estagio em Dados",
        normalized_title=normalize_title("Estagio em Dados"),
        description="Vaga sintetica com SQL.",
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        status=JobStatus.APPLIED,
    )
    session.add(job)
    session.flush()
    return job


def _write_gmail_config(settings: Settings) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.config_dir / "gmail.local.yaml").write_text(
        "\n".join(
            [
                "enabled: true",
                "query: candidatura",
                "max_results: 5",
                "scopes:",
                f"  - {GMAIL_READ_ONLY_SCOPE}",
            ]
        ),
        encoding="utf-8",
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'radar.sqlite3'}",
        config_dir=tmp_path / "config",
    )
