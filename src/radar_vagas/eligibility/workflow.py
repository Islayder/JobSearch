import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar_vagas.config.loaders import (
    blocked_company_reasons,
    load_eligibility_rules,
    load_ranking_weights,
)
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    EligibilityStatus,
    JobStatus,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.eligibility.service import EligibilityInput, evaluate_eligibility
from radar_vagas.persistence.models import Application, Decision, Job
from radar_vagas.ranking.service import RankingInput, rank_job


@dataclass(frozen=True)
class EvaluationSummary:
    total: int
    eligible: int
    manual_review: int
    ineligible: int
    track_only: int
    recommended: int


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


def evaluate_job_record(session: Session, job: Job, settings: Settings) -> Decision:
    rules = load_eligibility_rules(settings.config_dir)
    ranking_weights = load_ranking_weights(settings.config_dir)
    blocked_reasons = blocked_company_reasons(settings.config_dir)
    has_application = _has_any_application(session, job.id)

    eligibility = evaluate_eligibility(
        EligibilityInput(
            company_name=job.company.canonical_name,
            company_aliases=tuple(alias.alias for alias in job.company.aliases),
            company_is_blocked=job.company.is_blocked,
            job_status=job.status,
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
            status=job.status,
        ),
        eligibility.status,
        ranking_weights,
    )

    job.status = _next_job_status(
        current_status=job.status,
        eligibility_status=eligibility.status,
        ranking_score=ranking.total if ranking is not None else None,
        recommended_min_score=ranking_weights.recommended_min_score,
        has_application=has_application,
    )
    job.updated_at = utc_now()

    decision = session.scalar(select(Decision).where(Decision.job_id == job.id))
    if decision is None:
        decision = Decision(
            job_id=job.id,
            eligibility_status=eligibility.status,
            reason_code=eligibility.reason_code,
            reason_text=eligibility.reason_text,
            rules_version=eligibility.rules_version,
        )
        session.add(decision)

    decision.eligibility_status = eligibility.status
    decision.reason_code = eligibility.reason_code
    decision.reason_text = eligibility.reason_text
    decision.ranking_score = ranking.total if ranking is not None else None
    decision.ranking_breakdown_json = (
        json.dumps(ranking.breakdown, ensure_ascii=False, sort_keys=True)
        if ranking is not None
        else None
    )
    decision.evaluated_at = utc_now()
    decision.rules_version = eligibility.rules_version
    session.flush()
    return decision


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
