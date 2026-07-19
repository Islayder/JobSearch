from datetime import UTC, datetime

from radar_vagas.config.schemas import RankingWeightsConfig
from radar_vagas.domain.enums import EligibilityStatus, EmploymentType, JobStatus, WorkModel
from radar_vagas.ranking.service import RankingInput, rank_job


def make_ranking_input(
    *,
    salary_min: float | None = 1500,
    eligibility_status: EligibilityStatus = EligibilityStatus.ELIGIBLE,
) -> tuple[RankingInput, EligibilityStatus]:
    return (
        RankingInput(
            employment_type=EmploymentType.INTERNSHIP,
            work_model=WorkModel.REMOTE,
            city=None,
            state=None,
            remote_country_scope="Brasil",
            salary_min=salary_min,
            salary_max=None,
            description="Benefícios com vale alimentação, saúde e auxílio internet.",
            published_at=datetime(2026, 7, 17, tzinfo=UTC),
            hours_per_day=6,
            hours_per_week=30,
            status=JobStatus.NEW,
        ),
        eligibility_status,
    )


def test_ranking_uses_configured_weights_and_breakdown() -> None:
    job, status = make_ranking_input()
    ranking = rank_job(
        job,
        status,
        RankingWeightsConfig(),
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )

    assert ranking is not None
    assert ranking.breakdown["employment_type"] == 40
    assert ranking.breakdown["work_model"] == 30
    assert ranking.breakdown["salary_disclosed"] == 5
    assert ranking.breakdown["benefits"] == 5
    assert ranking.breakdown["freshness"] == 5
    assert ranking.breakdown["hours_disclosed"] == 2
    assert ranking.total == 87


def test_ineligible_job_has_no_ranking() -> None:
    job, _status = make_ranking_input()
    assert rank_job(job, EligibilityStatus.INELIGIBLE, RankingWeightsConfig()) is None


def test_salary_absence_does_not_remove_eligible_ranking() -> None:
    job, status = make_ranking_input(salary_min=None)
    ranking = rank_job(
        job,
        status,
        RankingWeightsConfig(),
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )

    assert ranking is not None
    assert ranking.breakdown["salary_disclosed"] == 0
    assert ranking.total == 82
