from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from radar_vagas.canonicalization.normalize import is_belo_horizonte, normalize_text
from radar_vagas.config.schemas import RankingWeightsConfig
from radar_vagas.domain.enums import EligibilityStatus, EmploymentType, JobStatus, WorkModel


@dataclass(frozen=True)
class RankingInput:
    employment_type: EmploymentType
    work_model: WorkModel
    city: str | None
    state: str | None
    remote_country_scope: str | None
    salary_min: float | None
    salary_max: float | None
    description: str
    published_at: datetime | None
    hours_per_day: float | None
    hours_per_week: float | None
    status: JobStatus


@dataclass(frozen=True)
class RankingResult:
    total: int
    breakdown: dict[str, int]


def rank_job(
    job: RankingInput,
    eligibility_status: EligibilityStatus,
    weights: RankingWeightsConfig,
    *,
    now: datetime | None = None,
) -> RankingResult | None:
    if eligibility_status is not EligibilityStatus.ELIGIBLE:
        return None
    if job.status in {JobStatus.DISMISSED, JobStatus.ARCHIVED}:
        return None

    reference_now = _ensure_aware(now or datetime.now(UTC))
    breakdown = {
        "employment_type": _employment_points(job.employment_type, weights),
        "work_model": _work_model_points(job, weights),
        "salary_disclosed": _salary_points(job, weights),
        "benefits": _benefit_points(job.description, weights),
        "freshness": _freshness_points(job.published_at, reference_now, weights),
        "hours_disclosed": _hours_points(job, weights),
    }
    return RankingResult(total=sum(breakdown.values()), breakdown=breakdown)


def _employment_points(employment_type: EmploymentType, weights: RankingWeightsConfig) -> int:
    return weights.employment_type.get(employment_type.value, 0)


def _work_model_points(job: RankingInput, weights: RankingWeightsConfig) -> int:
    if job.work_model is WorkModel.REMOTE and normalize_text(job.remote_country_scope) in {
        "br",
        "brasil",
        "brazil",
    }:
        return weights.work_model.get("remote_brazil", 0)
    if job.work_model is WorkModel.HYBRID and is_belo_horizonte(job.city, job.state):
        return weights.work_model.get("hybrid_belo_horizonte", 0)
    if job.work_model is WorkModel.ONSITE and is_belo_horizonte(job.city, job.state):
        return weights.work_model.get("onsite_belo_horizonte", 0)
    return 0


def _salary_points(job: RankingInput, weights: RankingWeightsConfig) -> int:
    if job.salary_min is not None or job.salary_max is not None:
        return weights.additional.salary_disclosed
    return 0


def _benefit_points(description: str, weights: RankingWeightsConfig) -> int:
    normalized_description = normalize_text(description)
    matches = {
        normalize_text(keyword)
        for keyword in weights.benefit_keywords
        if normalize_text(keyword) in normalized_description
    }
    return min(
        weights.additional.benefit_keyword_max,
        len(matches) * weights.additional.benefit_keyword_points,
    )


def _freshness_points(
    published_at: datetime | None, now: datetime, weights: RankingWeightsConfig
) -> int:
    if published_at is None:
        return 0
    published = _ensure_aware(published_at)
    if now - published <= timedelta(days=weights.additional.freshness_days):
        return weights.additional.freshness
    return 0


def _hours_points(job: RankingInput, weights: RankingWeightsConfig) -> int:
    if job.hours_per_day is not None or job.hours_per_week is not None:
        return weights.additional.hours_disclosed
    return 0


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
