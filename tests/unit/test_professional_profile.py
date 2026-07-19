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
    JobRequirementMatch,
    Posting,
    ProfessionalProfile,
    ProfessionalProfileVersion,
    ProfileEvidence,
    ProfileSkill,
    Resume,
    ResumeVersion,
    Source,
)
from radar_vagas.profile.service import (
    activate_profile_version,
    compare_job_to_profile,
    get_active_profile_version,
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
        assert statuses["Excel avancado"] is RequirementMatchStatus.NOT_PROVEN
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


def test_compound_technology_requirement_requires_internal_coverage(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_minimal_profile(
        tmp_path,
        skills="""
  - name: SQL
    level: intermediario
    evidence:
      - title: Projeto SQL
        evidence_type: PROJECT
""",
    )

    with session_scope(settings) as session:
        import_professional_profile(session, profile_path)
        job = _create_job_with_requirements(session, "SQL, Python, AWS e Databricks")
        result = compare_job_to_profile(session, job.id)
        requirement = result.requirements[0]

        assert set(requirement.requirement.terms) == {"sql", "python", "aws", "databricks"}
        assert requirement.status is RequirementMatchStatus.PARTIAL
        assert any(item.get("title") == "Projeto SQL" for item in requirement.evidence)


def test_technologies_json_without_context_is_unknown_not_mandatory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_minimal_profile(tmp_path)

    with session_scope(settings) as session:
        import_professional_profile(session, profile_path)
        job = _create_job_with_requirements(session, None, technologies_json='["SQL"]')
        result = compare_job_to_profile(session, job.id)

        assert result.requirements[0].requirement.kind is RequirementKind.UNKNOWN
        assert result.requirements[0].weight == 1
        assert result.requirements[0].status is RequirementMatchStatus.MATCHED


def test_required_level_is_compared_against_profile_level(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_minimal_profile(
        tmp_path,
        skills="""
  - name: Excel
    level: basico
    evidence:
      - title: Planilha academica
        evidence_type: PROJECT
  - name: SQL
    evidence:
      - title: Consultas SQL
        evidence_type: PROJECT
""",
    )

    with session_scope(settings) as session:
        import_professional_profile(session, profile_path)
        excel_job = _create_job_with_requirements(session, "Excel avancado")
        sql_job = _create_job_with_requirements(session, "SQL")

        excel = compare_job_to_profile(session, excel_job.id).requirements[0]
        sql = compare_job_to_profile(session, sql_job.id).requirements[0]

        assert excel.status is RequirementMatchStatus.PARTIAL
        assert sql.status is RequirementMatchStatus.MATCHED


def test_education_requirements_distinguish_match_absence_and_incompatibility(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    compatible = _write_minimal_profile(
        tmp_path,
        filename="compatible.yaml",
        education="""
  - institution: Universidade Exemplo
    course: Engenharia de Software
    status: em andamento
    end_date: "2027-12"
""",
    )
    incompatible = _write_minimal_profile(
        tmp_path,
        filename="incompatible.yaml",
        education="""
  - institution: Universidade Exemplo
    course: Medicina
    status: em andamento
""",
    )
    missing = _write_minimal_profile(tmp_path, filename="missing.yaml", education="")

    with session_scope(settings) as session:
        compatible_version = import_professional_profile(session, compatible)
        incompatible_version = import_professional_profile(session, incompatible)
        missing_version = import_professional_profile(session, missing)
        job = _create_job_with_requirements(session, "cursando Engenharia de Software")
        tech_job = _create_job_with_requirements(session, "graduacao em tecnologia")
        conclusion_job = _create_job_with_requirements(
            session,
            "estudante com conclusao prevista",
        )

        assert (
            compare_job_to_profile(
                session,
                job.id,
                profile_version_id=compatible_version.profile_version_id,
            )
            .requirements[0]
            .status
            is RequirementMatchStatus.MATCHED
        )
        assert (
            compare_job_to_profile(
                session,
                tech_job.id,
                profile_version_id=compatible_version.profile_version_id,
            )
            .requirements[0]
            .status
            is RequirementMatchStatus.MATCHED
        )
        assert (
            compare_job_to_profile(
                session,
                conclusion_job.id,
                profile_version_id=compatible_version.profile_version_id,
            )
            .requirements[0]
            .status
            is RequirementMatchStatus.MATCHED
        )
        assert (
            compare_job_to_profile(
                session,
                job.id,
                profile_version_id=incompatible_version.profile_version_id,
            )
            .requirements[0]
            .status
            is RequirementMatchStatus.NOT_MATCHED
        )
        assert (
            compare_job_to_profile(
                session,
                job.id,
                profile_version_id=missing_version.profile_version_id,
            )
            .requirements[0]
            .status
            is RequirementMatchStatus.NOT_PROVEN
        )


def test_experience_and_project_evidence_are_used_without_inventing_years(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_minimal_profile(
        tmp_path,
        skills="[]",
        experiences="""
  - title: Analista de Integracoes
    organization: Empresa Ficticia
    start_date: "2024-01"
    end_date: "2026-01"
    description: Trabalho com APIs REST.
    skills:
      - APIs
  - title: Experiencia sem datas
    description: Apoio com SQL.
    skills:
      - SQL
""",
        projects="""
  - name: Painel academico
    description: Projeto em Power BI para indicadores.
    technologies:
      - Power BI
""",
    )

    with session_scope(settings) as session:
        import_professional_profile(session, profile_path)
        years_job = _create_job_with_requirements(session, "2 anos de experiencia com APIs")
        missing_dates_job = _create_job_with_requirements(
            session,
            "1 ano de experiencia com SQL",
        )
        project_job = _create_job_with_requirements(session, "projeto em Power BI")
        experience_job = _create_job_with_requirements(session, "experiencia com APIs")

        assert (
            compare_job_to_profile(session, years_job.id).requirements[0].status
            is RequirementMatchStatus.MATCHED
        )
        assert (
            compare_job_to_profile(session, missing_dates_job.id).requirements[0].status
            is RequirementMatchStatus.AMBIGUOUS
        )
        assert (
            compare_job_to_profile(session, project_job.id).requirements[0].status
            is RequirementMatchStatus.MATCHED
        )
        assert (
            compare_job_to_profile(session, experience_job.id).requirements[0].status
            is RequirementMatchStatus.MATCHED
        )


def test_generic_requirement_next_to_technical_requirement_is_separated(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_path = _write_minimal_profile(tmp_path)

    with session_scope(settings) as session:
        import_professional_profile(session, profile_path)
        job = _create_job_with_requirements(session, "Boa comunicacao e SQL")
        result = compare_job_to_profile(session, job.id)
        statuses = {item.requirement.text: item.status for item in result.requirements}

        assert statuses["Requisito comportamental generico"] is RequirementMatchStatus.AMBIGUOUS
        assert statuses["Boa comunicacao e SQL"] is RequirementMatchStatus.MATCHED


def test_profile_activation_is_global_and_resume_base_is_reused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    profile_a_v1 = _write_minimal_profile(tmp_path, filename="a-v1.yaml", profile_name="Perfil A")
    profile_a_v2 = _write_minimal_profile(
        tmp_path,
        filename="a-v2.yaml",
        profile_name="Perfil A",
        skills="""
  - name: SQL
    evidence:
      - title: Projeto SQL
        evidence_type: PROJECT
  - name: Python
    evidence:
      - title: Projeto Python
        evidence_type: PROJECT
""",
    )
    profile_b = _write_minimal_profile(tmp_path, filename="b.yaml", profile_name="Perfil B")

    with session_scope(settings) as session:
        first = import_professional_profile(session, profile_a_v1)
        second = import_professional_profile(session, profile_a_v2)
        third = import_professional_profile(session, profile_b)

        assert get_active_profile_version(session).id == third.profile_version_id
        assert session.scalar(select(func.count(ProfessionalProfileVersion.id))) == 3
        assert session.scalar(select(func.count(Resume.id))) == 2
        assert session.scalar(select(func.count(ResumeVersion.id))) == 3
        profiles = session.scalars(select(ProfessionalProfile)).all()
        assert sum(1 for profile in profiles if profile.is_active) == 1

        activate_profile_version(session, first.profile_version_id)
        active_versions = session.scalars(
            select(ProfessionalProfileVersion).where(ProfessionalProfileVersion.is_active.is_(True))
        ).all()
        assert [version.id for version in active_versions] == [first.profile_version_id]
        assert get_active_profile_version(session).id == first.profile_version_id
        assert second.profile_id == first.profile_id


def test_comparison_audit_is_idempotent_and_keeps_historical_matches(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    first_profile = _write_minimal_profile(tmp_path, filename="profile-a.yaml")
    second_profile = _write_minimal_profile(
        tmp_path,
        filename="profile-b.yaml",
        skills="""
  - name: SQL
    evidence:
      - title: Projeto SQL
        evidence_type: PROJECT
  - name: AWS
    evidence:
      - title: Laboratorio AWS
        evidence_type: PROJECT
""",
    )

    with session_scope(settings) as session:
        first = import_professional_profile(session, first_profile)
        job = _create_job_with_requirements(session, "SQL")
        first_comparison = compare_job_to_profile(session, job.id)
        repeated = compare_job_to_profile(session, job.id)
        assert repeated.comparison_id == first_comparison.comparison_id
        assert session.scalar(select(func.count(JobProfileComparison.id))) == 1
        assert session.scalar(select(func.count(JobRequirementMatch.id))) == 1

        job.requirements = "SQL\nAWS"
        changed_job = compare_job_to_profile(session, job.id)
        second = import_professional_profile(session, second_profile)
        changed_profile = compare_job_to_profile(
            session,
            job.id,
            profile_version_id=second.profile_version_id,
        )

        assert changed_job.comparison_id != first_comparison.comparison_id
        assert changed_profile.comparison_id != changed_job.comparison_id
        assert first.profile_version_id != second.profile_version_id
        assert session.scalar(select(func.count(JobProfileComparison.id))) == 3


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


def _write_minimal_profile(
    tmp_path: Path,
    *,
    filename: str = "profile.yaml",
    profile_name: str = "Perfil Sintetico",
    skills: str = """
  - name: SQL
    level: intermediario
    evidence:
      - title: Projeto SQL
        evidence_type: PROJECT
""",
    experiences: str = "[]",
    projects: str = "[]",
    education: str = "[]",
) -> Path:
    path = tmp_path / filename
    path.write_text(
        f"""
profile_name: {profile_name}
summary: Perfil ficticio para testes.
skills:{_yaml_section(skills)}
experiences:{_yaml_section(experiences)}
projects:{_yaml_section(projects)}
education:{_yaml_section(education)}
languages:
  - name: Ingles
    level: intermediario
    evidence:
      - leitura tecnica
""",
        encoding="utf-8",
    )
    return path


def _yaml_section(value: str) -> str:
    stripped = value.strip()
    return value if stripped and stripped != "[]" else " []"


def _create_job_with_requirements(
    session,
    requirements: str | None,
    *,
    technologies_json: str = "[]",
) -> Job:
    source = session.scalar(select(Source).where(Source.slug == "gupy-profile-extra-tests"))
    if source is None:
        source = Source(
            name="Gupy Profile Extra Tests",
            slug="gupy-profile-extra-tests",
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
    sequence = int(session.scalar(select(func.count(Job.id))) or 0) + 1
    external_id = f"profile-extra-{sequence}"
    job = Job(
        company_id=company.id,
        canonical_title="Estagio em Dados",
        normalized_title=normalize_title("Estagio em Dados"),
        description="Estagio em dados com requisitos sinteticos.",
        requirements=requirements,
        technologies_json=technologies_json,
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
        collection_scope_key="gupy-profile-extra-tests",
        provider="gupy",
        provider_scope="public",
        provider_external_id=external_id,
        provider_identity_key=f"gupy:{external_id}",
        external_id=external_id,
        original_url=f"https://jobs.gupy.io/job/{external_id}",
        normalized_url=f"https://jobs.gupy.io/job/{external_id}",
        raw_title=job.canonical_title,
        raw_company=company.canonical_name,
        raw_location="Remote - Brazil",
        raw_description=job.description,
        raw_requirements=requirements,
        raw_technologies_json=technologies_json,
        content_hash=hashlib.sha256(external_id.encode("utf-8")).hexdigest(),
        job_id=job.id,
    )
    session.add(posting)
    session.flush()
    return job


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
