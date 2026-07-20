from __future__ import annotations

import hashlib
import re
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

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
    CareerEventSource,
    CareerEventType,
    CompanyInformationSourceType,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    RelevanceStatus,
    RequirementMatchStatus,
    ReviewState,
    SourceRunStatus,
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
    JobProfileComparison,
    JobReviewState,
    Posting,
    ProfessionalProfileVersion,
    Source,
    SourceRun,
)
from radar_vagas.profile.service import compare_job_to_profile, import_professional_profile
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


def test_web_app_shell_navigation_dashboard_and_job_cards(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        _create_job(
            session,
            title="Estagio Shell Design",
            provider_identity_key="gupy:web-120",
        )

    with TestClient(create_app(settings)) as client:
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert 'class="app-shell"' in dashboard.text
        assert '<aside class="sidebar"' in dashboard.text
        assert 'class="app-header"' in dashboard.text
        assert '<main class="app-main"' in dashboard.text
        assert '<footer class="app-footer"' in dashboard.text
        assert "Ir para o conteudo" in dashboard.text
        assert "data-sidebar-toggle" in dashboard.text
        assert 'aria-controls="app-sidebar"' in dashboard.text
        assert "data-theme-toggle" in dashboard.text
        assert 'name="q" placeholder="Pesquisar vagas ou empresas"' in dashboard.text
        assert 'href="/" aria-current="page"' in dashboard.text
        assert "O que precisa da sua atencao" in dashboard.text
        assert "Proximos compromissos" in dashboard.text
        assert "Saude das fontes" in dashboard.text

        jobs = client.get("/jobs?q=Acme")
        assert jobs.status_code == 200
        assert 'href="/jobs" aria-current="page"' in jobs.text
        assert 'id="job-filters"' in jobs.text
        assert "Filtros avancados" in jobs.text
        assert "filter-chip" in jobs.text
        assert "job-card" in jobs.text
        assert "Estagio Shell Design" in jobs.text
        assert "Ver detalhes" in jobs.text


def test_web_not_found_uses_redesigned_error_shell(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)

    with TestClient(create_app(settings)) as client:
        response = client.get("/rota-inexistente-5b4")

    assert response.status_code == 404
    assert 'class="app-shell"' in response.text
    assert '<aside class="sidebar"' in response.text
    assert 'class="app-header"' in response.text
    assert '<main class="app-main"' in response.text
    assert '<footer class="app-footer"' in response.text
    assert "Algo impediu a acao" in response.text
    assert "Not Found" in response.text


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


def test_web_job_detail_company_intelligence_and_interview_preparation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        job = _create_job(
            session,
            title="Estagio Produto Dados",
            provider_identity_key="gupy:web-130",
        )
        job.technologies_json = '["SQL", "Power BI"]'
        job_id = job.id

    with TestClient(create_app(settings)) as client:
        detail = client.get(f"/jobs/{job_id}")
        assert detail.status_code == 200
        token = _csrf(detail.text)

        denied = client.post(
            f"/jobs/{job_id}/company/facts",
            data={
                "category": "Produto",
                "content": "Plataforma oficial de analytics.",
            },
        )
        assert denied.status_code == 403

        profile = client.post(
            f"/jobs/{job_id}/company/profile",
            data={
                "csrf_token": token,
                "name": "Acme Dados",
                "official_website": "https://empresa.example",
                "industry": "Tecnologia",
                "company_size": "100-500",
                "location": "Brasil",
                "description": "Produto oficial de analytics.",
                "sources": "https://empresa.example/sobre",
            },
            follow_redirects=False,
        )
        assert profile.status_code == 303

        official_fact = client.post(
            f"/jobs/{job_id}/company/facts",
            data={
                "csrf_token": token,
                "category": "Produto",
                "content": "Plataforma oficial de analytics.",
                "origin_type": CompanyInformationSourceType.OFFICIAL_INFO.value,
                "source_url": "https://empresa.example/produto",
                "source_date": "2026",
            },
            follow_redirects=False,
        )
        assert official_fact.status_code == 303

        note = client.post(
            f"/jobs/{job_id}/company/facts",
            data={
                "csrf_token": token,
                "category": "Nota",
                "content": "Perguntar sobre rituais da equipe.",
                "origin_type": CompanyInformationSourceType.USER_NOTE.value,
            },
            follow_redirects=False,
        )
        assert note.status_code == 303

        reviews = client.post(
            f"/jobs/{job_id}/company/reviews",
            data={
                "csrf_token": token,
                "platform": "Portal Ficticio",
                "overall_rating": "4,1",
                "review_count": "12",
                "positives": "Aprendizado\nMentoria",
                "negatives": "Processos em maturacao",
                "period": "2026",
                "source_url": "https://reviews.example/acme",
            },
            follow_redirects=False,
        )
        assert reviews.status_code == 303

        preparation = client.post(
            f"/jobs/{job_id}/interview-preparation",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert preparation.status_code == 303

        refreshed = client.get(f"/jobs/{job_id}")
        assert refreshed.status_code == 200
        assert "Informacao oficial" in refreshed.text
        assert "Anotacao do usuario" in refreshed.text
        assert "Relato de funcionarios" in refreshed.text
        assert "Preparacao de entrevista" in refreshed.text
        assert "Plataforma oficial de analytics." in refreshed.text
        assert "Como voce explicaria sua experiencia com SQL" in refreshed.text


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


def test_web_application_date_filters_include_full_local_day(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        _create_application(
            session,
            title="Aplicacao Antes Local",
            provider_identity_key="gupy:web-801",
            applied_at=datetime(2026, 7, 20, 2, 59, tzinfo=UTC),
        )
        _create_application(
            session,
            title="Aplicacao Inicio Local",
            provider_identity_key="gupy:web-802",
            applied_at=datetime(2026, 7, 20, 3, 0, tzinfo=UTC),
        )
        _create_application(
            session,
            title="Aplicacao Meio Local",
            provider_identity_key="gupy:web-803",
            applied_at=datetime(2026, 7, 20, 17, 0, tzinfo=UTC),
        )
        _create_application(
            session,
            title="Aplicacao Fim Local",
            provider_identity_key="gupy:web-804",
            applied_at=datetime(2026, 7, 21, 2, 59, tzinfo=UTC),
        )
        _create_application(
            session,
            title="Aplicacao Depois Local",
            provider_identity_key="gupy:web-805",
            applied_at=datetime(2026, 7, 21, 3, 0, tzinfo=UTC),
        )
        _create_application(
            session,
            title="Aplicacao Sem Data",
            provider_identity_key="gupy:web-806",
            applied_at=None,
        )

    with TestClient(create_app(settings)) as client:
        page = client.get("/applications?from_date=2026-07-20&to_date=2026-07-20")
        assert page.status_code == 200
        assert "Aplicacao Antes Local" not in page.text
        assert "Aplicacao Inicio Local" in page.text
        assert "Aplicacao Meio Local" in page.text
        assert "Aplicacao Fim Local" in page.text
        assert "Aplicacao Depois Local" not in page.text
        assert "Aplicacao Sem Data" not in page.text


def test_web_application_date_filters_support_utc_and_reject_inverted_period(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    (settings.config_dir / "ui.local.yaml").write_text("timezone: UTC\n", encoding="utf-8")
    with session_scope(settings) as session:
        _create_application(
            session,
            title="Aplicacao UTC Dentro",
            provider_identity_key="gupy:web-811",
            applied_at=datetime(2026, 7, 20, 23, 59, tzinfo=UTC),
        )
        _create_application(
            session,
            title="Aplicacao UTC Fora",
            provider_identity_key="gupy:web-812",
            applied_at=datetime(2026, 7, 21, 0, 0, tzinfo=UTC),
        )

    with TestClient(create_app(settings)) as client:
        page = client.get("/applications?from_date=2026-07-20&to_date=2026-07-20")
        assert page.status_code == 200
        assert "Aplicacao UTC Dentro" in page.text
        assert "Aplicacao UTC Fora" not in page.text

        invalid = client.get("/applications?from_date=2026-07-21&to_date=2026-07-20")
        assert invalid.status_code == 400
        assert "Periodo inicial nao pode ser posterior ao final" in invalid.text


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


def test_web_manual_profile_textarea_skills_have_no_implicit_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)

    with TestClient(create_app(settings)) as client:
        profile = client.get("/profile")
        created = client.post(
            "/profile/manual",
            data={
                "csrf_token": _csrf(profile.text),
                "profile_name": "Perfil textarea",
                "skills": "SQL\nPython",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

    with session_scope(settings) as session:
        version = session.scalar(
            select(ProfessionalProfileVersion).order_by(ProfessionalProfileVersion.id.desc())
        )
        assert version is not None
        assert [skill.name for skill in sorted(version.skills, key=lambda skill: skill.id)] == [
            "SQL",
            "Python",
        ]
        assert version.evidences == []


def test_web_manual_profile_structured_skills_keep_own_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)

    with TestClient(create_app(settings)) as client:
        profile = client.get("/profile")
        created = client.post(
            "/profile/manual",
            data={
                "csrf_token": _csrf(profile.text),
                "profile_name": "Perfil estruturado",
                "skills": "",
                "skill_name": ["SQL", "AWS"],
                "skill_category": ["Dados", "Cloud"],
                "skill_level": ["intermediario", "basico"],
                "skill_evidence_title": ["Projeto SQL", ""],
                "skill_evidence_description": ["Consultas analiticas", ""],
                "skill_evidence_source": ["portfolio-sql", ""],
                "skill_evidence_type": ["PROJECT", "PROJECT"],
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

    with session_scope(settings) as session:
        version = session.scalar(
            select(ProfessionalProfileVersion).order_by(ProfessionalProfileVersion.id.desc())
        )
        assert version is not None
        skills = {skill.name: skill for skill in version.skills}
        assert set(skills) == {"SQL", "AWS"}
        assert skills["SQL"].category == "Dados"
        assert skills["SQL"].level == "intermediario"
        assert skills["AWS"].category == "Cloud"
        assert skills["AWS"].level == "basico"
        assert [evidence.title for evidence in skills["SQL"].evidences] == ["Projeto SQL"]
        assert skills["AWS"].evidences == []


def test_web_manual_profile_mixed_skills_merge_after_structured_binding(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)

    with TestClient(create_app(settings)) as client:
        profile = client.get("/profile")
        created = client.post(
            "/profile/manual",
            data={
                "csrf_token": _csrf(profile.text),
                "profile_name": "Perfil misto",
                "skills": "SQL\nPython",
                "skill_name": ["AWS", "Python"],
                "skill_category": ["Cloud", "Dados"],
                "skill_level": ["intermediario", "avancado"],
                "skill_evidence_title": ["Projeto AWS", ""],
                "skill_evidence_description": ["Infraestrutura de dados", ""],
                "skill_evidence_source": ["portfolio-aws", ""],
                "skill_evidence_type": ["PROJECT", "PROJECT"],
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

    with session_scope(settings) as session:
        version = session.scalar(
            select(ProfessionalProfileVersion).order_by(ProfessionalProfileVersion.id.desc())
        )
        assert version is not None
        ordered_skills = sorted(version.skills, key=lambda skill: skill.id)
        assert [skill.name for skill in ordered_skills] == ["SQL", "Python", "AWS"]
        skills = {skill.name: skill for skill in version.skills}
        assert skills["SQL"].evidences == []
        assert skills["Python"].level == "avancado"
        assert skills["Python"].evidences == []
        assert [evidence.title for evidence in skills["AWS"].evidences] == ["Projeto AWS"]

        job = _create_job(
            session,
            title="Estagio Python",
            provider_identity_key="gupy:web-350",
        )
        job.description = "Vaga sintetica para teste de perfil."
        job.requirements = "Python avancado obrigatorio"
        result = compare_job_to_profile(session, job.id)
        assert any(
            requirement.status is RequirementMatchStatus.NOT_PROVEN
            and any(term["status"] == "not_proven" for term in requirement.term_results)
            for requirement in result.requirements
        )


def test_web_bind_host_allows_only_loopback() -> None:
    assert validate_bind_host("localhost") == "127.0.0.1"
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    try:
        validate_bind_host("0.0.0.0")
    except Exception as exc:
        assert "publico" in str(exc)
    else:
        raise AssertionError("host publico deveria ser rejeitado")


def test_web_upload_does_not_persist_raw_file_and_manual_skill_is_not_evidence(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    upload_payload = b"""
profile_name: Perfil Upload
skills:
  - name: SQL
    evidence:
      - title: Projeto SQL
        evidence_type: PROJECT
experiences: []
projects: []
education: []
languages: []
"""

    with TestClient(create_app(settings)) as client:
        onboarding = client.get("/onboarding")
        token = _csrf(onboarding.text)
        uploaded = client.post(
            "/onboarding/profile/upload",
            data={"csrf_token": token},
            files={"file": ("profile.yaml", upload_payload, "text/yaml")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303

        profile = client.get("/profile")
        token = _csrf(profile.text)
        manual = client.post(
            "/profile/manual",
            data={
                "csrf_token": token,
                "profile_name": "Perfil Declarado",
                "skills": "AWS\nDatabricks",
            },
            follow_redirects=False,
        )
        assert manual.status_code == 303

    assert not list((tmp_path / "imports").glob("*"))
    with session_scope(settings) as session:
        versions = list(
            session.scalars(
                select(ProfessionalProfileVersion).order_by(ProfessionalProfileVersion.id)
            )
        )
        assert len(versions) == 2
        assert str(versions[0].source_path).startswith("upload:")
        assert versions[1].source_path == "manual:web"
        assert versions[1].evidences == []
        job = _create_job(
            session,
            title="Estagio em Engenharia de Dados",
            provider_identity_key="gupy:web-300",
        )
        job.requirements = "AWS e Databricks"
        result = compare_job_to_profile(session, job.id)
        assert all(
            requirement.status is not RequirementMatchStatus.MATCHED
            for requirement in result.requirements
        )
        assert result.overall_score < 100


def test_web_job_filters_tabs_unshortlist_and_restore(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        active = _create_job(
            session,
            title="Vaga Ativa Dados",
            provider_identity_key="gupy:web-401",
        )
        applied = _create_job(
            session,
            title="Vaga Aplicada Dados",
            provider_identity_key="gupy:web-402",
        )
        dismissed = _create_job(
            session,
            title="Vaga Descartada Dados",
            provider_identity_key="gupy:web-403",
        )
        closed = _create_job(
            session,
            title="Vaga Fechada Dados",
            provider_identity_key="gupy:web-404",
        )
        applied.status = JobStatus.APPLIED
        applied.review_state.state = ReviewState.APPLIED
        dismissed.status = JobStatus.DISMISSED
        dismissed.review_state.state = ReviewState.DISMISSED
        closed.status = JobStatus.CLOSED
        active_id = active.id
        dismissed_id = dismissed.id

    with TestClient(create_app(settings)) as client:
        default_page = client.get("/jobs")
        assert default_page.status_code == 200
        assert "Vaga Ativa Dados" in default_page.text
        assert "Vaga Aplicada Dados" not in default_page.text
        assert "Vaga Descartada Dados" not in default_page.text
        assert "Vaga Fechada Dados" not in default_page.text

        filtered = client.get("/jobs?work_model=REMOTE&employment_type=INTERNSHIP&min_ranking=80")
        assert filtered.status_code == 200
        invalid = client.get("/jobs?status=NAO_EXISTE")
        assert invalid.status_code == 400
        assert "Filtro invalido" in invalid.text

        detail = client.get(f"/jobs/{active_id}")
        token = _csrf(detail.text)
        assert (
            client.post(
                f"/jobs/{active_id}/shortlist",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )
        favorite_tab = client.get("/jobs?tab=favoritas")
        assert favorite_tab.status_code == 200
        assert "Vaga Ativa Dados" in favorite_tab.text
        token = _csrf(client.get(f"/jobs/{active_id}").text)
        assert (
            client.post(
                f"/jobs/{active_id}/unshortlist",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code
            == 303
        )
        token = _csrf(client.get(f"/jobs/{dismissed_id}").text)
        restored = client.post(
            f"/jobs/{dismissed_id}/restore",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert restored.status_code == 303

    with session_scope(settings) as session:
        assert session.get(Job, active_id).review_state.state is ReviewState.SEEN  # type: ignore[union-attr]
        restored_job = session.get(Job, dismissed_id)
        assert restored_job is not None
        assert restored_job.review_state.state is ReviewState.UNREVIEWED


def test_web_missing_review_state_is_effectively_unreviewed_without_get_backfill(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        active = _create_job(
            session,
            title="Vaga Antiga Sem Revisao",
            provider_identity_key="gupy:web-601",
            review_state=None,
        )
        closed = _create_job(
            session,
            title="Vaga Fechada Sem Revisao",
            provider_identity_key="gupy:web-602",
            review_state=None,
        )
        applied = _create_job(
            session,
            title="Vaga Aplicada Sem Revisao",
            provider_identity_key="gupy:web-603",
            review_state=None,
        )
        closed.status = JobStatus.CLOSED
        applied.status = JobStatus.APPLIED
        before_count = session.scalar(select(func.count(JobReviewState.id)))
        active_id = active.id

    with TestClient(create_app(settings)) as client:
        tab_page = client.get("/jobs?tab=aguardando-revisao")
        assert tab_page.status_code == 200
        assert "Vaga Antiga Sem Revisao" in tab_page.text
        assert "Vaga Fechada Sem Revisao" not in tab_page.text
        assert "Vaga Aplicada Sem Revisao" not in tab_page.text

        filter_page = client.get("/jobs?review=UNREVIEWED")
        assert filter_page.status_code == 200
        assert "Vaga Antiga Sem Revisao" in filter_page.text
        assert "Vaga Fechada Sem Revisao" not in filter_page.text
        assert "Vaga Aplicada Sem Revisao" not in filter_page.text

        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Vaga Antiga Sem Revisao" in dashboard.text
        assert "<strong>1</strong><span>nao revisadas</span>" in dashboard.text
        detail = client.get(f"/jobs/{active_id}")
        assert detail.status_code == 200

    with session_scope(settings) as session:
        assert session.scalar(select(func.count(JobReviewState.id))) == before_count
        assert session.get(Job, active_id).review_state is None  # type: ignore[union-attr]


def test_web_jobs_use_current_profile_comparison_in_list_filters_and_sort(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    v2_path = tmp_path / "professional-profile-v2.yaml"
    v2_path.write_text(
        """
profile_name: Perfil Web
summary: Perfil sintetico para testes.
skills:
  - name: Excel
    level: intermediario
    evidence:
      - title: Projeto Excel
        evidence_type: PROJECT
experiences: []
projects: []
education: []
languages: []
""",
        encoding="utf-8",
    )
    with session_scope(settings) as session:
        historical = _create_job(
            session,
            title="Vaga Historica Alta",
            provider_identity_key="gupy:web-701",
        )
        current = _create_job(
            session,
            title="Vaga Atual Excel",
            provider_identity_key="gupy:web-702",
        )
        current.description = "Vaga sintetica com Excel."
        current.requirements = "Excel"
        high_score = compare_job_to_profile(session, historical.id).overall_score
        assert high_score >= 80
        import_professional_profile(session, v2_path, activate=True)
        current_score = compare_job_to_profile(session, current.id).overall_score
        assert current_score >= 80
        historical_id = historical.id

    with TestClient(create_app(settings)) as client:
        with_compatibility = client.get("/jobs?only_with_compatibility=on")
        assert with_compatibility.status_code == 200
        assert "Vaga Atual Excel" in with_compatibility.text
        assert "Vaga Historica Alta" not in with_compatibility.text

        min_compatibility = client.get("/jobs?min_compatibility=80")
        assert min_compatibility.status_code == 200
        assert "Vaga Atual Excel" in min_compatibility.text
        assert "Vaga Historica Alta" not in min_compatibility.text

        sorted_page = client.get("/jobs?sort=compatibility")
        assert sorted_page.status_code == 200
        assert sorted_page.text.index("Vaga Atual Excel") < sorted_page.text.index(
            "Vaga Historica Alta"
        )

        stale_detail = client.get(f"/jobs/{historical_id}")
        assert stale_detail.status_code == 200
        assert "Existe uma análise anterior, mas ela está desatualizada" in stale_detail.text
        assert "perfil diferente" in stale_detail.text
        token = _csrf(stale_detail.text)
        compared = client.post(
            f"/jobs/{historical_id}/compare",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert compared.status_code == 303
        refreshed = client.get("/jobs?only_with_compatibility=on&q=Historica")
        assert "Vaga Historica Alta" in refreshed.text


def test_web_stale_profile_comparison_detects_content_and_rule_changes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        content_job = _create_job(
            session,
            title="Vaga Conteudo Mutavel",
            provider_identity_key="gupy:web-711",
        )
        rule_job = _create_job(
            session,
            title="Vaga Regra Mutavel",
            provider_identity_key="gupy:web-712",
        )
        compare_job_to_profile(session, content_job.id)
        compare_job_to_profile(session, rule_job.id)
        content_job.description = "Descricao atualizada depois da analise."
        rule_comparison = session.scalar(
            select(JobProfileComparison).where(JobProfileComparison.job_id == rule_job.id)
        )
        assert rule_comparison is not None
        rule_comparison.rules_version = "old-profile-rules"
        content_id = content_job.id
        rule_id = rule_job.id

    with TestClient(create_app(settings)) as client:
        content_detail = client.get(f"/jobs/{content_id}")
        assert content_detail.status_code == 200
        assert "Existe uma análise anterior, mas ela está desatualizada" in content_detail.text
        assert "conteudo da vaga alterado" in content_detail.text

        rule_detail = client.get(f"/jobs/{rule_id}")
        assert rule_detail.status_code == 200
        assert "Existe uma análise anterior, mas ela está desatualizada" in rule_detail.text
        assert "regra diferente" in rule_detail.text


def test_web_agenda_month_selects_and_meeting_url_validation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        job = _create_job(session, provider_identity_key="gupy:web-501")
        application = Application(
            job_id=job.id,
            application_key=application_key_for_job(job),
            status=ApplicationStatus.SUBMITTED,
            stage=ApplicationStage.APPLIED,
        )
        session.add(application)
        session.flush()
        job_id = job.id
        application_id = application.id

    with TestClient(create_app(settings)) as client:
        agenda_page = client.get("/agenda?year=2026&month=7")
        assert agenda_page.status_code == 200
        assert "Seg" in agenda_page.text
        assert "Sem data" in agenda_page.text
        assert "Acme Dados" in agenda_page.text
        token = _csrf(agenda_page.text)
        invalid = client.post(
            "/agenda/events",
            data={
                "csrf_token": token,
                "event_type": CareerEventType.INTERVIEW.value,
                "title": "Entrevista invalida",
                "job_id": str(job_id),
                "application_id": str(application_id),
                "meeting_url": "https://localhost/meet",
            },
        )
        assert invalid.status_code == 400
        assert "host local" in invalid.text

        valid = client.post(
            "/agenda/events",
            data={
                "csrf_token": token,
                "event_type": CareerEventType.INTERVIEW.value,
                "title": "Entrevista valida",
                "starts_at": "2026-07-21T10:00",
                "job_id": str(job_id),
                "application_id": str(application_id),
                "meeting_url": "https://meet.example.com/sala",
            },
            follow_redirects=False,
        )
        assert valid.status_code == 303
        undated = client.post(
            "/agenda/events",
            data={
                "csrf_token": token,
                "event_type": CareerEventType.FOLLOW_UP.value,
                "title": "Follow-up sem data",
            },
            follow_redirects=False,
        )
        assert undated.status_code == 303
        refreshed = client.get("/agenda?year=2026&month=7")
        assert "Entrevista valida" in refreshed.text
        assert "Follow-up sem data" in refreshed.text


def test_web_agenda_period_is_localized_limited_and_preserves_filters(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    _create_active_profile(settings, tmp_path)
    with session_scope(settings) as session:
        application = _create_application(
            session,
            title="Vaga Agenda Filtrada",
            provider_identity_key="gupy:web-821",
            applied_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        )
        job_id = application.job_id
        application_id = application.id
        events = [
            CareerEvent(
                job_id=job_id,
                application_id=application_id,
                event_type=CareerEventType.INTERVIEW,
                title="Evento Junho",
                starts_at=datetime(2026, 6, 30, 14, 0, tzinfo=UTC),
                ends_at=None,
                timezone="America/Sao_Paulo",
                source=CareerEventSource.MANUAL,
                confirmation_status=CareerEventConfirmationStatus.CONFIRMED,
            ),
            CareerEvent(
                job_id=job_id,
                application_id=application_id,
                event_type=CareerEventType.INTERVIEW,
                title="Evento Julho Manha",
                starts_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
                ends_at=None,
                timezone="America/Sao_Paulo",
                source=CareerEventSource.MANUAL,
                confirmation_status=CareerEventConfirmationStatus.CONFIRMED,
            ),
            CareerEvent(
                job_id=job_id,
                application_id=application_id,
                event_type=CareerEventType.INTERVIEW,
                title="Evento Julho Tarde",
                starts_at=datetime(2026, 7, 20, 18, 0, tzinfo=UTC),
                ends_at=None,
                timezone="America/Sao_Paulo",
                source=CareerEventSource.MANUAL,
                confirmation_status=CareerEventConfirmationStatus.CONFIRMED,
            ),
            CareerEvent(
                job_id=job_id,
                application_id=application_id,
                event_type=CareerEventType.INTERVIEW,
                title="Evento Agosto",
                starts_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
                ends_at=None,
                timezone="America/Sao_Paulo",
                source=CareerEventSource.MANUAL,
                confirmation_status=CareerEventConfirmationStatus.CONFIRMED,
            ),
            CareerEvent(
                job_id=job_id,
                application_id=application_id,
                event_type=CareerEventType.INTERVIEW,
                title="Evento Sem Data Filtrado",
                starts_at=None,
                ends_at=None,
                timezone="America/Sao_Paulo",
                source=CareerEventSource.MANUAL,
                confirmation_status=CareerEventConfirmationStatus.CONFIRMED,
            ),
        ]
        session.add_all(events)

    query = (
        "/agenda?year=2026&month=7&status=CONFIRMED&event_type=INTERVIEW"
        f"&source=MANUAL&job_id={job_id}&application_id={application_id}"
    )
    with TestClient(create_app(settings)) as client:
        page = client.get(query)
        assert page.status_code == 200
        assert "Julho 2026" in page.text
        assert "Evento Julho Manha" in page.text
        assert "Evento Julho Tarde" in page.text
        assert "Evento Junho" not in page.text
        assert "Evento Agosto" not in page.text
        assert (
            f"/agenda?year=2026&amp;month=6&amp;status=CONFIRMED&amp;event_type=INTERVIEW"
            f"&amp;source=MANUAL&amp;job_id={job_id}&amp;application_id={application_id}"
            in page.text
        )
        assert (
            f"/agenda?year=2026&amp;month=8&amp;status=CONFIRMED&amp;event_type=INTERVIEW"
            f"&amp;source=MANUAL&amp;job_id={job_id}&amp;application_id={application_id}"
            in page.text
        )
        period_section = page.text.split("<h2>Lista do periodo</h2>", 1)[1].split(
            "<h2>Sem data</h2>",
            1,
        )[0]
        undated_section = page.text.split("<h2>Sem data</h2>", 1)[1]
        assert "Evento Sem Data Filtrado" not in period_section
        assert "Evento Sem Data Filtrado" in undated_section


def test_web_source_health_labels_skipped_items_without_partial_claim(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    with session_scope(settings) as session:
        source = Source(
            name="Fonte com itens ignorados",
            slug="skipped-items",
            source_type="gupy",
            base_url="https://jobs.example.com",
        )
        session.add(source)
        session.flush()
        session.add(
            SourceRun(
                source_id=source.id,
                started_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
                finished_at=datetime(2026, 7, 19, 12, 1, tzinfo=UTC),
                status=SourceRunStatus.SUCCESS,
                items_found=10,
                items_created=8,
                items_skipped=2,
            )
        )

    with TestClient(create_app(settings)) as client:
        page = client.get("/sources")
        assert page.status_code == 200
        assert "itens ignorados: 2" in page.text
        assert "parcial" not in page.text


def test_web_collection_background_status_sanitizes_and_blocks_double_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _write_runtime_config(settings)
    started = Event()
    release = Event()
    calls = 0

    def fake_run_search_plan(*_args: object, **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=5)
        raise RuntimeError(
            "Traceback (most recent call last): https://example.com/jobs?token=abc "
            "C:\\Users\\ExampleUser\\secret\\profile.yaml"
        )

    monkeypatch.setattr("radar_vagas.web.collection.run_search_plan", fake_run_search_plan)

    with TestClient(create_app(settings)) as client:
        sources = client.get("/sources")
        token = _csrf(sources.text)
        first = client.post(
            "/sources/collect-search-plan",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert first.status_code == 303
        assert started.wait(timeout=5)
        running = client.get("/sources/collection-status").json()
        assert running["state"] == "running"
        second = client.post(
            "/sources/collect-search-plan",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert second.status_code == 400
        release.set()
        final = _wait_for_collection_state(
            lambda: client.get("/sources/collection-status").json(),
            "failed",
        )
        assert calls == 1
        assert "token=abc" not in final["message"]
        assert "C:\\Users" not in final["message"]
        assert "Traceback" not in final["message"]
        assert "https://example.com/jobs" not in final["message"]
        assert "Detalhes tecnicos omitidos" in final["message"]


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
    review_state: ReviewState | None = ReviewState.UNREVIEWED,
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
    session.add_all([posting, decision])
    if review_state is not None:
        session.add(JobReviewState(job_id=job.id, state=review_state))
    session.flush()
    return job


def _create_application(
    session: Session,
    *,
    title: str,
    provider_identity_key: str,
    applied_at: datetime | None,
) -> Application:
    job = _create_job(session, title=title, provider_identity_key=provider_identity_key)
    application = Application(
        job_id=job.id,
        application_key=application_key_for_job(job),
        status=ApplicationStatus.SUBMITTED,
        stage=ApplicationStage.APPLIED,
        applied_at=applied_at,
        platform="gupy",
    )
    session.add(application)
    session.flush()
    return application


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise AssertionError("token CSRF nao encontrado")
    return match.group(1)


def _wait_for_collection_state(
    status_provider: Callable[[], dict[str, object]],
    expected: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = status_provider()
        if status.get("state") == expected:
            return status
        time.sleep(0.05)
    raise AssertionError(f"coleta nao chegou ao estado {expected}")
