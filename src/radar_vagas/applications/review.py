from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, desc, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from radar_vagas.applications.guard import ApplicationGuard, ApplicationGuardResult
from radar_vagas.canonicalization.normalize import (
    normalize_company_name,
    normalize_title,
    normalize_url,
)
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationStage,
    ApplicationStatus,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    RelevanceStatus,
    ReviewEventType,
    ReviewState,
    WorkModel,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.eligibility.workflow import evaluate_job_record
from radar_vagas.persistence.models import (
    Application,
    ApplicationEvent,
    Company,
    Decision,
    DiscoveryHit,
    Job,
    JobReviewEvent,
    JobReviewState,
    Posting,
    SearchQuery,
)


@dataclass(frozen=True)
class ReviewQueueRow:
    job: Job
    company: Company
    decision: Decision | None
    review_state: ReviewState
    application: Application | None
    query_count: int
    guard: ApplicationGuardResult


def review_queue(
    session: Session,
    *,
    status: JobStatus | None = None,
    review_state: ReviewState | None = None,
    provider: str | None = None,
    employment_type: EmploymentType | None = None,
    work_model: WorkModel | None = None,
    relevance_status: RelevanceStatus | None = None,
    min_score: int | None = None,
    query_key: str | None = None,
    company: str | None = None,
    limit: int = 50,
    sort: str = "score",
) -> list[ReviewQueueRow]:
    if limit <= 0:
        raise RadarError("--limit deve ser um inteiro positivo.")
    if sort not in {"score", "newest", "first-seen"}:
        raise RadarError("--sort deve ser score, newest ou first-seen.")

    statement = select(Job, Company, Decision).join(Company).outerjoin(Decision)
    if status is None:
        statement = statement.where(
            Job.status.in_(
                [
                    JobStatus.RECOMMENDED,
                    JobStatus.ELIGIBLE,
                    JobStatus.PENDING_REVIEW,
                    JobStatus.SEEN,
                    JobStatus.NEW,
                ]
            )
        )
    else:
        statement = statement.where(Job.status == status)
    statement = statement.where(
        Job.status.not_in([JobStatus.APPLIED, JobStatus.DISMISSED, JobStatus.CLOSED])
    )
    if review_state is not None:
        statement = statement.where(Job.review_state.has(JobReviewState.state == review_state))
    elif status is None:
        statement = statement.where(
            ~Job.review_state.has()
            | Job.review_state.has(
                JobReviewState.state.not_in([ReviewState.DISMISSED, ReviewState.APPLIED])
            )
        )
    if provider is not None:
        statement = statement.where(Job.postings.any(Posting.provider == provider.strip().lower()))
    if employment_type is not None:
        statement = statement.where(Job.employment_type == employment_type)
    if work_model is not None:
        statement = statement.where(Job.work_model == work_model)
    if relevance_status is not None:
        statement = statement.where(Decision.relevance_status == relevance_status)
    if min_score is not None:
        statement = statement.where(Decision.ranking_score >= min_score)
    if query_key is not None:
        statement = statement.where(
            Job.postings.any(
                Posting.discovery_hits.any(
                    DiscoveryHit.search_query.has(SearchQuery.key == query_key)
                )
            )
        )
    if company is not None:
        normalized_company = normalize_company_name(company)
        statement = statement.where(Company.normalized_name.contains(normalized_company))

    first_seen = (
        select(func.min(Posting.first_seen_at)).where(Posting.job_id == Job.id).scalar_subquery()
    )
    status_priority = _status_priority_case()
    if sort == "score":
        statement = statement.order_by(
            status_priority,
            Decision.ranking_score.desc().nullslast(),
            Decision.relevance_score.desc().nullslast(),
            Job.id.asc(),
        )
    elif sort == "newest":
        statement = statement.order_by(desc(Job.published_at), Job.id.desc())
    else:
        statement = statement.order_by(desc(first_seen), Job.id.asc())
    statement = statement.limit(limit)

    guard = ApplicationGuard()
    rows: list[ReviewQueueRow] = []
    for job, company_row, decision in session.execute(statement).all():
        rows.append(
            ReviewQueueRow(
                job=job,
                company=company_row,
                decision=decision,
                review_state=current_review_state(job),
                application=_latest_application(job),
                query_count=_query_count(session, job.id),
                guard=guard.evaluate(job),
            )
        )
    return rows


def mark_seen(session: Session, job_id: int, *, source: str = "manual") -> JobReviewState:
    job = _job_or_raise(session, job_id)
    state = _get_or_create_review_state(session, job)
    if state.state in {ReviewState.SEEN, ReviewState.SHORTLISTED, ReviewState.APPLIED}:
        return state
    previous_status = job.status
    previous_state = state.state
    if job.status in {JobStatus.RECOMMENDED, JobStatus.ELIGIBLE}:
        job.status = JobStatus.SEEN
    state.state = ReviewState.SEEN
    state.updated_at = utc_now()
    _add_review_event(
        session,
        job=job,
        event_type=ReviewEventType.SEEN,
        previous_job_status=previous_status,
        previous_review_state=previous_state,
        new_review_state=state.state,
        source=source,
    )
    return state


def shortlist_job(session: Session, job_id: int, *, source: str = "manual") -> JobReviewState:
    job = _job_or_raise(session, job_id)
    state = _get_or_create_review_state(session, job)
    if state.state is ReviewState.SHORTLISTED:
        return state
    previous_state = state.state
    previous_status = job.status
    state.state = ReviewState.SHORTLISTED
    state.updated_at = utc_now()
    _add_review_event(
        session,
        job=job,
        event_type=ReviewEventType.SHORTLISTED,
        previous_job_status=previous_status,
        previous_review_state=previous_state,
        new_review_state=state.state,
        source=source,
    )
    return state


def dismiss_job(
    session: Session,
    job_id: int,
    *,
    reason_code: str | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> JobReviewState:
    job = _job_or_raise(session, job_id)
    state = _get_or_create_review_state(session, job)
    if job.status is JobStatus.DISMISSED and state.state is ReviewState.DISMISSED:
        return state
    previous_status = job.status
    previous_state = state.state
    job.status = JobStatus.DISMISSED
    job.updated_at = utc_now()
    state.state = ReviewState.DISMISSED
    state.reason_code = reason_code
    state.notes = notes
    state.updated_at = utc_now()
    _add_review_event(
        session,
        job=job,
        event_type=ReviewEventType.DISMISSED,
        previous_job_status=previous_status,
        previous_review_state=previous_state,
        new_review_state=state.state,
        reason_code=reason_code,
        notes=notes,
        source=source,
    )
    return state


def restore_job(
    session: Session,
    settings: Settings,
    job_id: int,
    *,
    source: str = "manual",
) -> Job:
    job = _job_or_raise(session, job_id)
    if job.status is JobStatus.APPLIED:
        raise RadarError("Vaga aplicada nao pode ser restaurada pela fila de revisao.")
    if job.status is JobStatus.CLOSED:
        raise RadarError("Vaga fechada nao e reaberta automaticamente pela fila de revisao.")
    state = _get_or_create_review_state(session, job)
    previous_status = job.status
    previous_state = state.state
    job.status = JobStatus.NEW
    state.state = ReviewState.UNREVIEWED
    state.updated_at = utc_now()
    evaluate_job_record(session, job, settings, job_status_override=JobStatus.NEW)
    _add_review_event(
        session,
        job=job,
        event_type=ReviewEventType.RESTORED,
        previous_job_status=previous_status,
        previous_review_state=previous_state,
        new_review_state=state.state,
        source=source,
    )
    return job


def mark_applied(
    session: Session,
    settings: Settings,
    job_id: int,
    *,
    applied_at: datetime | None = None,
    platform: str | None = None,
    external_reference: str | None = None,
    notes: str | None = None,
    application_url: str | None = None,
    source: str = "manual",
) -> Application:
    job = _job_or_raise(session, job_id)
    state = _get_or_create_review_state(session, job)
    application_key = application_key_for_job(
        job,
        external_reference=external_reference,
        application_url=application_url,
    )
    application = session.scalar(
        select(Application).where(Application.application_key == application_key)
    )
    if application is None:
        application = _latest_application(job)
    created = application is None
    if application is None:
        application = Application(
            job_id=job.id,
            application_key=application_key,
            status=ApplicationStatus.SUBMITTED,
            stage=ApplicationStage.APPLIED,
        )
        session.add(application)
    previous_status = job.status
    previous_state = state.state
    previous_ranking_score = job.decision.ranking_score if job.decision else None
    previous_ranking_breakdown = job.decision.ranking_breakdown_json if job.decision else None

    application.application_key = application.application_key or application_key
    application.applied_at = applied_at or application.applied_at or utc_now()
    application.platform = platform or application.platform or _platform_from_job(job)
    application.external_reference = external_reference or application.external_reference
    application.application_url = (
        application_url or application.application_url or job.application_url
    )
    application.notes = notes or application.notes
    application.status = ApplicationStatus.SUBMITTED
    application.stage = application.stage or ApplicationStage.APPLIED
    application.updated_at = utc_now()
    job.status = JobStatus.APPLIED
    job.updated_at = utc_now()
    state.state = ReviewState.APPLIED
    state.updated_at = utc_now()
    session.flush()

    if created or not _has_application_event(application, ApplicationEventType.SUBMITTED):
        session.add(
            ApplicationEvent(
                application_id=application.id,
                event_type=ApplicationEventType.SUBMITTED,
                occurred_at=application.applied_at or utc_now(),
                source=source,
                notes=notes,
            )
        )
    if (
        created
        or previous_status is not JobStatus.APPLIED
        or previous_state is not ReviewState.APPLIED
    ):
        _add_review_event(
            session,
            job=job,
            event_type=ReviewEventType.APPLIED,
            previous_job_status=previous_status,
            previous_review_state=previous_state,
            new_review_state=state.state,
            notes=notes,
            source=source,
        )

    decision = evaluate_job_record(session, job, settings)
    if (
        decision.eligibility_status is EligibilityStatus.TRACK_ONLY
        and previous_ranking_score is not None
    ):
        decision.ranking_score = previous_ranking_score
        decision.ranking_breakdown_json = previous_ranking_breakdown
    return application


def add_application_event(
    session: Session,
    application_id: int,
    *,
    event_type: ApplicationEventType,
    occurred_at: datetime | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> ApplicationEvent:
    application = session.get(Application, application_id)
    if application is None:
        raise RadarError(f"Candidatura nao encontrada: {application_id}")
    event = ApplicationEvent(
        application_id=application.id,
        event_type=event_type,
        occurred_at=occurred_at or utc_now(),
        source=source,
        notes=notes,
    )
    session.add(event)
    _apply_application_event_status(application, event_type)
    return event


def current_review_state(job: Job) -> ReviewState:
    if job.review_state is not None:
        return job.review_state.state
    if job.status is JobStatus.APPLIED:
        return ReviewState.APPLIED
    if job.status is JobStatus.DISMISSED:
        return ReviewState.DISMISSED
    return ReviewState.UNREVIEWED


def application_key_for_job(
    job: Job,
    *,
    external_reference: str | None = None,
    application_url: str | None = None,
) -> str:
    for posting in sorted(job.postings, key=lambda value: value.id or 0):
        if posting.provider_identity_key:
            return f"provider:{posting.provider_identity_key}"
    normalized_url = normalize_url(application_url or job.application_url)
    if normalized_url:
        return f"url:{normalized_url}"
    if external_reference:
        return f"external:{external_reference.strip()}"
    return f"job:{job.id}"


def _status_priority_case() -> ColumnElement[int]:
    from sqlalchemy import case

    return case(
        (Job.status == JobStatus.RECOMMENDED, 0),
        (Job.status == JobStatus.ELIGIBLE, 1),
        (Job.status == JobStatus.PENDING_REVIEW, 2),
        else_=3,
    )


def _job_or_raise(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise RadarError(f"Vaga nao encontrada: {job_id}")
    return job


def _get_or_create_review_state(session: Session, job: Job) -> JobReviewState:
    if job.review_state is not None:
        return job.review_state
    state = JobReviewState(job=job, state=current_review_state(job))
    session.add(state)
    session.flush()
    return state


def _add_review_event(
    session: Session,
    *,
    job: Job,
    event_type: ReviewEventType,
    previous_job_status: JobStatus | None,
    previous_review_state: ReviewState | None,
    new_review_state: ReviewState | None,
    source: str,
    reason_code: str | None = None,
    notes: str | None = None,
) -> JobReviewEvent:
    event = JobReviewEvent(
        job_id=job.id,
        event_type=event_type,
        previous_job_status=previous_job_status,
        new_job_status=job.status,
        previous_review_state=previous_review_state,
        new_review_state=new_review_state,
        reason_code=reason_code,
        notes=notes,
        source=source,
        occurred_at=utc_now(),
        created_at=utc_now(),
    )
    session.add(event)
    return event


def _latest_application(job: Job) -> Application | None:
    return max(job.applications, key=lambda value: value.id or 0) if job.applications else None


def _has_application_event(application: Application, event_type: ApplicationEventType) -> bool:
    return any(event.event_type is event_type for event in application.events)


def _platform_from_job(job: Job) -> str | None:
    for posting in sorted(job.postings, key=lambda value: value.id or 0):
        if posting.provider:
            return posting.provider
    return None


def _query_count(session: Session, job_id: int) -> int:
    count = session.scalar(
        select(func.count(func.distinct(DiscoveryHit.search_query_id))).where(
            DiscoveryHit.job_id == job_id
        )
    )
    return int(count or 0)


def _apply_application_event_status(
    application: Application,
    event_type: ApplicationEventType,
) -> None:
    if event_type is ApplicationEventType.SUBMITTED:
        application.status = ApplicationStatus.SUBMITTED
        application.stage = ApplicationStage.APPLIED
    elif event_type in {
        ApplicationEventType.CONFIRMATION_RECEIVED,
        ApplicationEventType.PROCESS_UPDATE,
    }:
        application.stage = ApplicationStage.AWAITING_UPDATE
    elif event_type in {
        ApplicationEventType.ASSESSMENT_INVITED,
    }:
        application.status = ApplicationStatus.TEST
        application.stage = ApplicationStage.ASSESSMENT_RECEIVED
    elif event_type in {
        ApplicationEventType.ASSESSMENT_COMPLETED,
    }:
        application.status = ApplicationStatus.TEST
        application.stage = ApplicationStage.ASSESSMENT_COMPLETED
    elif event_type is ApplicationEventType.CASE_RECEIVED:
        application.status = ApplicationStatus.TEST
        application.stage = ApplicationStage.CASE_RECEIVED
    elif event_type is ApplicationEventType.CASE_SUBMITTED:
        application.status = ApplicationStatus.TEST
        application.stage = ApplicationStage.CASE_SUBMITTED
    elif event_type in {
        ApplicationEventType.INTERVIEW_INVITED,
    }:
        application.status = ApplicationStatus.INTERVIEW
        application.stage = ApplicationStage.INTERVIEW_SCHEDULED
    elif event_type in {
        ApplicationEventType.INTERVIEW_COMPLETED,
    }:
        application.status = ApplicationStatus.INTERVIEW
        application.stage = ApplicationStage.INTERVIEW_COMPLETED
    elif event_type is ApplicationEventType.REJECTED:
        application.status = ApplicationStatus.REJECTED
        application.stage = ApplicationStage.REJECTED
    elif event_type is ApplicationEventType.OFFER_RECEIVED:
        application.status = ApplicationStatus.OFFER
        application.stage = ApplicationStage.OFFER_RECEIVED
    elif event_type is ApplicationEventType.WITHDRAWN:
        application.status = ApplicationStatus.WITHDRAWN
        application.stage = ApplicationStage.WITHDRAWN
    application.updated_at = utc_now()


def exact_job_by_provider_identity(
    session: Session,
    provider_identity_key: str | None,
) -> Job | None:
    if not provider_identity_key:
        return None
    return session.scalar(
        select(Job).where(Job.postings.any(Posting.provider_identity_key == provider_identity_key))
    )


def exact_job_by_url(session: Session, url: str | None) -> Job | None:
    normalized = normalize_url(url)
    if not normalized:
        return None
    return session.scalar(
        select(Job).where(
            (Job.application_url == normalized)
            | Job.postings.any(Posting.normalized_url == normalized)
            | Job.postings.any(Posting.original_url == url)
        )
    )


def probable_jobs_by_company_title(
    session: Session,
    *,
    company: str | None,
    title: str | None,
) -> list[Job]:
    normalized_company = normalize_company_name(company)
    normalized_title = normalize_title(title)
    if not normalized_company or not normalized_title:
        return []
    statement: Select[tuple[Job]] = (
        select(Job)
        .join(Company)
        .where(
            Company.normalized_name == normalized_company,
            Job.normalized_title == normalized_title,
        )
    )
    return list(session.scalars(statement).all())
