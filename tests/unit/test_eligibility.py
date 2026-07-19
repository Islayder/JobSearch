import pytest

from radar_vagas.config.schemas import EligibilityRulesConfig
from radar_vagas.domain.enums import EligibilityStatus, EmploymentType, JobStatus, WorkModel
from radar_vagas.eligibility.service import EligibilityInput, evaluate_eligibility


def make_job(
    *,
    employment_type: EmploymentType,
    work_model: WorkModel,
    city: str | None = "Belo Horizonte",
    state: str | None = "MG",
    remote_country_scope: str | None = None,
    hours_per_day: float | None = None,
    company_name: str = "Empresa Teste",
    company_is_blocked: bool = False,
    job_status: JobStatus = JobStatus.NEW,
    has_existing_application: bool = False,
    has_uninterpreted_course_requirement: bool = False,
) -> EligibilityInput:
    return EligibilityInput(
        company_name=company_name,
        company_aliases=(),
        company_is_blocked=company_is_blocked,
        job_status=job_status,
        employment_type=employment_type,
        work_model=work_model,
        city=city,
        state=state,
        remote_country_scope=remote_country_scope,
        hours_per_day=hours_per_day,
        has_existing_application=has_existing_application,
        has_uninterpreted_course_requirement=has_uninterpreted_course_requirement,
    )


def evaluate(job: EligibilityInput) -> EligibilityStatus:
    return evaluate_eligibility(
        job,
        EligibilityRulesConfig(),
        {"empresa bloqueada": "Bloqueada em teste."},
    ).status


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (
            make_job(
                employment_type=EmploymentType.INTERNSHIP,
                work_model=WorkModel.REMOTE,
                city=None,
                state=None,
                remote_country_scope="Brasil",
            ),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.INTERNSHIP,
                work_model=WorkModel.REMOTE,
                city=None,
                state=None,
                remote_country_scope=None,
            ),
            EligibilityStatus.MANUAL_REVIEW,
        ),
        (
            make_job(
                employment_type=EmploymentType.INTERNSHIP,
                work_model=WorkModel.HYBRID,
            ),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.INTERNSHIP,
                work_model=WorkModel.ONSITE,
            ),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.INTERNSHIP,
                work_model=WorkModel.HYBRID,
                city="Contagem",
            ),
            EligibilityStatus.INELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.INTERNSHIP,
                work_model=WorkModel.ONSITE,
                city="Nova Lima",
            ),
            EligibilityStatus.INELIGIBLE,
        ),
    ],
)
def test_internship_rules(job: EligibilityInput, expected: EligibilityStatus) -> None:
    assert evaluate(job) is expected


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (
            make_job(
                employment_type=EmploymentType.TRAINEE,
                work_model=WorkModel.REMOTE,
                city=None,
                state=None,
                remote_country_scope="BR",
            ),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(employment_type=EmploymentType.TRAINEE, work_model=WorkModel.HYBRID),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.TRAINEE,
                work_model=WorkModel.ONSITE,
                hours_per_day=6,
            ),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.TRAINEE,
                work_model=WorkModel.ONSITE,
                hours_per_day=8,
            ),
            EligibilityStatus.INELIGIBLE,
        ),
        (
            make_job(
                employment_type=EmploymentType.TRAINEE,
                work_model=WorkModel.ONSITE,
                hours_per_day=None,
            ),
            EligibilityStatus.MANUAL_REVIEW,
        ),
    ],
)
def test_trainee_rules(job: EligibilityInput, expected: EligibilityStatus) -> None:
    assert evaluate(job) is expected


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (
            make_job(
                employment_type=EmploymentType.JUNIOR,
                work_model=WorkModel.REMOTE,
                city=None,
                state=None,
                remote_country_scope="Brazil",
            ),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(employment_type=EmploymentType.JUNIOR, work_model=WorkModel.HYBRID),
            EligibilityStatus.ELIGIBLE,
        ),
        (
            make_job(employment_type=EmploymentType.JUNIOR, work_model=WorkModel.ONSITE),
            EligibilityStatus.INELIGIBLE,
        ),
    ],
)
def test_junior_rules(job: EligibilityInput, expected: EligibilityStatus) -> None:
    assert evaluate(job) is expected


def test_scholarship_other_and_unknown_go_to_manual_review() -> None:
    assert (
        evaluate(make_job(employment_type=EmploymentType.SCHOLARSHIP, work_model=WorkModel.REMOTE))
        is EligibilityStatus.MANUAL_REVIEW
    )
    assert (
        evaluate(make_job(employment_type=EmploymentType.OTHER, work_model=WorkModel.UNKNOWN))
        is EligibilityStatus.MANUAL_REVIEW
    )
    assert (
        evaluate(make_job(employment_type=EmploymentType.UNKNOWN, work_model=WorkModel.UNKNOWN))
        is EligibilityStatus.MANUAL_REVIEW
    )


def test_remote_scope_restricted_to_other_country_is_ineligible() -> None:
    job = make_job(
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        city=None,
        state=None,
        remote_country_scope="Estados Unidos",
    )
    assert evaluate(job) is EligibilityStatus.INELIGIBLE


def test_blocked_company_overrides_other_rules() -> None:
    job = make_job(
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        remote_country_scope="Brasil",
        company_name="Empresa Bloqueada",
    )
    assert evaluate(job) is EligibilityStatus.INELIGIBLE


def test_application_history_and_archived_jobs_are_track_only() -> None:
    with_application = make_job(
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        remote_country_scope="Brasil",
        has_existing_application=True,
    )
    archived = make_job(
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        remote_country_scope="Brasil",
        job_status=JobStatus.ARCHIVED,
    )
    assert evaluate(with_application) is EligibilityStatus.TRACK_ONLY
    assert evaluate(archived) is EligibilityStatus.TRACK_ONLY


def test_uninterpreted_course_requirement_moves_eligible_job_to_manual_review() -> None:
    job = make_job(
        employment_type=EmploymentType.INTERNSHIP,
        work_model=WorkModel.REMOTE,
        city=None,
        state=None,
        remote_country_scope="Brasil",
        has_uninterpreted_course_requirement=True,
    )
    assert evaluate(job) is EligibilityStatus.MANUAL_REVIEW
