from __future__ import annotations

import json
from pathlib import Path

from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_title
from radar_vagas.company_intelligence.service import (
    CompanyFactInput,
    CompanyProfileInput,
    CompanyReviewSnapshotInput,
    add_company_fact,
    add_company_review_snapshot,
    generate_interview_preparation,
    upsert_company_profile,
)
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    CompanyInformationSourceType,
    EmploymentType,
    JobStatus,
    WorkModel,
)
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import Company, InterviewPreparation, Job
from radar_vagas.profile.service import (
    ExperienceInput,
    ProfessionalProfileInput,
    SkillInput,
    create_professional_profile,
)


def test_company_information_sources_and_interview_preparation_are_separated(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session)
        profile = create_professional_profile(
            session,
            ProfessionalProfileInput(
                profile_name="Perfil dados",
                skills=[SkillInput(name="SQL"), SkillInput(name="Power BI")],
                experiences=[
                    ExperienceInput(
                        title="Estagiaria de Dados",
                        organization="Empresa Sintetica",
                        description="Dashboards com SQL e Power BI.",
                        skills=["SQL", "Power BI"],
                    )
                ],
            ),
            activate=True,
        )
        upsert_company_profile(
            session,
            job.company_id,
            CompanyProfileInput(
                name="Acme Dados",
                official_website="https://empresa.example",
                industry="Tecnologia",
                company_size="100-500",
                location="Brasil",
                description="Empresa sintetica de produtos de dados.",
                sources=["https://empresa.example/sobre"],
            ),
        )
        add_company_fact(
            session,
            job.company_id,
            CompanyFactInput(
                category="Produto",
                content="Plataforma oficial de analytics.",
                origin_type=CompanyInformationSourceType.OFFICIAL_INFO,
                source_url="https://empresa.example/produto",
            ),
        )
        add_company_fact(
            session,
            job.company_id,
            CompanyFactInput(
                category="Nota",
                content="Perguntar sobre rituais da equipe.",
                origin_type=CompanyInformationSourceType.USER_NOTE,
            ),
        )
        add_company_review_snapshot(
            session,
            job.company_id,
            CompanyReviewSnapshotInput(
                platform="Portal Ficticio",
                overall_rating=4.1,
                review_count=12,
                positives=["Aprendizado"],
                negatives=["Processos em maturacao"],
                period="2026",
                source_url="https://reviews.example/acme",
            ),
        )

        preparation = generate_interview_preparation(session, job.id)
        stored = session.get(InterviewPreparation, preparation.id)
        assert stored is not None
        assert stored.profile_version_id == profile.profile_version_id
        assert "Plataforma oficial de analytics" in stored.summary
        sources = json.loads(stored.sources_json)
        source_types = {item["type"] for item in sources}

        assert CompanyInformationSourceType.OFFICIAL_INFO.value in source_types
        assert CompanyInformationSourceType.USER_NOTE.value in source_types
        assert CompanyInformationSourceType.EMPLOYEE_REPORT.value in source_types
        assert CompanyInformationSourceType.RADAR_INFERENCE.value in source_types
        assert any("SQL" in question for question in json.loads(stored.likely_questions_json))
        assert any(
            "Estagiaria de Dados" in item for item in json.loads(stored.relevant_experiences_json)
        )
        assert json.loads(stored.gaps_json) == ["comparacao atual com perfil nao encontrada"]


def test_interview_preparation_uses_not_found_for_missing_profile(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        job = _create_job(session)
        preparation = generate_interview_preparation(session, job.id)

        assert "perfil nao encontrado" in preparation.summary
        assert json.loads(preparation.relevant_experiences_json) == ["perfil nao encontrado"]
        assert json.loads(preparation.gaps_json) == ["perfil ativo nao encontrado"]


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
        description="Vaga sintetica para atuar com SQL, Power BI e qualidade de dados.",
        requirements="SQL, Power BI e organizacao.",
        technologies_json='["SQL", "Power BI"]',
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        status=JobStatus.RECOMMENDED,
    )
    session.add(job)
    session.flush()
    return job


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'radar.sqlite3'}",
        config_dir=tmp_path / "config",
    )
