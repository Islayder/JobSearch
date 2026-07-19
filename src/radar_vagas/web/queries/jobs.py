from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.applications.review import current_review_state
from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_text
from radar_vagas.domain.enums import (
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    ReadableEnum,
    RelevanceStatus,
    ReviewState,
    WorkModel,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.models import (
    Application,
    Company,
    Decision,
    Job,
    JobProfileComparison,
    Posting,
)
from radar_vagas.profile.service import current_comparison_for_job
from radar_vagas.web.queries.common import Page
from radar_vagas.web.queries.profiles import active_profile_version
from radar_vagas.web.queries.review import effective_review_state_condition

JobSort = Literal[
    "recommendation",
    "score",
    "compatibility",
    "publication",
    "newest",
    "first-seen",
    "company",
    "title",
]

JOB_TABS = {
    "novas": "Novas",
    "recomendadas": "Recomendadas",
    "favoritas": "Favoritas",
    "aplicadas": "Aplicadas",
    "aguardando-revisao": "Aguardando revisao",
    "descartadas": "Descartadas",
    "encerradas": "Encerradas",
}
DEFAULT_HIDDEN_STATUSES = {
    JobStatus.APPLIED,
    JobStatus.DISMISSED,
    JobStatus.CLOSED,
    JobStatus.EXPIRED,
    JobStatus.ARCHIVED,
}


@dataclass(frozen=True)
class JobFilters:
    q: str | None = None
    status: JobStatus | None = None
    review: ReviewState | None = None
    employment_type: EmploymentType | None = None
    work_model: WorkModel | None = None
    provider: str | None = None
    eligibility: EligibilityStatus | None = None
    relevance: RelevanceStatus | None = None
    min_ranking: int | None = None
    min_compatibility: int | None = None
    company: str | None = None
    city: str | None = None
    state: str | None = None
    only_with_compatibility: bool = False
    only_without_compatibility: bool = False
    tab: str | None = None


def parse_job_filters(raw: dict[str, str | None]) -> JobFilters:
    tab = _text(raw.get("tab"))
    if tab and tab not in JOB_TABS:
        raise RadarError("Filtro de aba invalido.")
    only_with = _bool(raw.get("only_with_compatibility"))
    only_without = _bool(raw.get("only_without_compatibility"))
    if only_with and only_without:
        raise RadarError("Escolha vagas com ou sem compatibilidade, nao ambos.")
    return JobFilters(
        q=_text(raw.get("q")),
        status=_enum(JobStatus, raw.get("status"), "status da vaga"),
        review=_enum(ReviewState, raw.get("review"), "estado de revisao"),
        employment_type=_enum(EmploymentType, raw.get("employment_type"), "tipo de vaga"),
        work_model=_enum(WorkModel, raw.get("work_model"), "modelo de trabalho"),
        provider=_text(raw.get("provider")),
        eligibility=_enum(EligibilityStatus, raw.get("eligibility"), "elegibilidade"),
        relevance=_enum(RelevanceStatus, raw.get("relevance"), "relevancia"),
        min_ranking=_optional_score(raw.get("min_ranking"), "ranking minimo"),
        min_compatibility=_optional_score(raw.get("min_compatibility"), "compatibilidade minima"),
        company=_text(raw.get("company")),
        city=_text(raw.get("city")),
        state=_text(raw.get("state")),
        only_with_compatibility=only_with,
        only_without_compatibility=only_without,
        tab=tab,
    )


def jobs_page(
    session: Session,
    *,
    filters: JobFilters,
    page: int,
    page_size: int,
    sort: str,
) -> Page[Job]:
    page = max(1, page)
    page_size = min(max(5, page_size), 100)
    selected_sort = _parse_sort(sort)
    base = select(Job.id).join(Company)
    base = _apply_job_filters(base, filters)
    statement = select(Job).where(Job.id.in_(base))
    statement = statement.options(
        selectinload(Job.company),
        selectinload(Job.decision),
        selectinload(Job.review_state),
        selectinload(Job.postings).selectinload(Posting.source),
        selectinload(Job.applications),
        selectinload(Job.profile_comparisons),
    )
    items = list(session.scalars(statement).unique().all())
    _attach_current_comparisons(session, items)
    items = _apply_current_comparison_filters(items, filters)
    items = _sort_loaded_jobs(items, selected_sort)
    total = len(items)
    start = (page - 1) * page_size
    return Page(items=items[start : start + page_size], page=page, page_size=page_size, total=total)


def job_detail(session: Session, job_id: int) -> Job | None:
    return session.scalar(
        select(Job)
        .options(
            selectinload(Job.company),
            selectinload(Job.postings).selectinload(Posting.source),
            selectinload(Job.decision),
            selectinload(Job.review_state),
            selectinload(Job.review_events),
            selectinload(Job.applications).selectinload(Application.events),
            selectinload(Job.career_events),
            selectinload(Job.profile_comparisons).selectinload(
                JobProfileComparison.requirement_matches
            ),
            selectinload(Job.profile_comparisons).selectinload(
                JobProfileComparison.profile_version
            ),
        )
        .where(Job.id == job_id)
    )


def latest_comparison(job: Job) -> JobProfileComparison | None:
    if not job.profile_comparisons:
        return None
    return max(job.profile_comparisons, key=lambda comparison: comparison.created_at)


def historical_comparisons(job: Job) -> list[JobProfileComparison]:
    return sorted(
        job.profile_comparisons,
        key=lambda comparison: (comparison.created_at, comparison.id),
        reverse=True,
    )


def review_state_for(job: Job) -> ReviewState:
    return current_review_state(job)


def valid_job_actions(job: Job) -> dict[str, bool]:
    state = current_review_state(job)
    has_application = bool(job.applications)
    blocked = job.status in {JobStatus.CLOSED, JobStatus.EXPIRED, JobStatus.ARCHIVED}
    return {
        "seen": not blocked and not has_application and state is ReviewState.UNREVIEWED,
        "shortlist": not blocked
        and not has_application
        and state
        in {
            ReviewState.UNREVIEWED,
            ReviewState.SEEN,
        },
        "unshortlist": not blocked and not has_application and state is ReviewState.SHORTLISTED,
        "dismiss": not blocked
        and not has_application
        and state
        in {
            ReviewState.UNREVIEWED,
            ReviewState.SEEN,
            ReviewState.SHORTLISTED,
        },
        "restore": not has_application
        and (job.status is JobStatus.DISMISSED or state is ReviewState.DISMISSED),
        "apply": not blocked and not has_application and state is not ReviewState.DISMISSED,
        "compare": True,
        "event": True,
    }


def _apply_job_filters(statement: Select[tuple[int]], filters: JobFilters) -> Select[tuple[int]]:
    if filters.q:
        normalized_text = normalize_text(filters.q)
        normalized_company = normalize_company_name(filters.q)
        statement = statement.where(
            or_(
                Job.normalized_title.contains(normalized_text),
                Company.normalized_name.contains(normalized_company),
                Job.description.contains(filters.q),
            )
        )
    if filters.company:
        statement = statement.where(
            Company.normalized_name.contains(normalize_company_name(filters.company))
        )
    if filters.city:
        statement = statement.where(func.lower(Job.city).contains(filters.city.lower()))
    if filters.state:
        statement = statement.where(func.lower(Job.state).contains(filters.state.lower()))
    if filters.status:
        statement = statement.where(Job.status == filters.status)
    if filters.review:
        statement = statement.where(effective_review_state_condition(filters.review))
    if filters.employment_type:
        statement = statement.where(Job.employment_type == filters.employment_type)
    if filters.work_model:
        statement = statement.where(Job.work_model == filters.work_model)
    if filters.provider:
        provider = filters.provider.lower()
        statement = statement.where(
            exists(
                select(1).where(
                    Posting.job_id == Job.id,
                    func.lower(Posting.provider) == provider,
                )
            )
        )
    if filters.eligibility:
        statement = statement.where(
            Job.decision.has(Decision.eligibility_status == filters.eligibility)
        )
    if filters.relevance:
        statement = statement.where(
            Job.decision.has(Decision.relevance_status == filters.relevance)
        )
    if filters.min_ranking is not None:
        statement = statement.where(Job.decision.has(Decision.ranking_score >= filters.min_ranking))
    statement = _apply_tab(statement, filters.tab)
    if (
        not filters.tab
        and not filters.status
        and not filters.review
        and not filters.only_without_compatibility
    ):
        statement = statement.where(Job.status.not_in(DEFAULT_HIDDEN_STATUSES))
    return statement


def _apply_tab(statement: Select[tuple[int]], tab: str | None) -> Select[tuple[int]]:
    if tab == "novas":
        return statement.where(Job.status.in_([JobStatus.NEW, JobStatus.PENDING_REVIEW]))
    if tab == "recomendadas":
        return statement.where(Job.status.in_([JobStatus.RECOMMENDED, JobStatus.ELIGIBLE]))
    if tab == "favoritas":
        return statement.where(Job.review_state.has(state=ReviewState.SHORTLISTED))
    if tab == "aplicadas":
        return statement.where(Job.status == JobStatus.APPLIED)
    if tab == "aguardando-revisao":
        return statement.where(effective_review_state_condition(ReviewState.UNREVIEWED))
    if tab == "descartadas":
        return statement.where(Job.status == JobStatus.DISMISSED)
    if tab == "encerradas":
        return statement.where(Job.status.in_([JobStatus.CLOSED, JobStatus.EXPIRED]))
    return statement


def _attach_current_comparisons(session: Session, jobs: list[Job]) -> None:
    profile_version = active_profile_version(session)
    for job in jobs:
        job.current_comparison = current_comparison_for_job(job, profile_version)  # type: ignore[attr-defined]


def _apply_current_comparison_filters(jobs: list[Job], filters: JobFilters) -> list[Job]:
    filtered: list[Job] = []
    for job in jobs:
        comparison = getattr(job, "current_comparison", None)
        if filters.only_with_compatibility and comparison is None:
            continue
        if filters.only_without_compatibility and comparison is not None:
            continue
        if filters.min_compatibility is not None and (
            comparison is None or comparison.overall_score < filters.min_compatibility
        ):
            continue
        filtered.append(job)
    return filtered


def _sort_loaded_jobs(jobs: list[Job], sort: JobSort) -> list[Job]:
    if sort == "publication" or sort == "newest":
        return sorted(jobs, key=lambda job: (_date_value(job.published_at), job.id), reverse=True)
    if sort == "first-seen":
        return sorted(jobs, key=lambda job: (_date_value(_first_seen(job)), job.id), reverse=True)
    if sort == "compatibility":
        return sorted(jobs, key=lambda job: (_current_score(job), job.id), reverse=True)
    if sort == "company":
        return sorted(jobs, key=lambda job: (job.company.normalized_name, job.id))
    if sort == "title":
        return sorted(jobs, key=lambda job: (job.normalized_title, job.id))
    return sorted(
        jobs,
        key=lambda job: (
            job.decision.ranking_score if job.decision and job.decision.ranking_score else -1,
            job.decision.relevance_score if job.decision and job.decision.relevance_score else -1,
            _current_score(job),
            -job.id,
        ),
        reverse=True,
    )


def _current_score(job: Job) -> int:
    comparison = getattr(job, "current_comparison", None)
    return comparison.overall_score if comparison is not None else -1


def _first_seen(job: Job) -> datetime | None:
    dates = [posting.first_seen_at for posting in job.postings if posting.first_seen_at is not None]
    return min(dates) if dates else None


def _date_value(value: datetime | None) -> float:
    if value is None:
        return 0
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.timestamp()


def _parse_sort(value: str) -> JobSort:
    aliases = {"score": "recommendation", "newest": "publication"}
    normalized = aliases.get(value, value)
    allowed = {
        "recommendation",
        "compatibility",
        "publication",
        "first-seen",
        "company",
        "title",
    }
    if normalized not in allowed:
        raise RadarError("Ordenacao de vagas invalida.")
    return normalized  # type: ignore[return-value]


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _enum[EnumType: ReadableEnum](
    enum_type: type[EnumType],
    value: str | None,
    label: str,
) -> EnumType | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return parse_enum_value(enum_type, text)
    except ValueError as exc:
        raise RadarError(f"Filtro invalido para {label}: {exc}") from exc


def _optional_score(value: str | None, label: str) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise RadarError(f"{label} deve ser numerico.") from exc
    if parsed < 0 or parsed > 100:
        raise RadarError(f"{label} deve ficar entre 0 e 100.")
    return parsed


def _bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "on", "yes", "sim"}
