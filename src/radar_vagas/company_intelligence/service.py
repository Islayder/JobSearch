from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import (
    CompanyInformationSourceType,
    RequirementMatchStatus,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.persistence.models import (
    Company,
    CompanyFact,
    CompanyProfile,
    CompanyReviewSnapshot,
    InterviewPreparation,
    Job,
    JobProfileComparison,
    ProfessionalProfileVersion,
)
from radar_vagas.profile.service import current_comparison_for_job

MAX_TEXT = 3000
MAX_LIST_ITEMS = 12
TECH_TERMS = (
    "Python",
    "SQL",
    "Power BI",
    "Power Query",
    "Excel",
    "Tableau",
    "Looker",
    "ETL",
    "Git",
    "GitHub",
    "Docker",
    "AWS",
    "Azure",
    "GCP",
)


@dataclass(frozen=True)
class CompanyProfileInput:
    name: str
    official_website: str | None = None
    industry: str | None = None
    company_size: str | None = None
    location: str | None = None
    description: str | None = None
    sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompanyFactInput:
    category: str
    content: str
    origin_type: CompanyInformationSourceType
    source_url: str | None = None
    source_date: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class CompanyReviewSnapshotInput:
    platform: str
    overall_rating: float | None = None
    review_count: int | None = None
    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    period: str | None = None
    source_url: str | None = None
    source_note: str | None = None


def upsert_company_profile(
    session: Session,
    company_id: int,
    data: CompanyProfileInput,
) -> CompanyProfile:
    company = _company(session, company_id)
    profile = company.intelligence_profile
    if profile is None:
        profile = CompanyProfile(
            company_id=company.id,
            name=_required(data.name, "nome da empresa"),
            created_at=utc_now(),
        )
        session.add(profile)
    profile.name = _required(data.name, "nome da empresa")
    profile.official_website = _clean_url(data.official_website)
    profile.industry = _clean_text(data.industry)
    profile.company_size = _clean_text(data.company_size)
    profile.location = _clean_text(data.location)
    profile.description = _clean_text(data.description, limit=MAX_TEXT)
    profile.sources_json = _json_dump(_clean_list(data.sources))
    profile.retrieved_at = utc_now()
    profile.updated_at = utc_now()
    session.flush()
    return profile


def add_company_fact(
    session: Session,
    company_id: int,
    data: CompanyFactInput,
) -> CompanyFact:
    company = _company(session, company_id)
    fact = CompanyFact(
        company_id=company.id,
        category=_required(data.category, "categoria"),
        content=_required(data.content, "conteudo")[:MAX_TEXT],
        origin_type=data.origin_type,
        source_url=_clean_url(data.source_url),
        source_date=_clean_text(data.source_date, limit=80),
        note=_clean_text(data.note, limit=MAX_TEXT),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(fact)
    session.flush()
    return fact


def add_company_review_snapshot(
    session: Session,
    company_id: int,
    data: CompanyReviewSnapshotInput,
) -> CompanyReviewSnapshot:
    company = _company(session, company_id)
    snapshot = CompanyReviewSnapshot(
        company_id=company.id,
        platform=_required(data.platform, "plataforma"),
        overall_rating=_rating(data.overall_rating),
        review_count=_positive_int(data.review_count, "quantidade de relatos"),
        positives_json=_json_dump(_clean_list(data.positives)),
        negatives_json=_json_dump(_clean_list(data.negatives)),
        period=_clean_text(data.period, limit=120),
        source_url=_clean_url(data.source_url),
        source_note=_clean_text(data.source_note, limit=MAX_TEXT),
        employee_reports_notice="relatos de funcionarios",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def generate_interview_preparation(
    session: Session,
    job_id: int,
    *,
    profile_version_id: int | None = None,
) -> InterviewPreparation:
    job = _job_for_preparation(session, job_id)
    profile_version = _profile_version(session, profile_version_id)
    comparison = current_comparison_for_job(job, profile_version) if profile_version else None
    company = job.company
    summary = _preparation_summary(job, company, profile_version)
    preparation = InterviewPreparation(
        job_id=job.id,
        profile_version_id=profile_version.id if profile_version else None,
        company_id=company.id,
        summary=summary,
        likely_questions_json=_json_dump(_likely_questions(job, profile_version)),
        relevant_experiences_json=_json_dump(_relevant_experiences(job, profile_version)),
        gaps_json=_json_dump(_gaps(comparison, profile_version)),
        interviewer_questions_json=_json_dump(_questions_for_interviewer(company, job)),
        checklist_json=_json_dump(_checklist(job, company, profile_version)),
        sources_json=_json_dump(_sources_used(job, company, profile_version, comparison)),
        generated_at=utc_now(),
        created_at=utc_now(),
    )
    session.add(preparation)
    session.flush()
    return preparation


def latest_interview_preparation(job: Job) -> InterviewPreparation | None:
    if not job.interview_preparations:
        return None
    return max(job.interview_preparations, key=lambda item: (item.generated_at, item.id))


def parse_company_information_source_type(value: str) -> CompanyInformationSourceType:
    try:
        return parse_enum_value(CompanyInformationSourceType, value)
    except ValueError as exc:
        raise RadarError(f"Tipo de origem invalido: {exc}") from exc


def _company(session: Session, company_id: int) -> Company:
    company = session.get(Company, company_id)
    if company is None:
        raise RadarError("Empresa nao encontrada.")
    return company


def _job_for_preparation(session: Session, job_id: int) -> Job:
    job = session.scalar(
        select(Job)
        .options(
            selectinload(Job.company).selectinload(Company.intelligence_profile),
            selectinload(Job.company).selectinload(Company.facts),
            selectinload(Job.company).selectinload(Company.review_snapshots),
            selectinload(Job.profile_comparisons).selectinload(
                JobProfileComparison.requirement_matches
            ),
            selectinload(Job.profile_comparisons).selectinload(
                JobProfileComparison.profile_version
            ),
        )
        .where(Job.id == job_id)
    )
    if job is None:
        raise RadarError("Vaga nao encontrada.")
    return job


def _profile_version(
    session: Session,
    profile_version_id: int | None,
) -> ProfessionalProfileVersion | None:
    statement = (
        select(ProfessionalProfileVersion)
        .options(
            selectinload(ProfessionalProfileVersion.profile),
            selectinload(ProfessionalProfileVersion.skills),
            selectinload(ProfessionalProfileVersion.experiences),
            selectinload(ProfessionalProfileVersion.projects),
            selectinload(ProfessionalProfileVersion.education),
            selectinload(ProfessionalProfileVersion.languages),
        )
        .where(
            ProfessionalProfileVersion.id == profile_version_id
            if profile_version_id is not None
            else ProfessionalProfileVersion.is_active.is_(True)
        )
        .order_by(
            ProfessionalProfileVersion.created_at.desc(),
            ProfessionalProfileVersion.id.desc(),
        )
    )
    return session.scalar(statement)


def _preparation_summary(
    job: Job,
    company: Company,
    profile_version: ProfessionalProfileVersion | None,
) -> str:
    profile_text = (
        f"perfil v{profile_version.version_number}" if profile_version else "perfil nao encontrado"
    )
    facts = _official_facts(company)
    company_context = facts[0].content if facts else "informacao oficial nao encontrada"
    return (
        f"Preparacao deterministica para {job.canonical_title} em "
        f"{company.canonical_name}, usando {profile_text}. "
        f"Contexto oficial: {company_context[:240]}."
    )


def _likely_questions(
    job: Job,
    profile_version: ProfessionalProfileVersion | None,
) -> list[str]:
    technologies = _job_technologies(job)
    questions = [
        f"Como voce explicaria sua experiencia com {technology} aplicada a esta vaga?"
        for technology in technologies[:4]
    ]
    if profile_version and profile_version.experiences:
        questions.append(
            "Conte uma experiencia do seu perfil que demonstre organizacao, "
            "analise e validacao de dados."
        )
    if job.requirements:
        questions.append("Como voce priorizaria os requisitos descritos na vaga no primeiro mes?")
    if not questions:
        questions.append("Quais exemplos concretos do seu perfil melhor se conectam a esta vaga?")
    return questions[:6]


def _relevant_experiences(
    job: Job,
    profile_version: ProfessionalProfileVersion | None,
) -> list[str]:
    if profile_version is None:
        return ["perfil nao encontrado"]
    job_terms = {normalize_text(term) for term in _job_technologies(job)}
    rows: list[str] = []
    for experience in profile_version.experiences:
        text = " ".join(
            item
            for item in (
                experience.title,
                experience.organization,
                experience.description,
                experience.skills_json,
            )
            if item
        )
        if not job_terms or any(term in normalize_text(text) for term in job_terms):
            rows.append(_experience_label(experience.title, experience.organization, text))
    for project in profile_version.projects:
        text = " ".join(
            item for item in (project.name, project.description, project.technologies_json) if item
        )
        if not job_terms or any(term in normalize_text(text) for term in job_terms):
            rows.append(f"Projeto: {project.name}")
    return rows[:MAX_LIST_ITEMS] or ["nao encontrado"]


def _gaps(
    comparison: JobProfileComparison | None,
    profile_version: ProfessionalProfileVersion | None,
) -> list[str]:
    if profile_version is None:
        return ["perfil ativo nao encontrado"]
    if comparison is None:
        return ["comparacao atual com perfil nao encontrada"]
    gap_statuses = {
        RequirementMatchStatus.NOT_PROVEN,
        RequirementMatchStatus.NOT_MATCHED,
        RequirementMatchStatus.AMBIGUOUS,
    }
    gaps = [
        f"{match.requirement_text}: {match.match_status.value}"
        for match in comparison.requirement_matches
        if match.match_status in gap_statuses
    ]
    return gaps[:MAX_LIST_ITEMS] or ["nao encontrado"]


def _questions_for_interviewer(company: Company, job: Job) -> list[str]:
    profile = company.intelligence_profile
    questions = [
        "Como a area mede sucesso para esta vaga nos primeiros meses?",
        "Quais dados, ferramentas e rituais fazem parte da rotina da equipe?",
    ]
    if profile and profile.industry:
        questions.append(f"Como o setor {profile.industry} influencia os desafios da area?")
    if not _official_facts(company):
        questions.append("Onde encontro informacoes oficiais atualizadas sobre a empresa e a area?")
    if job.work_model:
        questions.append(
            "Como a equipe organiza colaboracao, acompanhamento e feedback nesse modelo?"
        )
    return questions[:6]


def _checklist(
    job: Job,
    company: Company,
    profile_version: ProfessionalProfileVersion | None,
) -> list[str]:
    items = [
        "Revisar a pagina oficial da vaga antes da entrevista.",
        "Separar um exemplo com problema, acao, ferramenta usada e resultado.",
        "Preparar perguntas para o entrevistador sem assumir informacoes nao encontradas.",
    ]
    if profile_version is None:
        items.append("Ativar ou revisar o perfil profissional antes da entrevista.")
    if not _official_facts(company):
        items.append("Adicionar informacoes oficiais da empresa quando disponiveis.")
    if company.review_snapshots:
        items.append("Ler relatos de funcionarios como percepcao, nao como fato oficial.")
    return items


def _sources_used(
    job: Job,
    company: Company,
    profile_version: ProfessionalProfileVersion | None,
    comparison: JobProfileComparison | None,
) -> list[dict[str, str]]:
    sources = [
        {
            "type": CompanyInformationSourceType.RADAR_INFERENCE.value,
            "label": f"Vaga local {job.id}",
        }
    ]
    if profile_version:
        sources.append(
            {
                "type": CompanyInformationSourceType.RADAR_INFERENCE.value,
                "label": f"Perfil v{profile_version.version_number}",
            }
        )
    if comparison:
        sources.append(
            {
                "type": CompanyInformationSourceType.RADAR_INFERENCE.value,
                "label": f"Comparacao {comparison.id}",
            }
        )
    if company.intelligence_profile:
        sources.append(
            {
                "type": CompanyInformationSourceType.OFFICIAL_INFO.value,
                "label": "Perfil local da empresa",
            }
        )
    for fact in sorted(company.facts, key=lambda item: (item.origin_type.value, item.id)):
        sources.append({"type": fact.origin_type.value, "label": fact.category})
    for snapshot in sorted(company.review_snapshots, key=lambda item: (item.platform, item.id)):
        sources.append(
            {
                "type": CompanyInformationSourceType.EMPLOYEE_REPORT.value,
                "label": f"{snapshot.platform}: relatos de funcionarios",
            }
        )
    return sources[:MAX_LIST_ITEMS]


def _official_facts(company: Company) -> list[CompanyFact]:
    return sorted(
        [
            fact
            for fact in company.facts
            if fact.origin_type is CompanyInformationSourceType.OFFICIAL_INFO
        ],
        key=lambda item: item.id,
    )


def _job_technologies(job: Job) -> list[str]:
    values = _json_load(job.technologies_json)
    technologies = [str(item).strip() for item in values if str(item).strip()]
    text = " ".join(
        item
        for item in (job.description, job.requirements, job.responsibilities, job.department)
        if item
    )
    normalized = normalize_text(text)
    for term in TECH_TERMS:
        if normalize_text(term) in normalized and term not in technologies:
            technologies.append(term)
    return technologies[:MAX_LIST_ITEMS]


def _experience_label(title: str, organization: str | None, text: str) -> str:
    if organization:
        return f"{title} em {organization}"
    return title or text[:120]


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str | None) -> list[Any]:
    if not value:
        return []
    loaded = json.loads(value)
    return loaded if isinstance(loaded, list) else []


def _clean_text(value: str | None, *, limit: int = 255) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:limit] or None


def _required(value: str | None, label: str) -> str:
    text = _clean_text(value, limit=MAX_TEXT)
    if not text:
        raise RadarError(f"Preencha {label}.")
    return text


def _clean_list(values: list[str]) -> list[str]:
    cleaned = [_clean_text(value, limit=500) for value in values]
    return [value for value in cleaned if value][:MAX_LIST_ITEMS]


def _clean_url(value: str | None) -> str | None:
    text = _clean_text(value, limit=1000)
    if not text:
        return None
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        raise RadarError("URLs de empresa devem comecar com http:// ou https://.")
    return text


def _rating(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 0 or value > 5:
        raise RadarError("Avaliacao geral deve ficar entre 0 e 5.")
    return round(value, 2)


def _positive_int(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise RadarError(f"{label} deve ser positivo.")
    return value
