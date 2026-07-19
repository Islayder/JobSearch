from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from radar_vagas.canonicalization.normalize import normalize_url
from radar_vagas.collectors.common import (
    as_text,
    compact_text,
    html_to_text,
    infer_employment_type,
    parse_location_text,
)
from radar_vagas.domain.enums import WorkModel
from radar_vagas.ingestion.import_schema import ImportedPosting


def map_jobposting_object(
    value: dict[str, Any],
    *,
    page_url: str,
    source_name: str | None = None,
    default_company: str | None = None,
    item_index: int = 1,
) -> ImportedPosting:
    organization = _first_dict(value.get("hiringOrganization"))
    company = as_text(organization.get("name")) if organization else None
    if not company:
        company = default_company or _host_label(page_url)

    title = as_text(value.get("title")) or as_text(value.get("name")) or "Vaga sem titulo"
    description = html_to_text(as_text(value.get("description")) or "")
    target_url = as_text(value.get("url")) or page_url
    identifier = _identifier(value.get("identifier"))

    location = _job_location(value)
    remote_hint = str(value.get("jobLocationType", "")).upper() == "TELECOMMUTE"
    location_info = parse_location_text(location, remote_hint=remote_hint)
    applicant_scope = _applicant_location_scope(value.get("applicantLocationRequirements"))
    if remote_hint or location_info["work_model"] == WorkModel.REMOTE.value:
        location_info["work_model"] = WorkModel.REMOTE.value
        location_info["remote_country_scope"] = (
            applicant_scope or location_info["remote_country_scope"] or "UNKNOWN"
        )

    salary = _salary(value.get("baseSalary") or value.get("estimatedSalary"))
    benefits = _list_text(value.get("jobBenefits"))
    metadata = _metadata(value, page_url=page_url, item_index=item_index)
    metadata["hiring_organization_same_as"] = (
        as_text(organization.get("sameAs")) if organization else None
    )
    metadata = {key: item for key, item in metadata.items() if item is not None}

    return ImportedPosting(
        source_name=source_name or f"JobPosting: {_host_label(page_url)}",
        source_type="jobposting",
        provider="jobposting",
        provider_scope=None,
        provider_external_id=normalize_url(target_url),
        provider_identity_key=f"jobposting:{normalize_url(target_url)}",
        external_id=identifier,
        url=target_url,
        title=title,
        company=company,
        location=location_info["location"],
        description=description,
        published_at=value.get("datePosted"),
        expires_at=value.get("validThrough"),
        employment_type=infer_employment_type(
            as_text(value.get("employmentType")),
            title,
            description,
        ),
        work_model=_work_model_from_info(location_info),
        country=location_info["country"],
        state=location_info["state"],
        city=location_info["city"],
        remote_country_scope=location_info["remote_country_scope"],
        salary_min=_salary_float(salary, "salary_min"),
        salary_max=_salary_float(salary, "salary_max"),
        salary_period=_salary_text(salary, "salary_period"),
        currency=_salary_text(salary, "currency"),
        benefits=benefits,
        application_url=target_url,
        metadata=metadata,
    )


def _identifier(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            identifier = _identifier(item)
            if identifier:
                return identifier
        return None
    if isinstance(value, dict):
        return as_text(value.get("value")) or as_text(value.get("name"))
    return as_text(value)


def _job_location(value: dict[str, Any]) -> str | None:
    locations = value.get("jobLocation")
    texts: list[str] = []
    for location in _as_list(locations):
        if isinstance(location, dict):
            address = location.get("address")
            if isinstance(address, dict):
                parts = [
                    as_text(address.get("addressLocality")),
                    as_text(address.get("addressRegion")),
                    _country_name(address.get("addressCountry")),
                ]
                texts.append(", ".join(part for part in parts if part))
            else:
                texts.append(as_text(address) or as_text(location.get("name")) or "")
        else:
            texts.append(as_text(location) or "")
    return "; ".join(text for text in texts if text) or None


def _applicant_location_scope(value: Any) -> str | None:
    texts = [as_text(item) for item in _as_list(value)]
    joined = " ".join(text for text in texts if text)
    normalized = joined.lower()
    if "brasil" in normalized or "brazil" in normalized or " br " in f" {normalized} ":
        return "Brasil"
    return compact_text(joined) or None


def _salary(value: Any) -> dict[str, float | str | None]:
    result: dict[str, float | str | None] = {
        "salary_min": None,
        "salary_max": None,
        "salary_period": None,
        "currency": None,
    }
    if not isinstance(value, dict):
        return result
    result["currency"] = as_text(value.get("currency"))
    salary_value = value.get("value")
    if isinstance(salary_value, dict):
        result["salary_min"] = _float_value(salary_value.get("minValue"))
        result["salary_max"] = _float_value(salary_value.get("maxValue"))
        fixed = _float_value(salary_value.get("value"))
        if fixed is not None:
            result["salary_min"] = fixed
            result["salary_max"] = fixed
        result["salary_period"] = as_text(salary_value.get("unitText"))
    else:
        fixed = _float_value(salary_value)
        if fixed is not None:
            result["salary_min"] = fixed
            result["salary_max"] = fixed
    if result["currency"] is None:
        result["currency"] = as_text(value.get("currencyCode"))
    return result


def _salary_float(data: dict[str, float | str | None], key: str) -> float | None:
    value = data.get(key)
    return value if isinstance(value, float) else None


def _salary_text(data: dict[str, float | str | None], key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _work_model_from_info(data: dict[str, str | None]) -> WorkModel:
    value = data.get("work_model") or WorkModel.UNKNOWN.value
    return WorkModel(value)


def _metadata(value: dict[str, Any], *, page_url: str, item_index: int) -> dict[str, Any]:
    keys = [
        "directApply",
        "industry",
        "occupationalCategory",
        "educationRequirements",
        "experienceRequirements",
        "skills",
        "qualifications",
        "responsibilities",
        "jobLocationType",
        "applicantLocationRequirements",
    ]
    metadata: dict[str, Any] = {
        "source_url": page_url,
        "json_ld_index": item_index,
    }
    for key in keys:
        if key in value:
            metadata[key] = value[key]
    return metadata


def _list_text(value: Any) -> list[str]:
    return [text for item in _as_list(value) if (text := as_text(item))]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
    return None


def _country_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return as_text(value.get("name")) or as_text(value.get("identifier"))
    return as_text(value)


def _float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _host_label(url: str) -> str:
    return urlsplit(url).hostname or "pagina-publica"
