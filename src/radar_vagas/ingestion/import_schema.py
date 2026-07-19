from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from radar_vagas.canonicalization.normalize import (
    normalize_employment_type,
    normalize_work_model,
)
from radar_vagas.domain.enums import EmploymentType, WorkModel


class ImportedPosting(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_name: str
    source_type: str | None = "file_import"
    provider: str | None = None
    provider_scope: str | None = None
    provider_external_id: str | None = None
    provider_identity_key: str | None = None
    external_id: str | None = None
    url: str | None = None
    title: str
    company: str
    location: str | None = None
    description: str | None = None
    department: str | None = None
    area: str | None = None
    requirements: str | None = None
    responsibilities: str | None = None
    technologies: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    expires_at: datetime | None = None
    employment_type: EmploymentType = EmploymentType.UNKNOWN
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
    benefits: list[str] = Field(default_factory=list)
    application_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_name", "title", "company")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("campo obrigatório vazio")
        return value.strip()

    @field_validator(
        "external_id",
        "provider",
        "provider_scope",
        "provider_external_id",
        "provider_identity_key",
        "url",
        "source_type",
        "location",
        "description",
        "department",
        "area",
        "requirements",
        "responsibilities",
        "expires_at",
        "country",
        "state",
        "city",
        "remote_country_scope",
        "salary_period",
        "currency",
        "application_url",
        mode="before",
    )
    @classmethod
    def blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("employment_type", mode="before")
    @classmethod
    def parse_employment_type(cls, value: object) -> EmploymentType:
        if value is None or isinstance(value, str):
            return normalize_employment_type(value)
        if isinstance(value, EmploymentType):
            return value
        return EmploymentType.UNKNOWN

    @field_validator("work_model", mode="before")
    @classmethod
    def parse_work_model(cls, value: object) -> WorkModel:
        if value is None or isinstance(value, str):
            return normalize_work_model(value)
        if isinstance(value, WorkModel):
            return value
        return WorkModel.UNKNOWN

    @field_validator("benefits", mode="before")
    @classmethod
    def parse_benefits(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                import json

                decoded = json.loads(stripped)
                if not isinstance(decoded, list):
                    raise ValueError("benefits em JSON deve ser uma lista")
                return [str(item).strip() for item in decoded if str(item).strip()]
            separator = "|" if "|" in stripped else ";"
            return [part.strip() for part in stripped.split(separator) if part.strip()]
        return [str(value)]

    @field_validator("technologies", mode="before")
    @classmethod
    def parse_technologies(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                import json

                decoded = json.loads(stripped)
                if not isinstance(decoded, list):
                    raise ValueError("technologies em JSON deve ser uma lista")
                return [str(item).strip() for item in decoded if str(item).strip()]
            separator = "|" if "|" in stripped else ";"
            return [part.strip() for part in stripped.split(separator) if part.strip()]
        return [str(value)]

    @field_validator("metadata", mode="before")
    @classmethod
    def parse_metadata(cls, value: object) -> dict[str, Any]:
        if value is None or value == "":
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            import json

            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError("metadata deve ser um objeto JSON")
            return decoded
        raise ValueError("metadata deve ser um objeto")

    @model_validator(mode="after")
    def move_extra_fields_to_metadata(self) -> "ImportedPosting":
        extra = self.__pydantic_extra__ or {}
        if extra:
            self.metadata = {**extra, **self.metadata}
            self.__pydantic_extra__ = {}
        return self

    def description_with_benefits(self) -> str:
        description = self.description or ""
        if not self.benefits:
            return description
        benefits = "; ".join(self.benefits)
        return f"{description}\nBenefícios: {benefits}".strip()
