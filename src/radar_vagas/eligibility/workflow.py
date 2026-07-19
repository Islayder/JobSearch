import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar_vagas.config.loaders import (
    blocked_company_reasons,
    load_eligibility_rules,
    load_ranking_weights,
    load_relevance_rules,
)
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    EligibilityStatus,
    JobStatus,
    RelevanceStatus,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.eligibility.service import (
    EligibilityInput,
    EligibilityResult,
    evaluate_eligibility,
)
from radar_vagas.persistence.models import Application, Decision, Job
from radar_vagas.ranking.service import RankingInput, rank_job
from radar_vagas.relevance.service import (
    build_role_relevance_input_from_job,
    evaluate_role_relevance,
)


@dataclass(frozen=True)
class EvaluationSummary:
    total: int
    eligible: int
    manual_review: int
    ineligible: int
    track_only: int
    recommended: int


@dataclass(frozen=True)
class ReevaluateJobChange:
    job_id: int
    before: dict[str, Any]
    after: dict[str, Any]


@dataclass(frozen=True)
class ReevaluateJobsSummary:
    total: int
    changed: int
    unchanged: int
    dry_run: bool
    changes: list[ReevaluateJobChange]


def evaluate_all_jobs(session: Session, settings: Settings) -> EvaluationSummary:
    jobs = session.scalars(
        select(Job).where(Job.status.in_([JobStatus.NEW, JobStatus.PENDING_REVIEW]))
    ).all()
    counters = _EvaluationCounters(total=len(jobs))
    for job in jobs:
        result = evaluate_job_record(session, job, settings)
        counters.add(result.eligibility_status, job.status)
    return counters.to_summary()


def evaluate_job_by_id(session: Session, job_id: int, settings: Settings) -> Decision:
    job = session.get(Job, job_id)
    if job is None:
        raise RadarError(f"Vaga não encontrada: {job_id}")
    return evaluate_job_record(session, job, settings)


def evaluate_job_record(
    session: Session,
    job: Job,
    settings: Settings,
    *,
    job_status_override: JobStatus | None = None,
) -> Decision:
    rules = load_eligibility_rules(settings.config_dir)
    ranking_weights = load_ranking_weights(settings.config_dir)
    relevance_rules = load_relevance_rules(settings.config_dir)
    blocked_reasons = blocked_company_reasons(settings.config_dir)
    has_application = _has_any_application(session, job.id)

    eligibility = evaluate_eligibility(
        EligibilityInput(
            company_name=job.company.canonical_name,
            company_aliases=tuple(alias.alias for alias in job.company.aliases),
            company_is_blocked=job.company.is_blocked,
            job_status=job_status_override or job.status,
            employment_type=job.employment_type,
            work_model=job.work_model,
            city=job.city,
            state=job.state,
            remote_country_scope=job.remote_country_scope,
            hours_per_day=job.hours_per_day,
            has_existing_application=has_application,
            has_uninterpreted_course_requirement=job.has_uninterpreted_course_requirement,
        ),
        rules,
        blocked_reasons,
    )
    relevance = evaluate_role_relevance(
        build_role_relevance_input_from_job(job),
        relevance_rules,
    )
    effective_eligibility = _apply_relevance_to_eligibility(eligibility, relevance.status)

    ranking = rank_job(
        RankingInput(
            employment_type=job.employment_type,
            work_model=job.work_model,
            city=job.city,
            state=job.state,
            remote_country_scope=job.remote_country_scope,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            description=job.description,
            published_at=job.published_at,
            hours_per_day=job.hours_per_day,
            hours_per_week=job.hours_per_week,
            status=job_status_override or job.status,
            relevance_status=relevance.status,
        ),
        effective_eligibility.status,
        ranking_weights,
    )

    job.status = _next_job_status(
        current_status=job.status,
        eligibility_status=effective_eligibility.status,
        ranking_score=ranking.total if ranking is not None else None,
        recommended_min_score=ranking_weights.recommended_min_score,
        has_application=has_application,
    )
    job.updated_at = utc_now()

    decision = session.scalar(select(Decision).where(Decision.job_id == job.id))
    if decision is None:
        decision = Decision(
            job_id=job.id,
            eligibility_status=effective_eligibility.status,
            reason_code=effective_eligibility.reason_code,
            reason_text=effective_eligibility.reason_text,
            rules_version=effective_eligibility.rules_version,
        )
        session.add(decision)

    decision.eligibility_status = effective_eligibility.status
    decision.reason_code = effective_eligibility.reason_code
    decision.reason_text = effective_eligibility.reason_text
    decision.ranking_score = ranking.total if ranking is not None else None
    decision.ranking_breakdown_json = (
        json.dumps(ranking.breakdown, ensure_ascii=False, sort_keys=True)
        if ranking is not None
        else None
    )
    decision.evaluated_at = utc_now()
    decision.rules_version = effective_eligibility.rules_version
    decision.relevance_status = relevance.status
    decision.relevance_score = relevance.score
    decision.relevance_reason_json = json.dumps(
        relevance.reason, ensure_ascii=False, sort_keys=True
    )
    decision.relevance_rules_version = relevance.rules_version
    session.flush()
    return decision


def reevaluate_jobs(
    session: Session,
    settings: Settings,
    *,
    provider: str | None = None,
    status: JobStatus | None = None,
    only_missing_relevance: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
) -> ReevaluateJobsSummary:
    statement = select(Job).order_by(Job.id.asc())
    if status is not None:
        statement = statement.where(Job.status == status)
    if provider is not None:
        from radar_vagas.persistence.models import Posting

        statement = statement.where(Job.postings.any(Posting.provider == provider.strip().lower()))
    if only_missing_relevance:
        statement = statement.where(
            Job.decision.has(Decision.relevance_status.is_(None)) | ~Job.decision.has()
        )
    if limit is not None:
        statement = statement.limit(limit)

    jobs = session.scalars(statement).all()
    changes: list[ReevaluateJobChange] = []
    for job in jobs:
        before = _decision_snapshot(job)
        status_override = JobStatus.NEW if job.status is JobStatus.ARCHIVED else None
        evaluate_job_record(session, job, settings, job_status_override=status_override)
        session.flush()
        after = _decision_snapshot(job)
        if before != after:
            changes.append(ReevaluateJobChange(job_id=job.id, before=before, after=after))

    if dry_run:
        session.rollback()

    return ReevaluateJobsSummary(
        total=len(jobs),
        changed=len(changes),
        unchanged=len(jobs) - len(changes),
        dry_run=dry_run,
        changes=changes,
    )


def _apply_relevance_to_eligibility(
    eligibility: EligibilityResult,
    relevance_status: RelevanceStatus,
) -> EligibilityResult:
    if eligibility.status in {EligibilityStatus.INELIGIBLE, EligibilityStatus.TRACK_ONLY}:
        return eligibility
    if relevance_status is RelevanceStatus.UNRELATED:
        return EligibilityResult(
            status=EligibilityStatus.INELIGIBLE,
            reason_code="ROLE_RELEVANCE_UNRELATED",
            reason_text="Vaga fora das areas profissionais alvo para este perfil.",
            rules_version=eligibility.rules_version,
        )
    if relevance_status is RelevanceStatus.MANUAL_REVIEW:
        return EligibilityResult(
            status=EligibilityStatus.MANUAL_REVIEW,
            reason_code="ROLE_RELEVANCE_REVIEW",
            reason_text="Relevancia profissional insuficiente para aprovacao automatica.",
            rules_version=eligibility.rules_version,
        )
    return eligibility


def _has_any_application(session: Session, job_id: int) -> bool:
    count = session.scalar(select(func.count(Application.id)).where(Application.job_id == job_id))
    return bool(count)


def _next_job_status(
    *,
    current_status: JobStatus,
    eligibility_status: EligibilityStatus,
    ranking_score: int | None,
    recommended_min_score: int,
    has_application: bool,
) -> JobStatus:
    if current_status in {JobStatus.APPLIED, JobStatus.DISMISSED, JobStatus.CLOSED}:
        return current_status
    if eligibility_status is EligibilityStatus.INELIGIBLE:
        return JobStatus.ARCHIVED
    if eligibility_status is EligibilityStatus.MANUAL_REVIEW:
        return JobStatus.PENDING_REVIEW
    if eligibility_status is EligibilityStatus.TRACK_ONLY:
        if has_application:
            return JobStatus.APPLIED
        return current_status
    if ranking_score is not None and ranking_score >= recommended_min_score:
        return JobStatus.RECOMMENDED
    return JobStatus.ELIGIBLE


def _decision_snapshot(job: Job) -> dict[str, Any]:
    decision = job.decision
    if decision is None:
        return {
            "job_status": job.status.value,
            "eligibility_status": None,
            "reason_code": None,
            "ranking_score": None,
            "relevance_status": None,
            "relevance_score": None,
            "relevance_rules_version": None,
        }
    return {
        "job_status": job.status.value,
        "eligibility_status": decision.eligibility_status.value,
        "reason_code": decision.reason_code,
        "ranking_score": decision.ranking_score,
        "relevance_status": (
            decision.relevance_status.value if decision.relevance_status is not None else None
        ),
        "relevance_score": decision.relevance_score,
        "relevance_rules_version": decision.relevance_rules_version,
        "relevance_reason_json": decision.relevance_reason_json,
        "ranking_breakdown_json": decision.ranking_breakdown_json,
    }


@dataclass
class _EvaluationCounters:
    total: int
    eligible: int = 0
    manual_review: int = 0
    ineligible: int = 0
    track_only: int = 0
    recommended: int = 0

    def add(self, eligibility_status: EligibilityStatus, job_status: JobStatus) -> None:
        if eligibility_status is EligibilityStatus.ELIGIBLE:
            self.eligible += 1
        elif eligibility_status is EligibilityStatus.MANUAL_REVIEW:
            self.manual_review += 1
        elif eligibility_status is EligibilityStatus.INELIGIBLE:
            self.ineligible += 1
        elif eligibility_status is EligibilityStatus.TRACK_ONLY:
            self.track_only += 1

        if job_status is JobStatus.RECOMMENDED:
            self.recommended += 1

    def to_summary(self) -> EvaluationSummary:
        return EvaluationSummary(
            total=self.total,
            eligible=self.eligible,
            manual_review=self.manual_review,
            ineligible=self.ineligible,
            track_only=self.track_only,
            recommended=self.recommended,
        )
