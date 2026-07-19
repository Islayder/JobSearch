from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationStage,
    ApplicationStatus,
    JobStatus,
    ReviewEventType,
    ReviewState,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.models import (
    Application,
    ApplicationEvent,
    Job,
    JobReviewState,
)

ACTIVE_JOB_STATUSES = {
    JobStatus.NEW,
    JobStatus.PENDING_REVIEW,
    JobStatus.ELIGIBLE,
    JobStatus.RECOMMENDED,
    JobStatus.SEEN,
}
BLOCKED_REVIEW_JOB_STATUSES = {JobStatus.CLOSED, JobStatus.EXPIRED}
VALID_REVIEW_TRANSITIONS = {
    ReviewState.UNREVIEWED: {
        ReviewState.SEEN,
        ReviewState.SHORTLISTED,
        ReviewState.DISMISSED,
        ReviewState.APPLIED,
    },
    ReviewState.SEEN: {
        ReviewState.SHORTLISTED,
        ReviewState.DISMISSED,
        ReviewState.APPLIED,
    },
    ReviewState.SHORTLISTED: {
        ReviewState.SEEN,
        ReviewState.DISMISSED,
        ReviewState.APPLIED,
    },
}
REVIEW_EVENT_BY_TARGET = {
    ReviewState.SEEN: ReviewEventType.SEEN,
    ReviewState.SHORTLISTED: ReviewEventType.SHORTLISTED,
    ReviewState.DISMISSED: ReviewEventType.DISMISSED,
    ReviewState.APPLIED: ReviewEventType.APPLIED,
}

INFORMATIVE_APPLICATION_EVENTS = {
    ApplicationEventType.CONFIRMATION_RECEIVED,
    ApplicationEventType.PROCESS_UPDATE,
    ApplicationEventType.UNKNOWN,
}
TERMINAL_APPLICATION_STAGES = {
    ApplicationStage.OFFER_RECEIVED,
    ApplicationStage.REJECTED,
    ApplicationStage.WITHDRAWN,
}
APPLICATION_EVENT_STAGE = {
    ApplicationEventType.SUBMITTED: ApplicationStage.APPLIED,
    ApplicationEventType.CONFIRMATION_RECEIVED: ApplicationStage.AWAITING_UPDATE,
    ApplicationEventType.PROCESS_UPDATE: ApplicationStage.AWAITING_UPDATE,
    ApplicationEventType.ASSESSMENT_INVITED: ApplicationStage.ASSESSMENT_RECEIVED,
    ApplicationEventType.ASSESSMENT_COMPLETED: ApplicationStage.ASSESSMENT_COMPLETED,
    ApplicationEventType.CASE_RECEIVED: ApplicationStage.CASE_RECEIVED,
    ApplicationEventType.CASE_SUBMITTED: ApplicationStage.CASE_SUBMITTED,
    ApplicationEventType.INTERVIEW_INVITED: ApplicationStage.INTERVIEW_SCHEDULED,
    ApplicationEventType.INTERVIEW_COMPLETED: ApplicationStage.INTERVIEW_COMPLETED,
    ApplicationEventType.REJECTED: ApplicationStage.REJECTED,
    ApplicationEventType.OFFER_RECEIVED: ApplicationStage.OFFER_RECEIVED,
    ApplicationEventType.WITHDRAWN: ApplicationStage.WITHDRAWN,
}
STAGE_RANK = {
    ApplicationStage.APPLIED: 10,
    ApplicationStage.AWAITING_UPDATE: 20,
    ApplicationStage.ASSESSMENT_RECEIVED: 30,
    ApplicationStage.CASE_RECEIVED: 35,
    ApplicationStage.ASSESSMENT_COMPLETED: 40,
    ApplicationStage.CASE_SUBMITTED: 45,
    ApplicationStage.INTERVIEW_SCHEDULED: 50,
    ApplicationStage.INTERVIEW_COMPLETED: 60,
    ApplicationStage.OFFER_RECEIVED: 70,
    ApplicationStage.REJECTED: 80,
    ApplicationStage.WITHDRAWN: 80,
}
STATUS_BY_STAGE = {
    ApplicationStage.APPLIED: ApplicationStatus.SUBMITTED,
    ApplicationStage.AWAITING_UPDATE: ApplicationStatus.SUBMITTED,
    ApplicationStage.ASSESSMENT_RECEIVED: ApplicationStatus.TEST,
    ApplicationStage.ASSESSMENT_COMPLETED: ApplicationStatus.TEST,
    ApplicationStage.CASE_RECEIVED: ApplicationStatus.TEST,
    ApplicationStage.CASE_SUBMITTED: ApplicationStatus.TEST,
    ApplicationStage.INTERVIEW_SCHEDULED: ApplicationStatus.INTERVIEW,
    ApplicationStage.INTERVIEW_COMPLETED: ApplicationStatus.INTERVIEW,
    ApplicationStage.OFFER_RECEIVED: ApplicationStatus.OFFER,
    ApplicationStage.REJECTED: ApplicationStatus.REJECTED,
    ApplicationStage.WITHDRAWN: ApplicationStatus.WITHDRAWN,
}


@dataclass(frozen=True)
class ReviewTransitionResult:
    previous_job_status: JobStatus
    previous_review_state: ReviewState
    new_review_state: ReviewState
    event_type: ReviewEventType
    changed: bool


@dataclass(frozen=True)
class ApplicationTimelineResult:
    status: ApplicationStatus
    stage: ApplicationStage | None


def apply_review_transition(
    job: Job,
    state: JobReviewState,
    target: ReviewState,
) -> ReviewTransitionResult:
    previous_job_status = job.status
    previous_review_state = state.state
    event_type = REVIEW_EVENT_BY_TARGET[target]
    _ensure_review_state_consistency(job, previous_review_state)

    target_job_status = _job_status_for_review_target(job.status, target)
    if previous_review_state is target and job.status is target_job_status:
        return ReviewTransitionResult(
            previous_job_status=previous_job_status,
            previous_review_state=previous_review_state,
            new_review_state=target,
            event_type=event_type,
            changed=False,
        )

    _ensure_review_transition_allowed(job, previous_review_state, target)
    job.status = target_job_status
    state.state = target
    return ReviewTransitionResult(
        previous_job_status=previous_job_status,
        previous_review_state=previous_review_state,
        new_review_state=target,
        event_type=event_type,
        changed=True,
    )


def apply_restore_transition(job: Job, state: JobReviewState) -> ReviewTransitionResult:
    previous_job_status = job.status
    previous_review_state = state.state
    _ensure_review_state_consistency(job, previous_review_state)
    if job.applications:
        raise RadarError("Vaga com candidatura registrada nao pode voltar para revisao.")
    if job.status is JobStatus.APPLIED or previous_review_state is ReviewState.APPLIED:
        raise RadarError("Vaga aplicada nao pode ser restaurada pela fila de revisao.")
    if job.status in BLOCKED_REVIEW_JOB_STATUSES:
        raise RadarError("Vaga fechada ou expirada nao pode ser restaurada pela fila de revisao.")
    if job.status is not JobStatus.DISMISSED and previous_review_state is not ReviewState.DISMISSED:
        raise RadarError("Somente vagas descartadas podem ser restauradas.")

    job.status = JobStatus.NEW
    state.state = ReviewState.UNREVIEWED
    state.reason_code = None
    state.notes = None
    return ReviewTransitionResult(
        previous_job_status=previous_job_status,
        previous_review_state=previous_review_state,
        new_review_state=ReviewState.UNREVIEWED,
        event_type=ReviewEventType.RESTORED,
        changed=True,
    )


def ensure_can_register_application(job: Job, state: JobReviewState) -> None:
    _ensure_review_state_consistency(job, state.state)
    if job.status in BLOCKED_REVIEW_JOB_STATUSES:
        raise RadarError("Vaga fechada ou expirada nao pode receber candidatura normal.")
    if state.state is ReviewState.DISMISSED or job.status is JobStatus.DISMISSED:
        raise RadarError("Restaure a vaga descartada antes de registrar candidatura.")


def rebuild_application_state(application: Application) -> ApplicationTimelineResult:
    result = reduce_application_timeline(list(application.events))
    application.status = result.status
    application.stage = result.stage
    return result


def reduce_application_timeline(
    events: list[ApplicationEvent],
) -> ApplicationTimelineResult:
    stage: ApplicationStage | None = None
    for event in sorted(events, key=_application_event_sort_key):
        next_stage = APPLICATION_EVENT_STAGE.get(event.event_type)
        if next_stage is None:
            continue
        if stage is None:
            stage = next_stage
            continue
        if _should_apply_stage_transition(stage, next_stage, event.event_type):
            stage = next_stage
    return ApplicationTimelineResult(
        status=ApplicationStatus.PREPARING if stage is None else STATUS_BY_STAGE[stage],
        stage=stage,
    )


def _ensure_review_transition_allowed(
    job: Job,
    current: ReviewState,
    target: ReviewState,
) -> None:
    if job.applications and target is not ReviewState.APPLIED:
        raise RadarError("Candidatura existente prevalece sobre acoes de revisao.")
    if job.status in BLOCKED_REVIEW_JOB_STATUSES:
        raise RadarError("Vaga fechada ou expirada nao pode ser alterada pela revisao.")
    if current is ReviewState.APPLIED:
        raise RadarError("Vaga aplicada nao volta para vista, shortlist ou descarte.")
    if current is ReviewState.DISMISSED:
        raise RadarError("Vaga descartada so pode voltar por restore-job.")
    if target not in VALID_REVIEW_TRANSITIONS.get(current, set()):
        raise RadarError(f"Transicao de revisao invalida: {current.value} -> {target.value}.")


def _ensure_review_state_consistency(job: Job, state: ReviewState) -> None:
    if job.status is JobStatus.APPLIED and state is not ReviewState.APPLIED:
        raise RadarError("Estado inconsistente: JobStatus APPLIED exige ReviewState APPLIED.")
    if job.status is JobStatus.DISMISSED and state is not ReviewState.DISMISSED:
        raise RadarError("Estado inconsistente: JobStatus DISMISSED exige ReviewState DISMISSED.")
    if state is ReviewState.APPLIED and job.status is not JobStatus.APPLIED:
        raise RadarError("Estado inconsistente: ReviewState APPLIED exige JobStatus APPLIED.")
    if state is ReviewState.DISMISSED and job.status is not JobStatus.DISMISSED:
        raise RadarError("Estado inconsistente: ReviewState DISMISSED exige JobStatus DISMISSED.")
    if state in {ReviewState.SEEN, ReviewState.SHORTLISTED} and job.status in {
        JobStatus.CLOSED,
        JobStatus.EXPIRED,
        JobStatus.APPLIED,
        JobStatus.DISMISSED,
    }:
        raise RadarError("Estado de revisao contradiz o estado atual da vaga.")


def _job_status_for_review_target(current_status: JobStatus, target: ReviewState) -> JobStatus:
    if target is ReviewState.SEEN:
        return JobStatus.SEEN
    if target is ReviewState.SHORTLISTED:
        return JobStatus.SEEN if current_status in ACTIVE_JOB_STATUSES else current_status
    if target is ReviewState.DISMISSED:
        return JobStatus.DISMISSED
    if target is ReviewState.APPLIED:
        return JobStatus.APPLIED
    return current_status


def _application_event_sort_key(
    event: ApplicationEvent,
) -> tuple[float, float, int, int]:
    return (
        _datetime_sort_value(event.occurred_at),
        _datetime_sort_value(event.created_at),
        event.id or 0,
        _event_priority(event.event_type),
    )


def _datetime_sort_value(value: datetime) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _event_priority(event_type: ApplicationEventType) -> int:
    if event_type in {
        ApplicationEventType.REJECTED,
        ApplicationEventType.WITHDRAWN,
        ApplicationEventType.OFFER_RECEIVED,
    }:
        return 100
    stage = APPLICATION_EVENT_STAGE.get(event_type)
    return 0 if stage is None else STAGE_RANK[stage]


def _should_apply_stage_transition(
    current: ApplicationStage,
    candidate: ApplicationStage,
    event_type: ApplicationEventType,
) -> bool:
    if event_type in INFORMATIVE_APPLICATION_EVENTS:
        return STAGE_RANK[candidate] > STAGE_RANK[current]
    if current in TERMINAL_APPLICATION_STAGES:
        return True
    return STAGE_RANK[candidate] >= STAGE_RANK[current]
