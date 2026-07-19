from dataclasses import dataclass
from datetime import UTC, datetime

from radar_vagas.canonicalization.normalize import normalize_url
from radar_vagas.domain.enums import DuplicateKind, WorkModel


@dataclass(frozen=True)
class DuplicateCandidate:
    normalized_company: str
    normalized_title: str
    city: str | None
    work_model: WorkModel
    published_at: datetime | None
    application_url: str | None
    content_hash: str | None = None


def classify_canonical_duplicate(
    new_candidate: DuplicateCandidate,
    existing_candidate: DuplicateCandidate,
    *,
    probable_window_days: int = 14,
) -> DuplicateKind:
    if _is_exact(new_candidate, existing_candidate):
        return DuplicateKind.EXACT
    if _is_probable(new_candidate, existing_candidate, probable_window_days):
        return DuplicateKind.PROBABLE
    return DuplicateKind.DISTINCT


def _is_exact(first: DuplicateCandidate, second: DuplicateCandidate) -> bool:
    first_url = normalize_url(first.application_url)
    second_url = normalize_url(second.application_url)
    if first_url and second_url and first_url == second_url:
        return _same_identity(first, second)
    return bool(
        first.content_hash and second.content_hash and first.content_hash == second.content_hash
    )


def _is_probable(
    first: DuplicateCandidate, second: DuplicateCandidate, probable_window_days: int
) -> bool:
    return (
        _same_identity(first, second)
        and _same_city(first, second)
        and _dates_close(first.published_at, second.published_at, probable_window_days)
    )


def _same_identity(first: DuplicateCandidate, second: DuplicateCandidate) -> bool:
    return (
        first.normalized_company == second.normalized_company
        and first.normalized_title == second.normalized_title
        and first.work_model is second.work_model
    )


def _same_city(first: DuplicateCandidate, second: DuplicateCandidate) -> bool:
    return (first.city or "") == (second.city or "")


def _dates_close(first: datetime | None, second: datetime | None, days: int) -> bool:
    if first is None or second is None:
        return False
    first_aware = _ensure_aware(first)
    second_aware = _ensure_aware(second)
    return abs((first_aware - second_aware).days) <= days


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
