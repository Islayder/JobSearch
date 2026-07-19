from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import (
    EmploymentType,
    JobStatus,
    ProfileEvidenceType,
    RequirementKind,
    RequirementMatchStatus,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.persistence.models import (
    EducationCredential,
    Job,
    JobProfileComparison,
    JobRequirementMatch,
    LanguageSkill,
    ProfessionalExperience,
    ProfessionalProfile,
    ProfessionalProfileVersion,
    ProfileActivationEvent,
    ProfileEvidence,
    ProfileProject,
    ProfileSkill,
    Resume,
    ResumeVersion,
)
from radar_vagas.relevance.service import technologies_from_json

PROFILE_RULES_VERSION = "2026-07-19.profile.2"
GENERIC_REQUIREMENT_TERMS = {
    "boa comunicacao",
    "comunicacao",
    "proatividade",
    "organizacao",
    "trabalho em equipe",
    "vontade de aprender",
    "perfil analitico",
}
DESIRABLE_MARKERS = (
    "desejavel",
    "diferencial",
    "seria um plus",
    "nice to have",
    "preferencial",
)
MANDATORY_MARKERS = ("obrigatorio", "necessario", "requisito", "precisa", "deve")
LEVEL_ORDER = {
    "basico": 1,
    "iniciante": 1,
    "intermediario": 2,
    "avancado": 3,
    "fluente": 4,
}
ADJACENT_SKILL_GROUPS = (
    {"power bi", "business intelligence", "bi", "dashboard", "dashboards", "looker", "tableau"},
    {"python", "pandas", "numpy", "jupyter"},
    {"sql", "postgresql", "mysql", "bigquery", "query"},
    {"etl", "pipeline", "engenharia de dados", "data engineering"},
    {"excel", "planilhas", "google sheets"},
)
KNOWN_TECH_TERMS = {
    "api",
    "apis",
    "aws",
    "azure",
    "bigquery",
    "databricks",
    "docker",
    "excel",
    "git",
    "google sheets",
    "looker",
    "mysql",
    "numpy",
    "pandas",
    "power bi",
    "python",
    "r",
    "rest",
    "sql",
    "tableau",
}
TECH_LIST_MARKERS = (
    "conhecimento",
    "experiencia",
    "experiencia com",
    "familiaridade",
    "tecnologias",
    "ferramentas",
    "stack",
)
TECH_COURSE_TERMS = {
    "engenharia de software",
    "engenharia da computacao",
    "ciencia da computacao",
    "sistemas de informacao",
    "analise e desenvolvimento de sistemas",
    "tecnologia",
    "dados",
}
KNOWN_COURSE_TERMS = {
    *TECH_COURSE_TERMS,
    "administracao",
    "direito",
    "economia",
    "enfermagem",
    "medicina",
}


class EvidenceInput(BaseModel):
    title: str
    description: str | None = None
    source_ref: str | None = None
    evidence_type: ProfileEvidenceType = ProfileEvidenceType.SKILL

    @field_validator("title")
    @classmethod
    def require_title(cls, value: str) -> str:
        return _required_text(value, "evidence.title")


class SkillInput(BaseModel):
    name: str
    category: str | None = None
    level: str | None = None
    evidence: list[EvidenceInput] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        return _required_text(value, "skill.name")


class ExperienceInput(BaseModel):
    title: str
    organization: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None
    skills: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def require_title(cls, value: str) -> str:
        return _required_text(value, "experience.title")


class ProjectInput(BaseModel):
    name: str
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    source_ref: str | None = None

    @field_validator("name")
    @classmethod
    def require_name(cls, value: str) -> str:
        return _required_text(value, "project.name")


class EducationInput(BaseModel):
    institution: str
    course: str
    status: str | None = None
    start_date: str | None = None
    end_date: str | None = None

    @field_validator("institution", "course")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _required_text(value, "education")


class LanguageInput(BaseModel):
    name: str
    level: str
    evidence: list[str] = Field(default_factory=list)

    @field_validator("name", "level")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _required_text(value, "language")


class ProfessionalProfileInput(BaseModel):
    profile_name: str
    headline: str | None = None
    summary: str | None = None
    skills: list[SkillInput] = Field(default_factory=list)
    experiences: list[ExperienceInput] = Field(default_factory=list)
    projects: list[ProjectInput] = Field(default_factory=list)
    education: list[EducationInput] = Field(default_factory=list)
    languages: list[LanguageInput] = Field(default_factory=list)

    @field_validator("profile_name")
    @classmethod
    def require_profile_name(cls, value: str) -> str:
        return _required_text(value, "profile_name")

    @model_validator(mode="after")
    def require_professional_content(self) -> ProfessionalProfileInput:
        if not (
            self.skills or self.experiences or self.projects or self.education or self.languages
        ):
            raise ValueError("perfil profissional sem conteudo estruturado")
        return self


@dataclass(frozen=True)
class ProfileImportResult:
    profile_id: int
    profile_version_id: int
    profile_name: str
    version_number: int
    content_hash: str
    created_version: bool
    source_path: Path


@dataclass(frozen=True)
class RequirementCandidate:
    text: str
    kind: RequirementKind
    terms: tuple[str, ...] = ()
    source: str = "requirements"
    original_text: str | None = None


@dataclass(frozen=True)
class RequirementEvaluation:
    requirement: RequirementCandidate
    status: RequirementMatchStatus
    evidence: list[dict[str, str]]
    explanation: str
    weight: int
    term_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ProfileComparisonResult:
    comparison_id: int
    job_id: int
    profile_version_id: int
    overall_score: int
    summary: str
    attention_points: list[str]
    requirements: list[RequirementEvaluation]


@dataclass(frozen=True)
class BatchProfileComparisonResult:
    requested: int
    created: int
    reused: int
    skipped: int
    failed: int
    errors: list[str]
    comparison_ids: list[int]


def import_professional_profile(
    session: Session,
    file_path: Path,
    *,
    profile_name: str | None = None,
    activate: bool = True,
) -> ProfileImportResult:
    if not file_path.exists():
        raise RadarError(f"Arquivo nao encontrado: {file_path}")
    raw_bytes = file_path.read_bytes()
    content_hash = sha256(raw_bytes).hexdigest()
    document = _load_profile_input(file_path, raw_bytes)
    effective_name = profile_name or document.profile_name
    normalized_name = normalize_text(effective_name)
    if not normalized_name:
        raise RadarError("Nome do perfil profissional nao pode ficar vazio.")

    profile = session.scalar(
        select(ProfessionalProfile).where(ProfessionalProfile.normalized_name == normalized_name)
    )
    if profile is None:
        profile = ProfessionalProfile(
            name=effective_name,
            normalized_name=normalized_name,
            is_active=False,
        )
        session.add(profile)
        session.flush()

    existing = session.scalar(
        select(ProfessionalProfileVersion).where(
            ProfessionalProfileVersion.profile_id == profile.id,
            ProfessionalProfileVersion.content_hash == content_hash,
        )
    )
    if existing is not None:
        if activate:
            activate_profile_version(session, existing.id, source="import_profile")
        return ProfileImportResult(
            profile_id=profile.id,
            profile_version_id=existing.id,
            profile_name=profile.name,
            version_number=existing.version_number,
            content_hash=content_hash,
            created_version=False,
            source_path=file_path,
        )

    version_number = _next_version_number(session, profile.id)
    profile.updated_at = utc_now()
    raw_profile_json = json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    version = ProfessionalProfileVersion(
        profile_id=profile.id,
        version_number=version_number,
        source_path=str(file_path),
        source_format=file_path.suffix.lower().lstrip(".") or "text",
        content_hash=content_hash,
        profile_hash=sha256(raw_profile_json.encode("utf-8")).hexdigest(),
        headline=document.headline,
        summary=document.summary,
        raw_profile_json=raw_profile_json,
        is_active=False,
        created_at=utc_now(),
    )
    session.add(version)
    session.flush()
    _store_profile_sections(session, version, document)
    _store_resume_version(session, version, effective_name, file_path, content_hash)
    if activate:
        activate_profile_version(session, version.id, source="import_profile")
    session.flush()
    return ProfileImportResult(
        profile_id=profile.id,
        profile_version_id=version.id,
        profile_name=profile.name,
        version_number=version.version_number,
        content_hash=content_hash,
        created_version=True,
        source_path=file_path,
    )


def list_profile_versions(session: Session) -> list[ProfessionalProfileVersion]:
    return list(
        session.scalars(
            select(ProfessionalProfileVersion)
            .options(selectinload(ProfessionalProfileVersion.profile))
            .order_by(
                ProfessionalProfileVersion.is_active.desc(),
                ProfessionalProfileVersion.created_at.desc(),
                ProfessionalProfileVersion.id.desc(),
            )
        ).all()
    )


def get_active_profile_version(session: Session) -> ProfessionalProfileVersion:
    versions = list(
        session.scalars(
            select(ProfessionalProfileVersion)
            .where(ProfessionalProfileVersion.is_active.is_(True))
            .order_by(
                ProfessionalProfileVersion.created_at.desc(),
                ProfessionalProfileVersion.id.desc(),
            )
        ).all()
    )
    if not versions:
        raise RadarError("Nenhum perfil profissional importado.")
    if len(versions) > 1:
        ids = ", ".join(str(version.id) for version in versions)
        raise RadarError(f"Mais de uma versao de perfil ativa: {ids}.")
    return versions[0]


def activate_profile_version(
    session: Session,
    profile_version_id: int,
    *,
    source: str = "manual",
) -> ProfessionalProfileVersion:
    version = session.get(ProfessionalProfileVersion, profile_version_id)
    if version is None:
        raise RadarError(f"Versao de perfil nao encontrada: {profile_version_id}")
    active_before = session.scalar(
        select(ProfessionalProfileVersion)
        .where(ProfessionalProfileVersion.is_active.is_(True))
        .order_by(
            ProfessionalProfileVersion.created_at.desc(),
            ProfessionalProfileVersion.id.desc(),
        )
    )
    if active_before is not None and active_before.id == version.id:
        return version
    for profile in session.scalars(select(ProfessionalProfile)).all():
        profile.is_active = False
        profile.updated_at = utc_now()
    for existing in session.scalars(select(ProfessionalProfileVersion)).all():
        existing.is_active = False
    session.flush()
    version.is_active = True
    version.profile.is_active = True
    version.profile.updated_at = utc_now()
    session.add(
        ProfileActivationEvent(
            profile_id=version.profile_id,
            profile_version_id=version.id,
            previous_profile_version_id=active_before.id if active_before is not None else None,
            source=source,
            occurred_at=utc_now(),
            created_at=utc_now(),
        )
    )
    session.flush()
    return version


def compare_job_to_profile(
    session: Session,
    job_id: int,
    *,
    profile_version_id: int | None = None,
) -> ProfileComparisonResult:
    job = session.get(Job, job_id)
    if job is None:
        raise RadarError(f"Vaga nao encontrada: {job_id}")
    profile_version = (
        session.get(ProfessionalProfileVersion, profile_version_id)
        if profile_version_id is not None
        else get_active_profile_version(session)
    )
    if profile_version is None:
        raise RadarError(f"Versao de perfil nao encontrada: {profile_version_id}")

    job_content_hash = _job_content_hash(job)
    comparison = session.scalar(
        select(JobProfileComparison).where(
            JobProfileComparison.job_id == job.id,
            JobProfileComparison.profile_version_id == profile_version.id,
            JobProfileComparison.rules_version == PROFILE_RULES_VERSION,
            JobProfileComparison.job_content_hash == job_content_hash,
        )
    )
    if comparison is not None:
        return _comparison_result_from_model(comparison)

    profile_data = _profile_data(profile_version)
    requirements = extract_requirements(job)
    evaluations = [
        evaluate_requirement(requirement, profile_data, job.employment_type)
        for requirement in requirements
    ]
    score, breakdown = _score(evaluations)
    attention_points = _attention_points(evaluations)
    summary = _summary(score, evaluations)
    comparison = JobProfileComparison(
        job_id=job.id,
        profile_version_id=profile_version.id,
        overall_score=score,
        summary=summary,
        score_breakdown_json=json.dumps(breakdown, ensure_ascii=False, sort_keys=True),
        attention_points_json=json.dumps(
            attention_points,
            ensure_ascii=False,
            sort_keys=True,
        ),
        rules_version=PROFILE_RULES_VERSION,
        job_content_hash=job_content_hash,
        created_at=utc_now(),
    )
    session.add(comparison)
    session.flush()

    for item in evaluations:
        comparison.requirement_matches.append(
            JobRequirementMatch(
                comparison_id=comparison.id,
                requirement_text=item.requirement.text,
                requirement_kind=item.requirement.kind,
                match_status=item.status,
                evidence_json=json.dumps(item.evidence, ensure_ascii=False, sort_keys=True),
                explanation=item.explanation,
                weight=item.weight,
                requirement_source=item.requirement.source,
                original_text=item.requirement.original_text,
                terms_json=json.dumps(
                    list(item.requirement.terms),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                term_results_json=json.dumps(
                    item.term_results,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
        )
    session.flush()
    return ProfileComparisonResult(
        comparison_id=comparison.id,
        job_id=job.id,
        profile_version_id=profile_version.id,
        overall_score=score,
        summary=summary,
        attention_points=attention_points,
        requirements=evaluations,
    )


def latest_comparison_for_job(
    session: Session,
    job_id: int,
) -> JobProfileComparison | None:
    return session.scalar(
        select(JobProfileComparison)
        .where(JobProfileComparison.job_id == job_id)
        .order_by(JobProfileComparison.created_at.desc(), JobProfileComparison.id.desc())
    )


def compare_active_jobs_to_profile(
    session: Session,
    *,
    limit: int = 50,
) -> BatchProfileComparisonResult:
    if limit <= 0:
        raise RadarError("limit deve ser um inteiro positivo.")
    profile_version = get_active_profile_version(session)
    statement = (
        select(Job)
        .where(
            Job.status.in_([JobStatus.ELIGIBLE, JobStatus.RECOMMENDED, JobStatus.PENDING_REVIEW])
        )
        .order_by(Job.updated_at.desc(), Job.id.desc())
        .limit(limit)
    )
    jobs = list(session.scalars(statement).all())
    created = 0
    reused = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    comparison_ids: list[int] = []
    for job in jobs:
        try:
            existing = _existing_comparison_for_identity(session, job, profile_version.id)
            if existing is not None:
                reused += 1
                comparison_ids.append(existing.id)
                continue
            result = compare_job_to_profile(
                session,
                job.id,
                profile_version_id=profile_version.id,
            )
            created += 1
            comparison_ids.append(result.comparison_id)
        except RadarError as exc:
            failed += 1
            errors.append(f"vaga {job.id}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive boundary for UI reporting
            failed += 1
            errors.append(f"vaga {job.id}: falha inesperada: {exc}")
    return BatchProfileComparisonResult(
        requested=len(jobs),
        created=created,
        reused=reused,
        skipped=skipped,
        failed=failed,
        errors=errors,
        comparison_ids=comparison_ids,
    )


def _existing_comparison_for_identity(
    session: Session,
    job: Job,
    profile_version_id: int,
) -> JobProfileComparison | None:
    return session.scalar(
        select(JobProfileComparison).where(
            JobProfileComparison.job_id == job.id,
            JobProfileComparison.profile_version_id == profile_version_id,
            JobProfileComparison.rules_version == PROFILE_RULES_VERSION,
            JobProfileComparison.job_content_hash == _job_content_hash(job),
        )
    )


def _comparison_result_from_model(comparison: JobProfileComparison) -> ProfileComparisonResult:
    return ProfileComparisonResult(
        comparison_id=comparison.id,
        job_id=comparison.job_id,
        profile_version_id=comparison.profile_version_id,
        overall_score=comparison.overall_score,
        summary=comparison.summary,
        attention_points=[
            str(item)
            for item in _safe_json_loads(comparison.attention_points_json, expected_type=list)
        ],
        requirements=[
            RequirementEvaluation(
                requirement=RequirementCandidate(
                    text=match.requirement_text,
                    kind=match.requirement_kind,
                    terms=tuple(
                        str(item) for item in _safe_json_loads(match.terms_json, expected_type=list)
                    ),
                    source=match.requirement_source or "legacy",
                    original_text=match.original_text,
                ),
                status=match.match_status,
                evidence=[
                    {str(key): str(value) for key, value in item.items()}
                    for item in _safe_json_loads(match.evidence_json, expected_type=list)
                    if isinstance(item, dict)
                ],
                explanation=match.explanation,
                weight=match.weight,
                term_results=[
                    item
                    for item in _safe_json_loads(match.term_results_json, expected_type=list)
                    if isinstance(item, dict)
                ],
            )
            for match in comparison.requirement_matches
        ],
    )


def extract_requirements(job: Job) -> list[RequirementCandidate]:
    candidates: list[RequirementCandidate] = []
    for raw_line in _split_requirement_text(job.requirements):
        candidates.extend(_candidates_from_requirement_line(raw_line, source="requirements"))
    for technology in technologies_from_json(job.technologies_json):
        text = f"Conhecimento em {technology}"
        if not _contains_candidate(candidates, text):
            candidates.append(
                RequirementCandidate(
                    text,
                    RequirementKind.UNKNOWN,
                    terms=(technology,),
                    source="technologies_json",
                    original_text=text,
                )
            )
    if not candidates:
        for raw_line in _split_requirement_text(job.description):
            if _looks_like_requirement(raw_line):
                candidates.extend(_candidates_from_requirement_line(raw_line, source="description"))
    if not candidates:
        candidates.append(
            RequirementCandidate(
                "Requisitos descritos de forma generica na vaga",
                RequirementKind.UNKNOWN,
                source="fallback",
            )
        )
    return _dedupe_candidates(candidates)


def evaluate_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    employment_type: EmploymentType,
) -> RequirementEvaluation:
    normalized = normalize_text(requirement.text)
    weight = _requirement_weight(requirement, employment_type)

    education_result = _education_requirement(requirement, profile_data, weight)
    if education_result is not None:
        return education_result

    experience_years_result = _experience_years_requirement(requirement, profile_data, weight)
    if experience_years_result is not None:
        return experience_years_result

    language_result = _language_requirement(requirement, profile_data, weight)
    if language_result is not None:
        return language_result

    terms = requirement.terms or tuple(_extract_known_terms(requirement.text))
    if terms:
        return _technical_requirement(requirement, profile_data, weight, terms)

    if _is_generic_requirement(normalize_text(requirement.original_text or requirement.text)):
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.AMBIGUOUS,
            evidence=[],
            explanation=(
                "Requisito generico; revisar manualmente sem assumir ausencia de competencia."
            ),
            weight=weight,
        )

    exact_skills = _matching_skills(normalized, profile_data["skills"])
    if exact_skills:
        required_level = _required_level(normalized)
        status, explanation = _skill_status_for_level(exact_skills[0], required_level)
        evidences = _evidence_for_skills(exact_skills, profile_data)
        if evidences:
            return RequirementEvaluation(
                requirement=requirement,
                status=status,
                evidence=evidences,
                explanation=explanation,
                weight=weight,
            )
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.NOT_PROVEN,
            evidence=[{"skill": skill["name"]} for skill in exact_skills],
            explanation="Competencia citada no perfil, mas sem evidencia estruturada.",
            weight=weight,
        )

    adjacent = _adjacent_skills(normalized, profile_data["skills"])
    if adjacent:
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.PARTIAL,
            evidence=_evidence_for_skills(adjacent, profile_data)
            or [{"skill": skill["name"]} for skill in adjacent],
            explanation="Perfil tem competencia proxima, mas nao comprova aderencia completa.",
            weight=weight,
        )

    status = _status_for_absent_requirement(requirement)
    return RequirementEvaluation(
        requirement=requirement,
        status=status,
        evidence=[],
        explanation=(
            "Diferencial ausente nao elimina a vaga automaticamente."
            if requirement.kind is RequirementKind.DESIRABLE
            else (
                "Tecnologia veio de campo sem contexto de obrigatoriedade; revisar manualmente."
                if requirement.kind is RequirementKind.UNKNOWN
                else "Nao ha evidencia estruturada; ausencia no curriculo nao prova incapacidade."
            )
        ),
        weight=weight,
    )


def _load_profile_input(file_path: Path, raw_bytes: bytes) -> ProfessionalProfileInput:
    suffix = file_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        loaded = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    elif suffix == ".json":
        loaded = json.loads(raw_bytes.decode("utf-8"))
    elif suffix == ".txt":
        loaded = _profile_from_text(raw_bytes.decode("utf-8"))
    else:
        raise RadarError("Use um perfil estruturado .yaml, .yml, .json ou .txt.")
    if not isinstance(loaded, dict):
        raise RadarError("Perfil profissional deve ser um objeto estruturado.")
    try:
        return ProfessionalProfileInput.model_validate(loaded)
    except ValueError as exc:
        raise RadarError(f"Perfil profissional invalido: {exc}") from exc


def _profile_from_text(text: str) -> dict[str, Any]:
    lines = [_strip_bullet(line) for line in text.splitlines()]
    skills = [{"name": line} for line in lines if line and len(line) <= 80]
    return {"profile_name": "Perfil importado", "skills": skills}


def _store_profile_sections(
    session: Session,
    version: ProfessionalProfileVersion,
    document: ProfessionalProfileInput,
) -> None:
    skill_by_normalized: dict[str, ProfileSkill] = {}
    for skill_input in document.skills:
        normalized = normalize_text(skill_input.name)
        if normalized in skill_by_normalized:
            skill = skill_by_normalized[normalized]
        else:
            skill = ProfileSkill(
                profile_version_id=version.id,
                name=skill_input.name,
                normalized_name=normalized,
                category=skill_input.category,
                level=skill_input.level,
                created_at=utc_now(),
            )
            session.add(skill)
            session.flush()
            skill_by_normalized[normalized] = skill
        for evidence_input in skill_input.evidence:
            session.add(
                ProfileEvidence(
                    profile_version_id=version.id,
                    skill_id=skill.id,
                    evidence_type=evidence_input.evidence_type,
                    title=evidence_input.title,
                    description=evidence_input.description,
                    source_ref=evidence_input.source_ref,
                    created_at=utc_now(),
                )
            )
    for experience in document.experiences:
        session.add(
            ProfessionalExperience(
                profile_version_id=version.id,
                title=experience.title,
                organization=experience.organization,
                start_date=experience.start_date,
                end_date=experience.end_date,
                description=experience.description,
                skills_json=json.dumps(experience.skills, ensure_ascii=False, sort_keys=True),
            )
        )
        _add_section_evidence(
            session,
            version.id,
            skill_by_normalized,
            skills=experience.skills,
            evidence_type=ProfileEvidenceType.EXPERIENCE,
            title=experience.title,
            description=experience.description,
        )
    for project in document.projects:
        session.add(
            ProfileProject(
                profile_version_id=version.id,
                name=project.name,
                description=project.description,
                technologies_json=json.dumps(
                    project.technologies,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                source_ref=project.source_ref,
            )
        )
        _add_section_evidence(
            session,
            version.id,
            skill_by_normalized,
            skills=project.technologies,
            evidence_type=ProfileEvidenceType.PROJECT,
            title=project.name,
            description=project.description,
            source_ref=project.source_ref,
        )
    for education in document.education:
        session.add(
            EducationCredential(
                profile_version_id=version.id,
                institution=education.institution,
                course=education.course,
                status=education.status,
                start_date=education.start_date,
                end_date=education.end_date,
            )
        )
    for language in document.languages:
        session.add(
            LanguageSkill(
                profile_version_id=version.id,
                name=language.name,
                normalized_name=normalize_text(language.name),
                level=language.level,
                evidence_json=json.dumps(language.evidence, ensure_ascii=False, sort_keys=True),
            )
        )


def _add_section_evidence(
    session: Session,
    profile_version_id: int,
    skill_by_normalized: dict[str, ProfileSkill],
    *,
    skills: list[str],
    evidence_type: ProfileEvidenceType,
    title: str,
    description: str | None,
    source_ref: str | None = None,
) -> None:
    for skill_name in skills:
        skill = skill_by_normalized.get(normalize_text(skill_name))
        if skill is None:
            continue
        session.add(
            ProfileEvidence(
                profile_version_id=profile_version_id,
                skill_id=skill.id,
                evidence_type=evidence_type,
                title=title,
                description=description,
                source_ref=source_ref,
                created_at=utc_now(),
            )
        )


def _store_resume_version(
    session: Session,
    profile_version: ProfessionalProfileVersion,
    profile_name: str,
    file_path: Path,
    content_hash: str,
) -> None:
    resume = session.scalar(
        select(Resume).where(
            Resume.profile_id == profile_version.profile_id,
            Resume.is_base.is_(True),
        )
    )
    if resume is None:
        resume = Resume(
            profile_id=profile_version.profile_id,
            name=f"Curriculo base - {profile_name}",
            is_base=True,
            source_path=str(file_path),
            content_hash=content_hash,
            created_at=utc_now(),
        )
        session.add(resume)
        session.flush()
    else:
        resume.source_path = str(file_path)
        resume.content_hash = content_hash
    existing_version = session.scalar(
        select(ResumeVersion).where(ResumeVersion.profile_version_id == profile_version.id)
    )
    if existing_version is not None:
        return
    session.add(
        ResumeVersion(
            resume_id=resume.id,
            profile_version_id=profile_version.id,
            file_path=str(file_path),
            change_summary=(
                f"Importacao estruturada do perfil versao {profile_version.version_number}."
            ),
            created_at=utc_now(),
        )
    )


def _next_version_number(session: Session, profile_id: int) -> int:
    current = session.scalar(
        select(func.max(ProfessionalProfileVersion.version_number)).where(
            ProfessionalProfileVersion.profile_id == profile_id
        )
    )
    return int(current or 0) + 1


def _profile_data(profile_version: ProfessionalProfileVersion) -> dict[str, Any]:
    skills = [
        {
            "id": skill.id,
            "name": skill.name,
            "normalized_name": skill.normalized_name,
            "level": skill.level,
        }
        for skill in profile_version.skills
    ]
    evidences = [
        {
            "skill_id": evidence.skill_id,
            "title": evidence.title,
            "description": evidence.description,
            "type": evidence.evidence_type.value,
            "source_ref": evidence.source_ref,
        }
        for evidence in profile_version.evidences
    ]
    languages = [
        {
            "name": language.name,
            "normalized_name": language.normalized_name,
            "level": language.level,
            "evidence": _safe_json_loads(language.evidence_json, expected_type=list),
        }
        for language in profile_version.languages
    ]
    experiences = [
        {
            "title": experience.title,
            "organization": experience.organization,
            "start_date": experience.start_date,
            "end_date": experience.end_date,
            "description": experience.description,
            "skills": _safe_json_loads(experience.skills_json, expected_type=list),
        }
        for experience in profile_version.experiences
    ]
    projects = [
        {
            "name": project.name,
            "description": project.description,
            "technologies": _safe_json_loads(project.technologies_json, expected_type=list),
            "source_ref": project.source_ref,
        }
        for project in profile_version.projects
    ]
    education = [
        {
            "institution": credential.institution,
            "course": credential.course,
            "status": credential.status,
            "start_date": credential.start_date,
            "end_date": credential.end_date,
        }
        for credential in profile_version.education
    ]
    return {
        "skills": skills,
        "evidences": evidences,
        "languages": languages,
        "experiences": experiences,
        "projects": projects,
        "education": education,
    }


def _job_content_hash(job: Job) -> str:
    payload = {
        "title": job.canonical_title,
        "description": job.description,
        "department": job.department,
        "area": job.area,
        "requirements": job.requirements,
        "responsibilities": job.responsibilities,
        "technologies_json": job.technologies_json,
        "employment_type": job.employment_type.value,
        "course_requirement": job.course_requirement,
        "work_model": job.work_model.value,
        "city": job.city,
        "state": job.state,
        "country": job.country,
        "remote_country_scope": job.remote_country_scope,
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _safe_json_loads(value: str | None, *, expected_type: type) -> Any:
    if not value:
        return expected_type()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return expected_type()
    return decoded if isinstance(decoded, expected_type) else expected_type()


def _split_requirement_text(value: str | None) -> list[str]:
    if not value:
        return []
    pieces = re.split(r"[\n\r;\u2022]+", value)
    return [_strip_bullet(piece) for piece in pieces if _strip_bullet(piece)]


def _candidates_from_requirement_line(
    value: str,
    *,
    source: str,
) -> list[RequirementCandidate]:
    kind = _requirement_kind(value)
    normalized = normalize_text(value)
    terms = tuple(_extract_known_terms(value))
    candidates: list[RequirementCandidate] = []
    if terms and _is_generic_requirement(normalized):
        candidates.append(
            RequirementCandidate(
                "Requisito comportamental generico",
                kind,
                source=source,
                original_text=value,
            )
        )
        candidates.append(
            RequirementCandidate(
                value,
                kind,
                terms=terms,
                source=source,
                original_text=value,
            )
        )
        return candidates
    candidates.append(
        RequirementCandidate(
            value,
            kind,
            terms=terms if _looks_like_technology_requirement(value, terms) else (),
            source=source,
            original_text=value,
        )
    )
    return candidates


def _extract_known_terms(value: str) -> list[str]:
    normalized = normalize_text(value)
    found = [
        term
        for term in sorted(KNOWN_TECH_TERMS, key=len, reverse=True)
        if _contains_term(normalized, term)
    ]
    deduped: list[str] = []
    for term in found:
        if not any(_contains_term(term, existing) for existing in deduped):
            deduped.append(term)
    return deduped


def _looks_like_technology_requirement(value: str, terms: tuple[str, ...]) -> bool:
    if not terms:
        return False
    normalized = normalize_text(value)
    if len(terms) > 1 and re.search(r",|\se\s|\sou\s|/", normalized):
        return True
    return any(marker in normalized for marker in TECH_LIST_MARKERS)


def _strip_bullet(value: str) -> str:
    return re.sub(r"^\s*(?:[-*]\s+|\d+[.)]\s*)", "", value).strip()


def _looks_like_requirement(value: str) -> bool:
    normalized = normalize_text(value)
    if any(marker in normalized for marker in (*MANDATORY_MARKERS, *DESIRABLE_MARKERS)):
        return True
    return any(
        _contains_term(normalized, term) for group in ADJACENT_SKILL_GROUPS for term in group
    )


def _requirement_kind(value: str) -> RequirementKind:
    normalized = normalize_text(value)
    if any(marker in normalized for marker in DESIRABLE_MARKERS):
        return RequirementKind.DESIRABLE
    return RequirementKind.MANDATORY


def _contains_candidate(candidates: list[RequirementCandidate], text: str) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(candidate.text) == normalized for candidate in candidates)


def _dedupe_candidates(candidates: list[RequirementCandidate]) -> list[RequirementCandidate]:
    deduped: dict[str, RequirementCandidate] = {}
    for candidate in candidates:
        normalized = normalize_text(candidate.text)
        if normalized and normalized not in deduped:
            deduped[normalized] = candidate
    return list(deduped.values())


def _is_generic_requirement(normalized_requirement: str) -> bool:
    return any(_contains_term(normalized_requirement, term) for term in GENERIC_REQUIREMENT_TERMS)


def _requirement_weight(
    requirement: RequirementCandidate,
    employment_type: EmploymentType,
) -> int:
    if requirement.kind is RequirementKind.DESIRABLE:
        return 1
    if requirement.kind is RequirementKind.UNKNOWN:
        return 1
    return 2 if employment_type is EmploymentType.INTERNSHIP else 3


def _status_for_absent_requirement(requirement: RequirementCandidate) -> RequirementMatchStatus:
    if requirement.kind is RequirementKind.UNKNOWN:
        return RequirementMatchStatus.AMBIGUOUS
    return RequirementMatchStatus.NOT_PROVEN


def _technical_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    weight: int,
    terms: tuple[str, ...],
) -> RequirementEvaluation:
    term_results = [
        _evaluate_technical_term(term, requirement, profile_data, terms=terms) for term in terms
    ]
    evidence = [item for result in term_results for item in result["evidence"]]
    matched_count = sum(1 for result in term_results if result["status"] == "matched")
    partial_count = sum(1 for result in term_results if result["status"] == "partial")
    not_proven_count = sum(1 for result in term_results if result["status"] == "not_proven")
    ambiguous_count = sum(1 for result in term_results if result["status"] == "ambiguous")

    if matched_count == len(term_results):
        status = RequirementMatchStatus.MATCHED
        explanation = "Todos os termos tecnicos do requisito foram comprovados."
    elif matched_count or partial_count:
        status = RequirementMatchStatus.PARTIAL
        explanation = (
            f"Cobertura parcial do requisito composto: {matched_count} de "
            f"{len(term_results)} termos comprovados."
        )
    else:
        status = _status_for_absent_requirement(requirement)
        explanation = (
            "Tecnologia veio de campo sem contexto de obrigatoriedade; revisar manualmente."
            if requirement.kind is RequirementKind.UNKNOWN
            else "Nenhum termo tecnico do requisito foi comprovado no perfil estruturado."
        )
    if ambiguous_count and status is not RequirementMatchStatus.PARTIAL:
        status = RequirementMatchStatus.AMBIGUOUS
        explanation = "Nivel do requisito composto nao ficou seguro para todos os termos."
    if not_proven_count and status is RequirementMatchStatus.MATCHED:
        status = RequirementMatchStatus.PARTIAL
    return RequirementEvaluation(
        requirement=requirement,
        status=status,
        evidence=evidence,
        explanation=explanation,
        weight=weight,
        term_results=term_results,
    )


def _evaluate_technical_term(
    term: str,
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    *,
    terms: tuple[str, ...],
) -> dict[str, Any]:
    required_level, level_ambiguous = _required_level_for_term(requirement.text, term, terms)
    matching_skills = _matching_skills(term, profile_data["skills"])
    if matching_skills:
        evidences = _evidence_for_skills(matching_skills, profile_data)
        if not evidences:
            return {
                "term": term,
                "status": "not_proven",
                "evidence": [{"term": term, "skill": str(matching_skills[0]["name"])}],
                "explanation": "habilidade citada sem evidencia estruturada.",
                "required_level": _level_label(required_level),
                "level_ambiguous": level_ambiguous,
            }
        if level_ambiguous:
            return {
                "term": term,
                "status": "ambiguous",
                "evidence": evidences,
                "explanation": "nivel citado no requisito composto nao foi ligado com seguranca.",
                "required_level": None,
                "level_ambiguous": True,
            }
        status, explanation = _skill_status_for_level(matching_skills[0], required_level)
        return {
            "term": term,
            "status": _term_status_name(status),
            "evidence": evidences,
            "explanation": explanation,
            "required_level": _level_label(required_level),
            "level_ambiguous": False,
        }

    project_evidence = _project_evidence_for_term(term, profile_data)
    experience_evidence = _experience_evidence_for_term(term, profile_data)
    if project_evidence or experience_evidence:
        if level_ambiguous:
            return {
                "term": term,
                "status": "ambiguous",
                "evidence": [*project_evidence, *experience_evidence],
                "explanation": "nivel citado no requisito composto nao foi ligado com seguranca.",
                "required_level": None,
                "level_ambiguous": True,
            }
        return {
            "term": term,
            "status": "matched" if required_level is None else "partial",
            "evidence": [*project_evidence, *experience_evidence],
            "explanation": "termo comprovado por projeto ou experiencia.",
            "required_level": _level_label(required_level),
            "level_ambiguous": False,
        }

    adjacent = _adjacent_skills(term, profile_data["skills"])
    if adjacent:
        return {
            "term": term,
            "status": "partial",
            "evidence": _evidence_for_skills(adjacent, profile_data)
            or [{"term": term, "skill": str(skill["name"])} for skill in adjacent],
            "explanation": "competencia adjacente encontrada.",
            "required_level": _level_label(required_level),
            "level_ambiguous": level_ambiguous,
        }
    if level_ambiguous:
        return {
            "term": term,
            "status": "ambiguous",
            "evidence": [],
            "explanation": "nivel citado no requisito composto nao foi ligado com seguranca.",
            "required_level": None,
            "level_ambiguous": True,
        }
    return {
        "term": term,
        "status": "not_proven",
        "evidence": [],
        "explanation": "",
        "required_level": _level_label(required_level),
        "level_ambiguous": False,
    }


def _term_status_name(status: RequirementMatchStatus) -> str:
    if status is RequirementMatchStatus.MATCHED:
        return "matched"
    if status is RequirementMatchStatus.PARTIAL:
        return "partial"
    if status is RequirementMatchStatus.AMBIGUOUS:
        return "ambiguous"
    return "not_proven"


def _skill_status_for_level(
    skill: dict[str, Any],
    required_level: int | None,
) -> tuple[RequirementMatchStatus, str]:
    if required_level is None:
        return (
            RequirementMatchStatus.MATCHED,
            "Competencia encontrada no perfil com evidencia associada.",
        )
    profile_level = _normalized_level(skill.get("level"))
    if profile_level is None:
        return (
            RequirementMatchStatus.AMBIGUOUS,
            "Nivel requerido foi informado, mas o nivel do perfil nao esta estruturado.",
        )
    if profile_level >= required_level:
        return (
            RequirementMatchStatus.MATCHED,
            "Competencia encontrada com nivel suficiente para o requisito.",
        )
    return (
        RequirementMatchStatus.PARTIAL,
        "Competencia encontrada, mas o nivel comprovado e inferior ao requerido.",
    )


def _project_evidence_for_term(term: str, profile_data: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    normalized_term = normalize_text(term)
    for project in profile_data["projects"]:
        haystack = " ".join(
            [
                str(project.get("name") or ""),
                str(project.get("description") or ""),
                " ".join(str(item) for item in project.get("technologies", [])),
            ]
        )
        if _contains_term(normalize_text(haystack), normalized_term):
            evidence.append(
                {
                    "term": term,
                    "type": ProfileEvidenceType.PROJECT.value,
                    "title": str(project.get("name") or ""),
                    "source_ref": str(project.get("source_ref") or ""),
                }
            )
    return evidence


def _experience_evidence_for_term(term: str, profile_data: dict[str, Any]) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    normalized_term = normalize_text(term)
    for experience in profile_data["experiences"]:
        haystack = " ".join(
            [
                str(experience.get("title") or ""),
                str(experience.get("description") or ""),
                " ".join(str(item) for item in experience.get("skills", [])),
            ]
        )
        if _contains_term(normalize_text(haystack), normalized_term):
            evidence.append(
                {
                    "term": term,
                    "type": ProfileEvidenceType.EXPERIENCE.value,
                    "title": str(experience.get("title") or ""),
                    "organization": str(experience.get("organization") or ""),
                }
            )
    return evidence


def _education_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    weight: int,
) -> RequirementEvaluation | None:
    normalized = normalize_text(requirement.text)
    if not any(
        marker in normalized
        for marker in ("graduacao", "curso", "cursando", "estudante", "formacao")
    ):
        return None
    education = profile_data["education"]
    if not education:
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.NOT_PROVEN,
            evidence=[],
            explanation="Formacao nao informada no perfil estruturado.",
            weight=weight,
        )

    requested_courses = [term for term in KNOWN_COURSE_TERMS if _contains_term(normalized, term)]
    if "conclusao prevista" in normalized:
        for credential in education:
            if credential.get("end_date"):
                return RequirementEvaluation(
                    requirement=requirement,
                    status=RequirementMatchStatus.MATCHED,
                    evidence=[_education_evidence(credential)],
                    explanation="Formacao possui data de conclusao prevista.",
                    weight=weight,
                )
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.NOT_PROVEN,
            evidence=[],
            explanation="Perfil informa formacao, mas nao comprova conclusao prevista.",
            weight=weight,
        )

    if not requested_courses:
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.AMBIGUOUS,
            evidence=[_education_evidence(item) for item in education],
            explanation="Requisito academico generico; revisar manualmente.",
            weight=weight,
        )

    for credential in education:
        course = normalize_text(str(credential.get("course") or ""))
        if _course_matches(course, requested_courses):
            if "cursando" in normalized or "estudante" in normalized:
                status = normalize_text(str(credential.get("status") or ""))
                if any(term in status for term in ("andamento", "cursando", "estudante")):
                    return RequirementEvaluation(
                        requirement=requirement,
                        status=RequirementMatchStatus.MATCHED,
                        evidence=[_education_evidence(credential)],
                        explanation="Curso e situacao academica atendem ao requisito.",
                        weight=weight,
                    )
                return RequirementEvaluation(
                    requirement=requirement,
                    status=RequirementMatchStatus.PARTIAL,
                    evidence=[_education_evidence(credential)],
                    explanation="Curso compativel, mas situacao academica nao esta comprovada.",
                    weight=weight,
                )
            return RequirementEvaluation(
                requirement=requirement,
                status=RequirementMatchStatus.MATCHED,
                evidence=[_education_evidence(credential)],
                explanation="Formacao compativel encontrada no perfil.",
                weight=weight,
            )

    return RequirementEvaluation(
        requirement=requirement,
        status=RequirementMatchStatus.NOT_MATCHED,
        evidence=[_education_evidence(item) for item in education],
        explanation="Formacao informada e objetivamente diferente da exigida.",
        weight=weight,
    )


def _course_matches(normalized_course: str, requested_courses: list[str]) -> bool:
    if "tecnologia" in requested_courses:
        return any(
            _contains_term(normalized_course, term)
            for term in TECH_COURSE_TERMS
            if term != "tecnologia"
        )
    return any(_contains_term(normalized_course, term) for term in requested_courses)


def _education_evidence(credential: dict[str, Any]) -> dict[str, str]:
    return {
        "type": ProfileEvidenceType.EDUCATION.value,
        "course": str(credential.get("course") or ""),
        "institution": str(credential.get("institution") or ""),
        "status": str(credential.get("status") or ""),
        "end_date": str(credential.get("end_date") or ""),
    }


def _experience_years_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    weight: int,
) -> RequirementEvaluation | None:
    normalized = normalize_text(requirement.text)
    match = re.search(r"(\d+)\s*(?:\+?\s*)anos?", normalized)
    if match is None or "experiencia" not in normalized:
        return None
    required_years = int(match.group(1))
    terms = requirement.terms or tuple(_extract_known_terms(requirement.text))
    complete_months = 0
    has_incomplete_match = False
    evidence: list[dict[str, str]] = []
    for experience in profile_data["experiences"]:
        if terms and not any(_experience_contains_term(experience, term) for term in terms):
            continue
        months = _experience_months(experience)
        if months is None:
            has_incomplete_match = True
            evidence.append(_experience_evidence(experience))
            continue
        complete_months += months
        evidence.append(_experience_evidence(experience))
    if complete_months >= required_years * 12:
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.MATCHED,
            evidence=evidence,
            explanation="Tempo de experiencia comprovado por datas completas.",
            weight=weight,
        )
    if complete_months > 0:
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.NOT_MATCHED,
            evidence=evidence,
            explanation="Tempo comprovado e inferior ao minimo requerido.",
            weight=weight,
        )
    if has_incomplete_match:
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.AMBIGUOUS,
            evidence=evidence,
            explanation="Experiencia relacionada existe, mas as datas nao permitem calcular anos.",
            weight=weight,
        )
    return RequirementEvaluation(
        requirement=requirement,
        status=RequirementMatchStatus.NOT_PROVEN,
        evidence=[],
        explanation="Tempo de experiencia nao comprovado no perfil estruturado.",
        weight=weight,
    )


def _experience_contains_term(experience: dict[str, Any], term: str) -> bool:
    haystack = " ".join(
        [
            str(experience.get("title") or ""),
            str(experience.get("description") or ""),
            " ".join(str(item) for item in experience.get("skills", [])),
        ]
    )
    return _contains_term(normalize_text(haystack), term)


def _experience_evidence(experience: dict[str, Any]) -> dict[str, str]:
    return {
        "type": ProfileEvidenceType.EXPERIENCE.value,
        "title": str(experience.get("title") or ""),
        "organization": str(experience.get("organization") or ""),
        "start_date": str(experience.get("start_date") or ""),
        "end_date": str(experience.get("end_date") or ""),
    }


def _experience_months(experience: dict[str, Any]) -> int | None:
    start = _date_parts(str(experience.get("start_date") or ""))
    end = _date_parts(str(experience.get("end_date") or ""))
    if start is None or end is None:
        return None
    return max(0, (end[0] - start[0]) * 12 + end[1] - start[1])


def _date_parts(value: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d{4})(?:-(\d{2})(?:-\d{2})?)?$", value.strip())
    if match is None:
        return None
    month = int(match.group(2) or "1")
    return int(match.group(1)), month


def _language_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    weight: int,
) -> RequirementEvaluation | None:
    normalized = normalize_text(requirement.text)
    if not any(term in normalized for term in ("ingles", "english", "espanhol", "spanish")):
        return None
    required_level = _required_level(normalized)
    for language in profile_data["languages"]:
        language_name = str(language["normalized_name"])
        if language_name and (
            _contains_term(normalized, language_name)
            or (language_name == "ingles" and "english" in normalized)
        ):
            level = _normalized_level(str(language["level"]))
            if required_level is None or (
                "leitura" in normalized
                and any(
                    "leitura" in normalize_text(str(item)) for item in language.get("evidence", [])
                )
            ):
                status = RequirementMatchStatus.MATCHED
            elif level is None:
                status = RequirementMatchStatus.AMBIGUOUS
            else:
                status = (
                    RequirementMatchStatus.MATCHED
                    if level >= required_level
                    else RequirementMatchStatus.PARTIAL
                )
            return RequirementEvaluation(
                requirement=requirement,
                status=status,
                evidence=[{"language": str(language["name"]), "level": str(language["level"])}],
                explanation="Idioma encontrado no perfil.",
                weight=weight,
            )
    return RequirementEvaluation(
        requirement=requirement,
        status=_status_for_absent_requirement(requirement),
        evidence=[],
        explanation="Idioma nao comprovado no perfil estruturado.",
        weight=weight,
    )


def _required_level(normalized_requirement: str) -> int | None:
    for label, order in LEVEL_ORDER.items():
        if label in normalized_requirement:
            return order
    return None


def _required_level_for_term(
    requirement_text: str,
    term: str,
    terms: tuple[str, ...],
) -> tuple[int | None, bool]:
    normalized = normalize_text(requirement_text)
    normalized_term = normalize_text(term)
    level_labels = "|".join(
        re.escape(label) for label in sorted(LEVEL_ORDER, key=len, reverse=True)
    )
    term_pattern = re.escape(normalized_term).replace(r"\ ", r"\s+")
    after = re.search(
        rf"(?<![a-z0-9]){term_pattern}(?![a-z0-9])\s+(?:nivel\s+)?({level_labels})\b",
        normalized,
    )
    before = re.search(
        rf"\b(?:nivel\s+)?({level_labels})\s+(?:em|de|com)?\s*"
        rf"(?<![a-z0-9]){term_pattern}(?![a-z0-9])",
        normalized,
    )
    matches = [match.group(1) for match in (after, before) if match is not None]
    levels = {LEVEL_ORDER[label] for label in matches}
    if len(levels) == 1:
        return next(iter(levels)), False
    if len(levels) > 1:
        return None, True
    if len(terms) == 1:
        return _required_level(normalized), False
    if any(label in normalized for label in LEVEL_ORDER):
        return None, True
    return None, False


def _level_label(level: int | None) -> str | None:
    if level is None:
        return None
    for label, order in LEVEL_ORDER.items():
        if order == level:
            return label
    return str(level)


def _normalized_level(value: Any) -> int | None:
    if value is None:
        return None
    normalized = normalize_text(str(value))
    for label, order in LEVEL_ORDER.items():
        if label in normalized:
            return order
    return None


def _matching_skills(
    normalized_requirement: str,
    skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        skill
        for skill in skills
        if _contains_term(normalized_requirement, str(skill["normalized_name"]))
    ]


def _adjacent_skills(
    normalized_requirement: str,
    skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    skill_names = {str(skill["normalized_name"]): skill for skill in skills}
    for group in ADJACENT_SKILL_GROUPS:
        requirement_terms = [term for term in group if _contains_term(normalized_requirement, term)]
        if not requirement_terms:
            continue
        for term in group:
            skill = skill_names.get(term)
            if skill is not None and term not in requirement_terms:
                result.append(skill)
    return result


def _evidence_for_skills(
    skills: list[dict[str, Any]],
    profile_data: dict[str, Any],
) -> list[dict[str, str]]:
    skill_ids = {int(skill["id"]) for skill in skills}
    evidence: list[dict[str, str]] = []
    for item in profile_data["evidences"]:
        if item["skill_id"] in skill_ids:
            evidence.append(
                {
                    "title": str(item["title"]),
                    "type": str(item["type"]),
                    "source_ref": str(item["source_ref"] or ""),
                }
            )
    return evidence


def _score(evaluations: list[RequirementEvaluation]) -> tuple[int, dict[str, Any]]:
    points = {
        RequirementMatchStatus.MATCHED: 1.0,
        RequirementMatchStatus.PARTIAL: 0.6,
        RequirementMatchStatus.NOT_PROVEN: 0.25,
        RequirementMatchStatus.AMBIGUOUS: 0.15,
        RequirementMatchStatus.NOT_MATCHED: 0.0,
    }
    possible = sum(item.weight for item in evaluations) or 1
    earned = sum(points[item.status] * item.weight for item in evaluations)
    by_status = {
        status.value: sum(1 for item in evaluations if item.status is status)
        for status in RequirementMatchStatus
    }
    return round((earned / possible) * 100), {
        "possible": possible,
        "earned": earned,
        "by_status": by_status,
        "rules_version": PROFILE_RULES_VERSION,
    }


def _attention_points(evaluations: list[RequirementEvaluation]) -> list[str]:
    return [
        item.requirement.text
        for item in evaluations
        if item.requirement.kind is RequirementKind.MANDATORY
        and item.status
        in {
            RequirementMatchStatus.NOT_PROVEN,
            RequirementMatchStatus.NOT_MATCHED,
            RequirementMatchStatus.AMBIGUOUS,
        }
    ]


def _summary(score: int, evaluations: list[RequirementEvaluation]) -> str:
    matched = sum(1 for item in evaluations if item.status is RequirementMatchStatus.MATCHED)
    partial = sum(1 for item in evaluations if item.status is RequirementMatchStatus.PARTIAL)
    not_proven = sum(
        1
        for item in evaluations
        if item.status in {RequirementMatchStatus.NOT_PROVEN, RequirementMatchStatus.AMBIGUOUS}
    )
    not_matched = sum(
        1 for item in evaluations if item.status is RequirementMatchStatus.NOT_MATCHED
    )
    return (
        f"Compatibilidade {score}/100: {matched} atendidos, {partial} parciais, "
        f"{not_proven} nao comprovados/ambiguos e {not_matched} nao atendidos."
    )


def _required_text(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} nao pode ficar vazio")
    return text


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    normalized_text = normalize_text(text)
    escaped_term = re.escape(normalize_text(term)).replace("\\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){escaped_term}(?![a-z0-9])", normalized_text) is not None
