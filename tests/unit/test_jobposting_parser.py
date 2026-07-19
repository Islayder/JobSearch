from __future__ import annotations

from pathlib import Path

import pytest

from radar_vagas.collectors.jobposting.mapping import map_jobposting_object
from radar_vagas.collectors.jobposting.parser import (
    JobPostingJsonError,
    MultipleJobPostingsError,
    NoJobPostingError,
    extract_jobposting_objects,
    select_jobposting_object,
)
from radar_vagas.config.settings import PROJECT_ROOT
from radar_vagas.domain.enums import EmploymentType, WorkModel

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "http" / "jobposting"


def test_extracts_single_jobposting_and_maps_fields() -> None:
    objects = extract_jobposting_objects(_fixture("single.html"))

    posting = map_jobposting_object(objects[0], page_url="https://empresa.example/jobs/123")

    assert posting.title == "Estagio em Dados"
    assert posting.company == "Empresa Exemplo"
    assert posting.external_id == "job-123"
    assert posting.employment_type is EmploymentType.INTERNSHIP
    assert posting.work_model is WorkModel.REMOTE
    assert posting.remote_country_scope == "Brasil"
    assert posting.salary_min == 1200
    assert posting.salary_max == 1800
    assert posting.salary_period == "MONTH"
    assert posting.currency == "BRL"
    assert "alert" not in (posting.description or "")
    assert "Python" in (posting.description or "")
    assert posting.expires_at is not None
    assert posting.metadata["directApply"] is True


def test_extracts_list_and_requires_selection_for_multiple() -> None:
    objects = extract_jobposting_objects(_fixture("multiple.html"))

    assert len(objects) == 2
    with pytest.raises(MultipleJobPostingsError):
        select_jobposting_object(objects, include_all=False, selected_index=None)
    selected = select_jobposting_object(objects, include_all=False, selected_index=2)
    assert selected[0]["title"] == "Trainee em Dados"


def test_extracts_graph_and_marks_unknown_remote_scope() -> None:
    objects = extract_jobposting_objects(_fixture("graph.html"))

    posting = map_jobposting_object(objects[0], page_url="https://empresa.example/jobs/3")

    assert posting.work_model is WorkModel.REMOTE
    assert posting.remote_country_scope == "UNKNOWN"


def test_all_and_select_are_conflicting_options() -> None:
    objects = extract_jobposting_objects(_fixture("multiple.html"))

    with pytest.raises(Exception, match="--all"):
        select_jobposting_object(objects, include_all=True, selected_index=1)


def test_invalid_json_ld_and_missing_jobposting_are_clear_errors() -> None:
    with pytest.raises(JobPostingJsonError):
        extract_jobposting_objects(_fixture("invalid.html"))
    with pytest.raises(NoJobPostingError):
        extract_jobposting_objects(_fixture("none.html"))


def _fixture(name: str) -> str:
    return Path(FIXTURE_DIR / name).read_text(encoding="utf-8")
