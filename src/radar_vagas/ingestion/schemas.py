from datetime import datetime

from pydantic import BaseModel, ConfigDict

from radar_vagas.domain.enums import EmploymentType, WorkModel


class FixtureSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slug: str
    source_type: str
    base_url: str | None = None


class FixturePosting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: FixtureSource
    external_id: str | None = None
    original_url: str
    raw_title: str
    raw_company: str
    raw_location: str
    raw_description: str
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    seniority: str | None = None
    work_model: WorkModel = WorkModel.UNKNOWN
    country: str | None = None
    state: str | None = None
    city: str | None = None
    remote_country_scope: str | None = None
    hours_per_day: float | None = None
    hours_per_week: float | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str | None = None
    currency: str | None = None
    application_url: str | None = None
    published_at: datetime | None = None
    expires_at: datetime | None = None
    course_requirement: str | None = None
    has_uninterpreted_course_requirement: bool = False


class FixtureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FixturePosting]
