from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.domain.enums import (
    ApplicationStage,
    ApplicationStatus,
    CareerEventConfirmationStatus,
    JobStatus,
    ReviewState,
)
from radar_vagas.persistence.models import Application, CareerEvent, Job
from radar_vagas.web.queries.profiles import active_profile_version
from radar_vagas.web.queries.review import effective_review_state_condition
from radar_vagas.web.queries.sources import source_health_summary


def dashboard_context(session: Session, *, page_size: int, timezone: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    local_hour = now.astimezone(ZoneInfo(timezone)).hour
    review_statuses = [
        JobStatus.NEW,
        JobStatus.PENDING_REVIEW,
        JobStatus.ELIGIBLE,
        JobStatus.RECOMMENDED,
        JobStatus.SEEN,
    ]
    review_today = list(
        session.scalars(
            select(Job)
            .options(
                selectinload(Job.company),
                selectinload(Job.decision),
                selectinload(Job.review_state),
                selectinload(Job.postings),
                selectinload(Job.profile_comparisons),
            )
            .where(Job.status.in_(review_statuses))
            .where(effective_review_state_condition(ReviewState.UNREVIEWED))
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .limit(6)
        ).all()
    )
    latest_jobs = list(
        session.scalars(
            select(Job)
            .options(
                selectinload(Job.company),
                selectinload(Job.decision),
                selectinload(Job.review_state),
                selectinload(Job.postings),
                selectinload(Job.profile_comparisons),
            )
            .where(Job.status.in_(review_statuses))
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .limit(6)
        ).all()
    )
    upcoming = upcoming_events(session, now=now, days=30, limit=8)
    awaiting_update = applications_by_stage(session, [ApplicationStage.AWAITING_UPDATE], limit=6)
    tests_cases = applications_by_stage(
        session,
        [
            ApplicationStage.ASSESSMENT_RECEIVED,
            ApplicationStage.ASSESSMENT_COMPLETED,
            ApplicationStage.CASE_RECEIVED,
            ApplicationStage.CASE_SUBMITTED,
        ],
        limit=6,
    )
    interviews = applications_by_stage(
        session,
        [ApplicationStage.INTERVIEW_SCHEDULED, ApplicationStage.INTERVIEW_COMPLETED],
        limit=6,
    )
    source_health = source_health_summary(session)
    return {
        "active_profile": active_profile_version(session),
        "greeting": _greeting(local_hour),
        "daily_summary": (
            f"Voce tem {_review_count(session, ReviewState.UNREVIEWED, review_statuses)} "
            f"vagas para revisar e {len(upcoming)} compromissos proximos."
        ),
        "metrics": {
            "new_jobs": _job_count(session, [JobStatus.NEW]),
            "unreviewed": _review_count(session, ReviewState.UNREVIEWED, review_statuses),
            "recommended": _job_count(session, [JobStatus.RECOMMENDED]),
            "shortlisted": _review_count(session, ReviewState.SHORTLISTED, review_statuses),
            "active_applications": _application_count(
                session,
                [
                    ApplicationStatus.SUBMITTED,
                    ApplicationStatus.TEST,
                    ApplicationStatus.INTERVIEW,
                    ApplicationStatus.FINAL_STAGE,
                    ApplicationStatus.OFFER,
                ],
            ),
            "awaiting_update": _stage_count(session, [ApplicationStage.AWAITING_UPDATE]),
            "tests_cases": _stage_count(
                session,
                [
                    ApplicationStage.ASSESSMENT_RECEIVED,
                    ApplicationStage.ASSESSMENT_COMPLETED,
                    ApplicationStage.CASE_RECEIVED,
                    ApplicationStage.CASE_SUBMITTED,
                ],
            ),
            "interviews": _stage_count(
                session,
                [ApplicationStage.INTERVIEW_SCHEDULED, ApplicationStage.INTERVIEW_COMPLETED],
            ),
            "offers": _application_count(session, [ApplicationStatus.OFFER]),
            "upcoming_commitments": len(upcoming),
            "sources_with_problem": source_health["problem_count"],
            "page_size": page_size,
        },
        "review_today": review_today,
        "latest_jobs": latest_jobs,
        "upcoming_events": upcoming,
        "awaiting_update": awaiting_update,
        "tests_cases": tests_cases,
        "interviews": interviews,
        "source_health": source_health,
    }


def _greeting(hour: int) -> str:
    if hour < 12:
        return "Bom dia"
    if hour < 18:
        return "Boa tarde"
    return "Boa noite"


def upcoming_events(
    session: Session,
    *,
    now: datetime,
    days: int,
    limit: int,
) -> list[CareerEvent]:
    return list(
        session.scalars(
            select(CareerEvent)
            .options(
                selectinload(CareerEvent.job).selectinload(Job.company),
                selectinload(CareerEvent.application),
            )
            .where(CareerEvent.starts_at.is_not(None))
            .where(CareerEvent.starts_at >= now)
            .where(CareerEvent.starts_at <= now + timedelta(days=days))
            .where(
                CareerEvent.confirmation_status.not_in(
                    [
                        CareerEventConfirmationStatus.DISMISSED,
                        CareerEventConfirmationStatus.COMPLETED,
                        CareerEventConfirmationStatus.CANCELLED,
                    ]
                )
            )
            .order_by(CareerEvent.starts_at.asc(), CareerEvent.id.asc())
            .limit(limit)
        ).all()
    )


def applications_by_stage(
    session: Session,
    stages: list[ApplicationStage],
    *,
    limit: int,
) -> list[Application]:
    return list(
        session.scalars(
            select(Application)
            .options(selectinload(Application.job).selectinload(Job.company))
            .where(Application.stage.in_(stages))
            .order_by(Application.updated_at.desc(), Application.id.desc())
            .limit(limit)
        ).all()
    )


def _job_count(session: Session, statuses: list[JobStatus]) -> int:
    return int(session.scalar(select(func.count(Job.id)).where(Job.status.in_(statuses))) or 0)


def _review_count(
    session: Session,
    state: ReviewState,
    allowed_statuses: list[JobStatus],
) -> int:
    return int(
        session.scalar(
            select(func.count(Job.id))
            .where(Job.status.in_(allowed_statuses))
            .where(effective_review_state_condition(state))
        )
        or 0
    )


def _application_count(session: Session, statuses: list[ApplicationStatus]) -> int:
    return int(
        session.scalar(select(func.count(Application.id)).where(Application.status.in_(statuses)))
        or 0
    )


def _stage_count(session: Session, stages: list[ApplicationStage]) -> int:
    return int(
        session.scalar(select(func.count(Application.id)).where(Application.stage.in_(stages))) or 0
    )
