import pytest

from radar_vagas.canonicalization.normalize import (
    normalize_employment_type,
    normalize_work_model,
)
from radar_vagas.domain.enums import EmploymentType, WorkModel


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("estágio", EmploymentType.INTERNSHIP),
        ("internship", EmploymentType.INTERNSHIP),
        ("pessoa estagiária", EmploymentType.INTERNSHIP),
        ("programa trainee", EmploymentType.TRAINEE),
        ("júnior", EmploymentType.JUNIOR),
        ("analista junior", EmploymentType.JUNIOR),
        ("bolsa de inovação", EmploymentType.SCHOLARSHIP),
        ("algo novo", EmploymentType.UNKNOWN),
    ],
)
def test_employment_type_variations(value: str, expected: EmploymentType) -> None:
    assert normalize_employment_type(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("remoto", WorkModel.REMOTE),
        ("home office", WorkModel.REMOTE),
        ("100% remoto", WorkModel.REMOTE),
        ("híbrido", WorkModel.HYBRID),
        ("hybrid", WorkModel.HYBRID),
        ("presencial", WorkModel.ONSITE),
        ("on-site", WorkModel.ONSITE),
        ("indefinido", WorkModel.UNKNOWN),
    ],
)
def test_work_model_variations(value: str, expected: WorkModel) -> None:
    assert normalize_work_model(value) is expected


def test_conflicting_work_model_terms_are_unknown() -> None:
    assert normalize_work_model("remoto e presencial") is WorkModel.UNKNOWN
