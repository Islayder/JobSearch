from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from radar_vagas.collectors.common import (
    as_text,
    html_to_text,
    infer_employment_type,
    infer_work_model,
)
from radar_vagas.domain.enums import EmploymentType, WorkModel
from radar_vagas.ingestion.import_schema import ImportedPosting


def map_gupy_job(
    job: dict[str, Any],
    *,
    source_name: str,
    page_number: int,
    position_in_results: int,
) -> ImportedPosting:
    job_id = as_text(job.get("id")) or ""
    title = as_text(job.get("name")) or ""
    public_url = as_text(job.get("jobUrl")) or ""
    country = as_text(job.get("country"))
    state = as_text(job.get("state"))
    city = as_text(job.get("city"))
    workplace_type = as_text(job.get("workplaceType"))
    job_type = as_text(job.get("type"))
    company = as_text(job.get("careerPageName")) or _company_from_url(public_url)
    work_model = _work_model(workplace_type, title)
    remote_country_scope = _remote_country_scope(work_model, country)

    return ImportedPosting(
        source_name=source_name,
        source_type="gupy",
        provider="gupy",
        provider_scope=None,
        provider_external_id=job_id,
        provider_identity_key=f"gupy:{job_id}",
        external_id=job_id,
        url=public_url,
        title=title,
        company=company,
        location=_location(city=city, state=state, country=country, workplace_type=workplace_type),
        description=html_to_text(as_text(job.get("description")) or ""),
        published_at=_parse_datetime(job.get("publishedDate")),
        expires_at=_parse_datetime(job.get("applicationDeadline")),
        employment_type=_employment_type(job_type, title),
        work_model=work_model,
        country=country,
        state=state,
        city=city,
        remote_country_scope=remote_country_scope,
        application_url=public_url,
        metadata={
            "career_page_id": job.get("careerPageId"),
            "career_page_name": company,
            "career_page_url": as_text(job.get("careerPageUrl")),
            "company_id": job.get("companyId"),
            "department": as_text(job.get("department")),
            "area": as_text(job.get("department")),
            "badges": job.get("badges") if isinstance(job.get("badges"), list) else [],
            "disabilities": job.get("disabilities")
            if isinstance(job.get("disabilities"), list)
            else [],
            "skills": job.get("skills") if isinstance(job.get("skills"), list) else [],
            "workplace_type": workplace_type,
            "job_type": job_type,
            "is_remote_work": job.get("isRemoteWork"),
            "page_number": page_number,
            "position_in_results": position_in_results,
        },
    )


def _employment_type(job_type: str | None, title: str) -> EmploymentType:
    normalized = (job_type or "").lower()
    if "internship" in normalized:
        return EmploymentType.INTERNSHIP
    if "trainee" in normalized:
        return EmploymentType.TRAINEE
    return infer_employment_type(job_type, title)


def _work_model(workplace_type: str | None, title: str) -> WorkModel:
    normalized = (workplace_type or "").lower()
    if normalized == "remote":
        return WorkModel.REMOTE
    if normalized == "hybrid":
        return WorkModel.HYBRID
    if normalized in {"on-site", "onsite", "presential"}:
        return WorkModel.ONSITE
    return infer_work_model(workplace_type, title)


def _remote_country_scope(work_model: WorkModel, country: str | None) -> str | None:
    if work_model is not WorkModel.REMOTE:
        return None
    normalized_country = (country or "").strip().lower()
    if normalized_country in {"brasil", "brazil", "br"}:
        return "Brasil"
    return "UNKNOWN"


def _location(
    *,
    city: str | None,
    state: str | None,
    country: str | None,
    workplace_type: str | None,
) -> str | None:
    parts = [part for part in [city, state, country] if part]
    if parts:
        return ", ".join(parts)
    return workplace_type


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _company_from_url(url: str) -> str:
    host = urlsplit(url).hostname or "Gupy"
    if host.endswith(".gupy.io"):
        return host.removesuffix(".gupy.io")
    return "Gupy"
