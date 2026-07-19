from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from radar_vagas.domain.enums import JobStatus, ReviewState
from radar_vagas.persistence.models import Job, JobReviewState

ABSENCE_UNREVIEWED_EXCLUDED_STATUSES = (
    JobStatus.APPLIED,
    JobStatus.DISMISSED,
    JobStatus.CLOSED,
    JobStatus.EXPIRED,
    JobStatus.ARCHIVED,
)


def effective_review_state_condition(state: ReviewState) -> ColumnElement[bool]:
    explicit = Job.review_state.has(JobReviewState.state == state)
    if state is not ReviewState.UNREVIEWED:
        return explicit
    missing_state_is_unreviewed = and_(
        ~Job.review_state.has(),
        Job.status.not_in(ABSENCE_UNREVIEWED_EXCLUDED_STATUSES),
    )
    return or_(explicit, missing_state_is_unreviewed)
