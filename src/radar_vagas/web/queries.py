from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy import Select, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.applications.review import current_review_state
from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_text
from radar_vagas.domain.enums import (
    ApplicationStatus,
    CareerEventConfirmationStatus,
    JobStatus,
    ReviewState,
    SourceRunStatus,
)
from radar_vagas.persistence.models import (
    Application,
    CareerEvent,
    Company,
    CompanyBoard,
    Decision,
    Job,
    JobProfileComparison,
    Posting,
    ProfessionalProfileVersion,
    SearchQuery,
    Source,
    SourceRun,
)


@dataclass(frozen=True)
class Page:
    items: list[Job]
    page: int
    page_size: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, ceil(self.total / self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def active_profile_version(session: Session) -> ProfessionalProfileVersion | None:
    return session.scalar(
        select(ProfessionalProfileVersion)
        .options(selectinload(ProfessionalProfileVersion.profile))
        .where(ProfessionalProfileVersion.is_active.is_(True))
        .order_by(
            ProfessionalProfileVersion.created_at.desc(),
            ProfessionalProfileVersion.id.desc(),
        )
    )


def dashboard_context(session: Session, *, page_size: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    active_statuses = [
        JobStatus.NEW,
        JobStatus.PENDING_REVIEW,
        JobStatus.ELIGIBLE,
        JobStatus.RECOMMENDED,
        JobStatus.SEEN,
    ]
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
            .where(Job.status.in_(active_statuses))
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .limit(6)
        ).all()
    )
    upcoming = list(
        session.scalars(
            select(CareerEvent)
            .options(
                selectinload(CareerEvent.job).selectinload(Job.company),
                selectinload(CareerEvent.application),
            )
            .where(CareerEvent.starts_at.is_not(None))
            .where(CareerEvent.starts_at >= now)
            .where(CareerEvent.starts_at <= now + timedelta(days=30))
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
            .limit(6)
        ).all()
    )
    return {
        "active_profile": active_profile_version(session),
        "metrics": {
            "to_review": _job_count(session, active_statuses),
            "recommended": _job_count(session, [JobStatus.RECOMMENDED]),
            "commitments": len(upcoming),
            "waiting_applications": _application_count(
                session,
                [
                    ApplicationStatus.SUBMITTED,
                    ApplicationStatus.TEST,
                    ApplicationStatus.INTERVIEW,
                ],
            ),
            "page_size": page_size,
        },
        "latest_jobs": latest_jobs,
        "upcoming_events": upcoming,
        "recent_runs": recent_source_runs(session, limit=5),
    }


def jobs_page(
    session: Session,
    *,
    q: str | None,
    status: str | None,
    review: str | None,
    page: int,
    page_size: int,
    sort: str,
) -> Page:
    page = max(1, page)
    page_size = min(max(5, page_size), 100)
    base = select(Job.id).join(Company)
    base = _apply_job_filters(base, q=q, status=status, review=review)
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)

    statement = select(Job).where(Job.id.in_(base))
    statement = statement.options(
        selectinload(Job.company),
        selectinload(Job.decision),
        selectinload(Job.review_state),
        selectinload(Job.postings),
        selectinload(Job.applications),
        selectinload(Job.profile_comparisons),
    )
    first_seen = select(func.min(Posting.first_seen_at)).where(Posting.job_id == Job.id)
    if sort == "newest":
        statement = statement.order_by(desc(Job.published_at), Job.id.desc())
    elif sort == "first-seen":
        statement = statement.order_by(desc(first_seen.scalar_subquery()), Job.id.desc())
    else:
        statement = statement.outerjoin(Decision).order_by(
            Decision.ranking_score.desc().nullslast(),
            Decision.relevance_score.desc().nullslast(),
            Job.id.asc(),
        )
    items = list(
        session.scalars(statement.offset((page - 1) * page_size).limit(page_size)).unique().all()
    )
    return Page(items=items, page=page, page_size=page_size, total=total)


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
        )
        .where(Job.id == job_id)
    )


def applications_list(
    session: Session,
    *,
    status: str | None = None,
) -> list[Application]:
    statement = (
        select(Application)
        .options(
            selectinload(Application.job).selectinload(Job.company),
            selectinload(Application.events),
            selectinload(Application.career_events),
        )
        .order_by(Application.updated_at.desc(), Application.id.desc())
    )
    if status:
        statement = statement.where(Application.status == status.upper())
    return list(session.scalars(statement).unique().all())


def application_detail(session: Session, application_id: int) -> Application | None:
    return session.scalar(
        select(Application)
        .options(
            selectinload(Application.job).selectinload(Job.company),
            selectinload(Application.events),
            selectinload(Application.career_events),
        )
        .where(Application.id == application_id)
    )


def agenda_events(
    session: Session,
    *,
    status: str | None = None,
    event_type: str | None = None,
) -> list[CareerEvent]:
    statement = (
        select(CareerEvent)
        .options(
            selectinload(CareerEvent.job).selectinload(Job.company),
            selectinload(CareerEvent.application),
        )
        .order_by(CareerEvent.starts_at.asc().nullslast(), CareerEvent.id.asc())
    )
    if status:
        statement = statement.where(CareerEvent.confirmation_status == status.upper())
    if event_type:
        statement = statement.where(CareerEvent.event_type == event_type.upper())
    return list(session.scalars(statement).unique().all())


def profile_versions(session: Session) -> list[ProfessionalProfileVersion]:
    return list(
        session.scalars(
            select(ProfessionalProfileVersion)
            .options(
                selectinload(ProfessionalProfileVersion.profile),
                selectinload(ProfessionalProfileVersion.skills),
                selectinload(ProfessionalProfileVersion.experiences),
                selectinload(ProfessionalProfileVersion.projects),
                selectinload(ProfessionalProfileVersion.education),
                selectinload(ProfessionalProfileVersion.languages),
            )
            .order_by(
                ProfessionalProfileVersion.is_active.desc(),
                ProfessionalProfileVersion.created_at.desc(),
                ProfessionalProfileVersion.id.desc(),
            )
        ).all()
    )


def sources_context(session: Session) -> dict[str, Any]:
    return {
        "sources": list(
            session.scalars(
                select(Source)
                .options(selectinload(Source.runs))
                .order_by(Source.name.asc(), Source.id.asc())
            ).all()
        ),
        "boards": list(
            session.scalars(
                select(CompanyBoard)
                .options(selectinload(CompanyBoard.source), selectinload(CompanyBoard.last_run))
                .order_by(CompanyBoard.key.asc())
            ).all()
        ),
        "queries": list(
            session.scalars(
                select(SearchQuery)
                .options(selectinload(SearchQuery.last_run))
                .order_by(SearchQuery.priority.asc(), SearchQuery.key.asc())
            ).all()
        ),
        "recent_runs": recent_source_runs(session, limit=20),
        "failed_runs": int(
            session.scalar(
                select(func.count(SourceRun.id)).where(SourceRun.status == SourceRunStatus.FAILED)
            )
            or 0
        ),
    }


def recent_source_runs(session: Session, *, limit: int) -> list[SourceRun]:
    return list(
        session.scalars(
            select(SourceRun)
            .options(selectinload(SourceRun.source))
            .order_by(SourceRun.started_at.desc(), SourceRun.id.desc())
            .limit(limit)
        ).all()
    )


def latest_comparison(job: Job) -> JobProfileComparison | None:
    if not job.profile_comparisons:
        return None
    return max(job.profile_comparisons, key=lambda comparison: comparison.created_at)


def review_state_for(job: Job) -> ReviewState:
    return current_review_state(job)


def _job_count(session: Session, statuses: list[JobStatus]) -> int:
    return int(session.scalar(select(func.count(Job.id)).where(Job.status.in_(statuses))) or 0)


def _application_count(session: Session, statuses: list[ApplicationStatus]) -> int:
    return int(
        session.scalar(select(func.count(Application.id)).where(Application.status.in_(statuses)))
        or 0
    )


def _apply_job_filters(
    statement: Select[tuple[int]],
    *,
    q: str | None,
    status: str | None,
    review: str | None,
) -> Select[tuple[int]]:
    if q:
        normalized_text = normalize_text(q)
        normalized_company = normalize_company_name(q)
        statement = statement.where(
            or_(
                Job.normalized_title.contains(normalized_text),
                Company.normalized_name.contains(normalized_company),
            )
        )
    if status:
        statement = statement.where(Job.status == status.upper())
    if review:
        statement = statement.where(Job.review_state.has(state=review.upper()))
    return statement
