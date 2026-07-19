from __future__ import annotations

import hashlib
import re
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar_vagas.applications.review import application_key_for_job
from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_title
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationStage,
    ApplicationStatus,
    CareerEventConfirmationStatus,
    CareerEventType,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    RelevanceStatus,
    ReviewState,
    WorkModel,
)
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    Application,
    CareerEvent,
    Company,
    Decision,
    Job,
    JobReviewState,
    Posting,
    ProfessionalProfileVersion,
    Source,
)
from radar_vagas.profile.service import import_professional_profile
from radar_vagas.web.app import create_app
from radar_vagas.web.server import validate_bind_host

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CREATE_CONNECTION = socket.create_connection


@pytest.fixture(autouse=True)
def allow_testclient_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    def guarded_connect(instance: socket.socket, address: object) -> object:
        if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1"}:
            return _ORIGINAL_CONNECT(instance, address)
        raise AssertionError("Testes nao podem acessar rede real.")

    def guarded_create_connection(
        address: object,
        *args: object,
        **kwargs: object,
    ) -> socket.socket:
        if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1"}:
            return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)
        raise AssertionError("Testes nao podem acessar rede real.")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


def test_web_onboarding_manual_profile_and_csrf(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)

    with TestClient(create_app(settings)) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/onboarding"

        page = client.get("/onboarding")
        assert page.status_code == 200
        assert "Content-Security-Policy" in page.headers
        assert "radar_csrf" in client.cookies
        assert "Primeiro acesso" in page.text

        denied = client.post(
            "/onboarding/profile/manual",
            data={"profile_name": "Perfil local", "skills": "SQL"},
        )
        assert denied.status_code == 403

        created = client.post(
            "/onboarding/profile/manual",
            data={
                "csrf_token": _csrf(page.text),
                "profile_name": "Perfil local",
                "headline": "Dados",
                "summary": "Perfil sintetico.",
                "skills": "SQL\nPython",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

        with session_scope(settings) as session:
            assert session.scalar(select(func.count(ProfessionalProfileVersion.id))) == 1
            assert (
                session.scalar(
                    select(func.count(ProfessionalProfileVersion.id)).where(
                        ProfessionalProfileVersion.is_active.is_(True)
                    )
                )
                == 1
            )
        assert (settings.config_dir / "ui.local.yaml").exists()


def test_web_jobs_filters_detail_actions_apply_and_xss(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        job = _create_job(
            session,
            title="<script>alert(1)</script> Estagio em Dados",
            provider_identity_key="gupy:web-100",
        )
        job_id = job.id

    with TestClient(create_app(settings)) as client:
        jobs_page = client.get("/jobs?q=Acme")
        assert jobs_page.status_code == 200
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in jobs_page.text
        assert "<script>alert(1)</script>" not in jobs_page.text

        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        token = _csrf(detail.text)

        assert (
            client.post(
                f"/jobs/{job_id}/seen",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.post(
                f"/jobs/{job_id}/shortlist",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.post(
                f"/jobs/{job_id}/compare",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )
        applied = client.post(
            f"/jobs/{job_id}/apply",
            data={
                "csrf_token": token,
                "applied_at": "2026-07-19T10:00",
                "platform": "gupy",
                "external_reference": "APP-WEB-100",
            },
            follow_redirects=False,
        )
        assert applied.status_code == 303

    with session_scope(settings) as session:
        persisted = session.get(Job, job_id)
        assert persisted is not None
        assert persisted.status is JobStatus.APPLIED
        assert persisted.review_state is not None
        assert persisted.review_state.state is ReviewState.APPLIED
        assert persisted.applications[0].stage is ApplicationStage.APPLIED


def test_web_applications_agenda_profile_and_sources(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        job = _create_job(session, provider_identity_key="gupy:web-200")
        application = Application(
            job_id=job.id,
            application_key=application_key_for_job(job),
            status=ApplicationStatus.SUBMITTED,
            stage=ApplicationStage.APPLIED,
            applied_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        )
        session.add(application)
        session.flush()
        job_id = job.id
        application_id = application.id

    with TestClient(create_app(settings)) as client:
        application_page = client.get(f"/applications/{application_id}")
        assert application_page.status_code == 200
        token = _csrf(application_page.text)
        event_response = client.post(
            f"/applications/{application_id}/events",
            data={
                "csrf_token": token,
                "event_type": ApplicationEventType.INTERVIEW_INVITED.value,
                "occurred_at": "2026-07-20T09:00",
            },
            follow_redirects=False,
        )
        assert event_response.status_code == 303

        agenda_page = client.get("/agenda")
        assert agenda_page.status_code == 200
        token = _csrf(agenda_page.text)
        created_event = client.post(
            "/agenda/events",
            data={
                "csrf_token": token,
                "event_type": CareerEventType.INTERVIEW.value,
                "title": "Entrevista web",
                "starts_at": "2026-07-21T10:00",
                "job_id": str(job_id),
                "application_id": str(application_id),
                "status": CareerEventConfirmationStatus.SUGGESTED.value,
            },
            follow_redirects=False,
        )
        assert created_event.status_code == 303

        with session_scope(settings) as session:
            event_id = session.scalar(select(CareerEvent.id))
            assert event_id is not None

        assert (
            client.post(
                f"/agenda/events/{event_id}/confirm",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert (
            client.post(
                f"/agenda/events/{event_id}/complete",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )

        profile = client.get("/profile")
        assert profile.status_code == 200
        token = _csrf(profile.text)
        batch = client.post(
            "/profile/batch-compare",
            data={"csrf_token": token, "limit": "10"},
            follow_redirects=False,
        )
        assert batch.status_code == 303

        sources = client.get("/sources")
        assert sources.status_code == 200
        token = _csrf(sources.text)
        collected = client.post(
            "/sources/collect-search-plan",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert collected.status_code == 303

    with session_scope(settings) as session:
        application = session.get(Application, application_id)
        assert application is not None
        assert application.stage is ApplicationStage.INTERVIEW_SCHEDULED
        event = session.scalar(select(CareerEvent))
        assert event is not None
        assert event.confirmation_status is CareerEventConfirmationStatus.COMPLETED
        assert event.completed_at is not None


def test_web_rejects_bad_upload_and_invalid_ids(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)

    with TestClient(create_app(settings)) as client:
        onboarding = client.get("/onboarding")
        token = _csrf(onboarding.text)
        rejected = client.post(
            "/onboarding/profile/upload",
            data={"csrf_token": token},
            files={"file": ("profile.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert rejected.status_code == 400
        assert client.get("/jobs/-1").status_code == 404
        assert client.get("/jobs/999").status_code == 404


def test_web_bind_host_allows_only_loopback() -> None:
    assert validate_bind_host("localhost") == "127.0.0.1"
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    try:
        validate_bind_host("0.0.0.0")
    except Exception as exc:
        assert "publico" in str(exc)
    else:
        raise AssertionError("host publico deveria ser rejeitado")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=tmp_path / "config",
    )


def _write_runtime_config(settings: Settings) -> None:
    run_migrations(settings)
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    (settings.config_dir / "profile.example.yaml").write_text(
        """
location:
  city: Belo Horizonte
  state: MG
  country: Brasil
institution: Universidade Exemplo
course: Engenharia de Software
preferences:
  accepted_employment_types:
    - INTERNSHIP
interest_areas:
  - dados
""",
        encoding="utf-8",
    )


def _create_active_profile(settings: Settings, tmp_path: Path) -> None:
    path = tmp_path / "professional-profile.yaml"
    path.write_text(
        """
profile_name: Perfil Web
summary: Perfil sintetico para testes.
skills:
  - name: SQL
    level: intermediario
    evidence:
      - title: Projeto SQL
        evidence_type: PROJECT
  - name: Python
    level: intermediario
    evidence:
      - title: Projeto Python
        evidence_type: PROJECT
experiences: []
projects: []
education: []
languages: []
""",
        encoding="utf-8",
    )
    with session_scope(settings) as session:
        import_professional_profile(session, path, activate=True)


def _create_job(
    session: Session,
    *,
    title: str = "Estagio em Dados",
    provider_identity_key: str,
) -> Job:
    source = session.scalar(select(Source).where(Source.slug == "web-tests"))
    if source is None:
        source = Source(
            name="Web Tests",
            slug="web-tests",
            source_type="gupy",
            base_url="https://jobs.gupy.io",
        )
        session.add(source)
        session.flush()
    normalized_company = normalize_company_name("Acme Dados")
    company = session.scalar(select(Company).where(Company.normalized_name == normalized_company))
    if company is None:
        company = Company(
            canonical_name="Acme Dados",
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
        description="Vaga sintetica com SQL e Python.",
        requirements="SQL\nPython",
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        application_url=application_url,
        status=JobStatus.RECOMMENDED,
        updated_at=datetime.now(UTC) + timedelta(seconds=int(external_id[-1:], 36)),
    )
    session.add(job)
    session.flush()
    posting = Posting(
        source_id=source.id,
        collection_scope_key="web-tests",
        provider="gupy",
        provider_scope="public",
        provider_external_id=external_id,
        provider_identity_key=provider_identity_key,
        external_id=external_id,
        original_url=application_url,
        normalized_url=application_url,
        raw_title=title,
        raw_company=company.canonical_name,
        raw_location="Remote - Brazil",
        raw_description=job.description,
        raw_requirements=job.requirements,
        content_hash=hashlib.sha256(provider_identity_key.encode("utf-8")).hexdigest(),
        job_id=job.id,
    )
    decision = Decision(
        job_id=job.id,
        eligibility_status=EligibilityStatus.ELIGIBLE,
        reason_code="TEST",
        reason_text="Elegivel no teste.",
        ranking_score=90,
        ranking_breakdown_json="{}",
        rules_version="test",
        relevance_status=RelevanceStatus.CORE,
        relevance_score=90,
        relevance_reason_json="{}",
        relevance_rules_version="test",
    )
    session.add_all(
        [posting, decision, JobReviewState(job_id=job.id, state=ReviewState.UNREVIEWED)]
    )
    session.flush()
    return job


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise AssertionError("token CSRF nao encontrado")
    return match.group(1)
