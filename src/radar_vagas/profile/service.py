from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
    ProfileEvidence,
    ProfileProject,
    ProfileSkill,
    Resume,
    ResumeVersion,
)
from radar_vagas.relevance.service import technologies_from_json

PROFILE_RULES_VERSION = "2026-07-19.profile.1"
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


@dataclass(frozen=True)
class RequirementEvaluation:
    requirement: RequirementCandidate
    status: RequirementMatchStatus
    evidence: list[dict[str, str]]
    explanation: str
    weight: int


@dataclass(frozen=True)
class ProfileComparisonResult:
    comparison_id: int
    job_id: int
    profile_version_id: int
    overall_score: int
    summary: str
    attention_points: list[str]
    requirements: list[RequirementEvaluation]


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
            is_active=True,
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
            _activate_profile_version(session, existing)
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
    if activate:
        _deactivate_profile_versions(session, profile.id)
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
        is_active=activate,
        created_at=utc_now(),
    )
    session.add(version)
    session.flush()
    _store_profile_sections(session, version, document)
    _store_resume_version(session, version, effective_name, file_path, content_hash)
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
    version = session.scalar(
        select(ProfessionalProfileVersion)
        .where(ProfessionalProfileVersion.is_active.is_(True))
        .order_by(
            ProfessionalProfileVersion.created_at.desc(),
            ProfessionalProfileVersion.id.desc(),
        )
    )
    if version is None:
        raise RadarError("Nenhum perfil profissional importado.")
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

    profile_data = _profile_data(profile_version)
    requirements = extract_requirements(job)
    evaluations = [
        evaluate_requirement(requirement, profile_data, job.employment_type)
        for requirement in requirements
    ]
    score, breakdown = _score(evaluations)
    attention_points = _attention_points(evaluations)
    summary = _summary(score, evaluations)
    comparison = session.scalar(
        select(JobProfileComparison).where(
            JobProfileComparison.job_id == job.id,
            JobProfileComparison.profile_version_id == profile_version.id,
        )
    )
    if comparison is None:
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
            created_at=utc_now(),
        )
        session.add(comparison)
        session.flush()
    else:
        comparison.overall_score = score
        comparison.summary = summary
        comparison.score_breakdown_json = json.dumps(
            breakdown,
            ensure_ascii=False,
            sort_keys=True,
        )
        comparison.attention_points_json = json.dumps(
            attention_points,
            ensure_ascii=False,
            sort_keys=True,
        )
        comparison.rules_version = PROFILE_RULES_VERSION
        comparison.created_at = utc_now()
        comparison.requirement_matches.clear()
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


def extract_requirements(job: Job) -> list[RequirementCandidate]:
    candidates: list[RequirementCandidate] = []
    for raw_line in _split_requirement_text(job.requirements):
        candidates.append(RequirementCandidate(raw_line, _requirement_kind(raw_line)))
    for technology in technologies_from_json(job.technologies_json):
        text = f"Conhecimento em {technology}"
        if not _contains_candidate(candidates, text):
            candidates.append(RequirementCandidate(text, RequirementKind.MANDATORY))
    if not candidates:
        for raw_line in _split_requirement_text(job.description):
            if _looks_like_requirement(raw_line):
                candidates.append(RequirementCandidate(raw_line, _requirement_kind(raw_line)))
    if not candidates:
        candidates.append(
            RequirementCandidate(
                "Requisitos descritos de forma generica na vaga",
                RequirementKind.MANDATORY,
            )
        )
    return _dedupe_candidates(candidates)


def evaluate_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
    employment_type: EmploymentType,
) -> RequirementEvaluation:
    normalized = normalize_text(requirement.text)
    weight = 3 if requirement.kind is RequirementKind.MANDATORY else 1
    if (
        employment_type is EmploymentType.INTERNSHIP
        and requirement.kind is RequirementKind.MANDATORY
    ):
        weight = 2
    if _is_generic_requirement(normalized):
        return RequirementEvaluation(
            requirement=requirement,
            status=RequirementMatchStatus.AMBIGUOUS,
            evidence=[],
            explanation=(
                "Requisito generico; revisar manualmente sem assumir ausencia de competencia."
            ),
            weight=weight,
        )

    language_result = _language_requirement(requirement, profile_data)
    if language_result is not None:
        return language_result

    exact_skills = _matching_skills(normalized, profile_data["skills"])
    if exact_skills:
        evidences = _evidence_for_skills(exact_skills, profile_data)
        if evidences:
            return RequirementEvaluation(
                requirement=requirement,
                status=RequirementMatchStatus.MATCHED,
                evidence=evidences,
                explanation="Competencia encontrada no perfil com evidencia associada.",
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

    status = (
        RequirementMatchStatus.NOT_PROVEN
        if requirement.kind is RequirementKind.DESIRABLE
        else RequirementMatchStatus.NOT_MATCHED
    )
    return RequirementEvaluation(
        requirement=requirement,
        status=status,
        evidence=[],
        explanation=(
            "Diferencial ausente nao elimina a vaga automaticamente."
            if requirement.kind is RequirementKind.DESIRABLE
            else "Nao ha evidencia estruturada para este requisito obrigatorio."
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
    resume = Resume(
        name=f"Curriculo base - {profile_name}",
        is_base=True,
        source_path=str(file_path),
        content_hash=content_hash,
        created_at=utc_now(),
    )
    session.add(resume)
    session.flush()
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


def _activate_profile_version(session: Session, version: ProfessionalProfileVersion) -> None:
    _deactivate_profile_versions(session, version.profile_id)
    version.is_active = True
    version.profile.is_active = True
    version.profile.updated_at = utc_now()


def _deactivate_profile_versions(session: Session, profile_id: int) -> None:
    versions = session.scalars(
        select(ProfessionalProfileVersion).where(
            ProfessionalProfileVersion.profile_id == profile_id
        )
    ).all()
    for version in versions:
        version.is_active = False


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
            "evidence": json.loads(language.evidence_json),
        }
        for language in profile_version.languages
    ]
    return {"skills": skills, "evidences": evidences, "languages": languages}


def _split_requirement_text(value: str | None) -> list[str]:
    if not value:
        return []
    pieces = re.split(r"[\n\r;\u2022]+", value)
    return [_strip_bullet(piece) for piece in pieces if _strip_bullet(piece)]


def _strip_bullet(value: str) -> str:
    return re.sub(r"^\s*[-*0-9.)]+\s*", "", value).strip()


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


def _language_requirement(
    requirement: RequirementCandidate,
    profile_data: dict[str, Any],
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
                weight=3 if requirement.kind is RequirementKind.MANDATORY else 1,
            )
    return RequirementEvaluation(
        requirement=requirement,
        status=(
            RequirementMatchStatus.NOT_PROVEN
            if requirement.kind is RequirementKind.DESIRABLE
            else RequirementMatchStatus.NOT_MATCHED
        ),
        evidence=[],
        explanation="Idioma nao comprovado no perfil estruturado.",
        weight=3 if requirement.kind is RequirementKind.MANDATORY else 1,
    )


def _required_level(normalized_requirement: str) -> int:
    for label, order in LEVEL_ORDER.items():
        if label in normalized_requirement:
            return order
    return 1


def _normalized_level(value: str) -> int:
    normalized = normalize_text(value)
    for label, order in LEVEL_ORDER.items():
        if label in normalized:
            return order
    return 1


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
    escaped_term = re.escape(normalize_text(term)).replace("\\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){escaped_term}(?![a-z0-9])", text) is not None
