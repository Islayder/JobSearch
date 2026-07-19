from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import func, select

from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_title
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import (
    EmploymentType,
    JobStatus,
    RequirementKind,
    RequirementMatchStatus,
    WorkModel,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    Company,
    Job,
    JobProfileComparison,
    Posting,
    ProfessionalProfileVersion,
    ProfileEvidence,
    ProfileSkill,
    ResumeVersion,
    Source,
)
from radar_vagas.profile.service import (
    compare_job_to_profile,
    import_professional_profile,
)


def test_import_professional_profile_stores_version_skills_evidence_and_resume(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_profile(tmp_path, extra_skill="")

    with session_scope(settings) as session:
        imported = import_professional_profile(session, profile_path)
        assert imported.created_version is True
        assert imported.version_number == 1
        assert session.scalar(select(func.count(ProfessionalProfileVersion.id))) == 1
        assert session.scalar(select(func.count(ProfileSkill.id))) == 4
        assert session.scalar(select(func.count(ProfileEvidence.id))) >= 3
        assert session.scalar(select(func.count(ResumeVersion.id))) == 1

    with session_scope(settings) as session:
        imported_again = import_professional_profile(session, profile_path)
        assert imported_again.created_version is False
        assert imported_again.profile_version_id == imported.profile_version_id
        assert session.scalar(select(func.count(ProfessionalProfileVersion.id))) == 1


def test_import_professional_profile_rejects_invalid_data(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    invalid_path = tmp_path / "invalid.yaml"
    invalid_path.write_text("profile_name: Perfil sem conteudo\n", encoding="utf-8")

    with session_scope(settings) as session, pytest.raises(RadarError):
        import_professional_profile(session, invalid_path)


def test_job_profile_comparison_explains_all_requirement_categories(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_profile(tmp_path, extra_skill="")

    with session_scope(settings) as session:
        import_professional_profile(session, profile_path)
        job = _create_job(session)
        result = compare_job_to_profile(session, job.id)

        statuses = {item.requirement.text: item.status for item in result.requirements}
        assert statuses["Obrigatorio: SQL"] is RequirementMatchStatus.MATCHED
        assert statuses["Git"] is RequirementMatchStatus.NOT_PROVEN
        assert statuses["Conhecimento em Tableau"] is RequirementMatchStatus.PARTIAL
        assert statuses["Boa comunicacao"] is RequirementMatchStatus.AMBIGUOUS
        assert statuses["Excel avancado"] is RequirementMatchStatus.NOT_MATCHED
        assert statuses["Diferencial: R"] is RequirementMatchStatus.NOT_PROVEN
        assert 0 < result.overall_score < 100

        kinds = {item.requirement.text: item.requirement.kind for item in result.requirements}
        assert kinds["Diferencial: R"] is RequirementKind.DESIRABLE
        comparison = session.scalar(select(JobProfileComparison))
        assert comparison is not None
        assert comparison.profile_version_id == result.profile_version_id


def test_profile_change_creates_new_version_and_allows_reevaluation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    first_profile = _write_profile(tmp_path, extra_skill="")
    second_profile = _write_profile(
        tmp_path,
        filename="profile-v2.yaml",
        extra_skill="""
  - name: R
    category: dados
    level: basico
    evidence:
      - title: Analise sintetica em R
        description: Exercicios academicos ficticios.
        evidence_type: PROJECT
""",
    )

    with session_scope(settings) as session:
        first = import_professional_profile(session, first_profile)
        job = _create_job(session)
        first_result = compare_job_to_profile(session, job.id)
        second = import_professional_profile(session, second_profile)
        second_result = compare_job_to_profile(session, job.id)

        assert second.profile_version_id != first.profile_version_id
        assert second.version_number == 2
        assert first_result.profile_version_id == first.profile_version_id
        assert second_result.profile_version_id == second.profile_version_id
        assert second_result.overall_score > first_result.overall_score


def test_private_resume_and_profile_paths_are_ignored() -> None:
    ignored = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/personal/" in ignored
    assert "data/resumes/" in ignored
    assert "data/curricula/" in ignored
    assert "config/profile.local.yaml" in ignored
    assert "config/professional_profile.local.yaml" in ignored


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )


def _write_profile(
    tmp_path: Path,
    *,
    filename: str = "profile.yaml",
    extra_skill: str,
) -> Path:
    path = tmp_path / filename
    path.write_text(
        f"""
profile_name: Perfil Sintetico
headline: Dados e BI
summary: Perfil ficticio para testes.
skills:
  - name: SQL
    category: dados
    level: intermediario
    evidence:
      - title: Projeto SQL
        description: Consultas para indicadores.
        evidence_type: PROJECT
  - name: Python
    category: dados
    level: intermediario
    evidence:
      - title: Automacao Python
        description: Scripts para consolidar CSV.
        evidence_type: PROJECT
  - name: Power BI
    category: bi
    level: basico
    evidence:
      - title: Painel de BI
        description: Dashboard sintetico.
        evidence_type: PROJECT
  - name: Git
    category: tecnologia
    level: basico
{extra_skill}
experiences:
  - title: Monitoria de dados
    organization: Organizacao Ficticia
    description: Apoio em indicadores sinteticos.
    skills:
      - SQL
      - Python
projects:
  - name: Dashboard academico
    description: Painel sintetico.
    technologies:
      - Power BI
      - SQL
education:
  - institution: Universidade Exemplo
    course: Engenharia de Software
    status: em andamento
languages:
  - name: Ingles
    level: intermediario
    evidence:
      - leitura tecnica
""",
        encoding="utf-8",
    )
    return path


def _create_job(session) -> Job:
    source = Source(
        name="Gupy Tests",
        slug="gupy-profile-tests",
        source_type="gupy",
        base_url="https://jobs.gupy.io",
    )
    company = Company(
        canonical_name="Acme Dados",
        normalized_name=normalize_company_name("Acme Dados"),
    )
    session.add_all([source, company])
    session.flush()
    job = Job(
        company_id=company.id,
        canonical_title="Estagio em Dados",
        normalized_title=normalize_title("Estagio em Dados"),
        description="Estagio em dados com muitos requisitos idealizados.",
        requirements=(
            "Obrigatorio: SQL\n"
            "Git\n"
            "Conhecimento em Tableau\n"
            "Boa comunicacao\n"
            "Excel avancado\n"
            "Diferencial: R"
        ),
        technologies_json="[]",
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        country="Brasil",
        remote_country_scope="Brasil",
        status=JobStatus.RECOMMENDED,
    )
    session.add(job)
    session.flush()
    posting = Posting(
        source_id=source.id,
        collection_scope_key="gupy-profile-tests",
        provider="gupy",
        provider_scope="public",
        provider_external_id="profile-1",
        provider_identity_key="gupy:profile-1",
        external_id="profile-1",
        original_url="https://jobs.gupy.io/job/profile-1",
        normalized_url="https://jobs.gupy.io/job/profile-1",
        raw_title=job.canonical_title,
        raw_company=company.canonical_name,
        raw_location="Remote - Brazil",
        raw_description=job.description,
        content_hash=hashlib.sha256(b"profile-1").hexdigest(),
        job_id=job.id,
    )
    session.add(posting)
    session.flush()
    return job
