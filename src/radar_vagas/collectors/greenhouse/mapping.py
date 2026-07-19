from __future__ import annotations

from typing import Any

from radar_vagas.collectors.common import (
    as_text,
    compact_text,
    html_to_text,
    infer_employment_type,
    infer_work_model,
    metadata_text,
    parse_location_text,
)
from radar_vagas.domain.enums import WorkModel
from radar_vagas.ingestion.import_schema import ImportedPosting


def map_greenhouse_job(
    job: dict[str, Any],
    *,
    company_name: str,
    board_token: str,
    source_name: str,
) -> ImportedPosting:
    title = as_text(job.get("title")) or ""
    location_name = _location_name(job.get("location"))
    metadata_values = _metadata_values(job.get("metadata"))
    metadata_blob = metadata_text(metadata_values)
    location_info = parse_location_text(location_name)
    employment_type = infer_employment_type(title, metadata_blob)
    work_model = infer_work_model(location_name, title, metadata_blob)
    if work_model.value != "UNKNOWN":
        location_info["work_model"] = work_model.value
    description = html_to_text(as_text(job.get("content")) or "")
    public_url = as_text(job.get("absolute_url"))

    return ImportedPosting(
        source_name=source_name,
        source_type="greenhouse",
        provider="greenhouse",
        provider_scope=board_token,
        provider_external_id=as_text(job.get("id")),
        provider_identity_key=f"greenhouse:{board_token}:{as_text(job.get('id'))}",
        external_id=as_text(job.get("id")),
        url=public_url,
        title=title,
        company=company_name,
        location=location_info["location"],
        description=description,
        published_at=job.get("updated_at"),
        employment_type=employment_type,
        work_model=_work_model_from_info(location_info),
        country=location_info["country"],
        state=location_info["state"],
        city=location_info["city"],
        remote_country_scope=location_info["remote_country_scope"],
        application_url=public_url,
        metadata={
            "board_token": board_token,
            "departments": _names(job.get("departments")),
            "offices": _names(job.get("offices")),
            "raw_metadata": metadata_values,
            "greenhouse_updated_at": job.get("updated_at"),
            "location": job.get("location"),
        },
    )


def _location_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return as_text(value.get("name"))
    return as_text(value)


def _names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        name for item in value if isinstance(item, dict) and (name := as_text(item.get("name")))
    ]


def _metadata_values(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = compact_text(as_text(item.get("name")) or "")
        raw_value = item.get("value")
        text_value = as_text(raw_value)
        if name or text_value:
            result.append({"name": name, "value": text_value or ""})
    return result


def _work_model_from_info(data: dict[str, str | None]) -> WorkModel:
    value = data.get("work_model") or WorkModel.UNKNOWN.value
    return WorkModel(value)
