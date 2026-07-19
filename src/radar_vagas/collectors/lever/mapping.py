from __future__ import annotations

from typing import Any

from radar_vagas.collectors.common import (
    as_text,
    html_to_text,
    infer_employment_type,
    infer_work_model,
    join_unique,
    parse_epoch_millis,
    parse_location_text,
)
from radar_vagas.domain.enums import WorkModel
from radar_vagas.ingestion.import_schema import ImportedPosting


def map_lever_posting(
    posting: dict[str, Any],
    *,
    company_name: str,
    board_token: str,
    source_name: str,
) -> ImportedPosting:
    categories = posting.get("categories") if isinstance(posting.get("categories"), dict) else {}
    title = as_text(posting.get("text")) or ""
    location_text = as_text(categories.get("location")) if isinstance(categories, dict) else None
    commitment = as_text(categories.get("commitment")) if isinstance(categories, dict) else None
    workplace_type = as_text(posting.get("workplaceType"))
    description = _description(posting)
    work_model = infer_work_model(location_text, workplace_type, title)
    if workplace_type and workplace_type.lower() == "remote":
        work_model = WorkModel.REMOTE
    location_info = parse_location_text(location_text, remote_hint=work_model is WorkModel.REMOTE)
    location_info["work_model"] = work_model.value

    public_url = as_text(posting.get("hostedUrl")) or as_text(posting.get("applyUrl"))
    external_id = as_text(posting.get("id"))
    return ImportedPosting(
        source_name=source_name,
        source_type="lever",
        provider="lever",
        provider_scope=board_token,
        provider_external_id=external_id,
        provider_identity_key=f"lever:{board_token}:{external_id}",
        external_id=external_id,
        url=public_url,
        title=title,
        company=company_name,
        location=location_info["location"],
        description=description,
        department=categories.get("department") if isinstance(categories, dict) else None,
        area=categories.get("team") if isinstance(categories, dict) else None,
        requirements=_lever_list_text(posting, "requirements"),
        responsibilities=_lever_list_text(posting, "responsibilities"),
        technologies=_lever_list_text(posting, "technologies").splitlines(),
        published_at=parse_epoch_millis(posting.get("createdAt")),
        employment_type=infer_employment_type(commitment, title),
        work_model=_work_model_from_info(location_info),
        country=location_info["country"],
        state=location_info["state"],
        city=location_info["city"],
        remote_country_scope=location_info["remote_country_scope"],
        application_url=as_text(posting.get("applyUrl")) or public_url,
        metadata={
            "board_token": board_token,
            "categories": categories,
            "team": categories.get("team") if isinstance(categories, dict) else None,
            "department": categories.get("department") if isinstance(categories, dict) else None,
            "commitment": commitment,
            "hosted_url": as_text(posting.get("hostedUrl")),
            "workplace_type": workplace_type,
            "lists": posting.get("lists") if isinstance(posting.get("lists"), list) else [],
        },
    )


def _description(posting: dict[str, Any]) -> str:
    parts: list[str] = []
    base = as_text(posting.get("descriptionPlain")) or html_to_text(
        as_text(posting.get("description"))
    )
    if base:
        parts.append(base)
    lists = posting.get("lists")
    if isinstance(lists, list):
        for item in lists:
            if not isinstance(item, dict):
                continue
            heading = as_text(item.get("text"))
            content = html_to_text(as_text(item.get("content")))
            if heading and content:
                parts.append(f"{heading}\n{content}")
            elif content:
                parts.append(content)
    additional = html_to_text(as_text(posting.get("additional")))
    if additional:
        parts.append(additional)
    return join_unique(parts)


def _lever_list_text(posting: dict[str, Any], marker: str) -> str:
    lists = posting.get("lists")
    if not isinstance(lists, list):
        return ""
    parts: list[str] = []
    for item in lists:
        if not isinstance(item, dict):
            continue
        heading = (as_text(item.get("text")) or "").lower()
        if marker not in heading:
            continue
        content = html_to_text(as_text(item.get("content")))
        if content:
            parts.append(content)
    return join_unique(parts)


def _work_model_from_info(data: dict[str, str | None]) -> WorkModel:
    value = data.get("work_model") or WorkModel.UNKNOWN.value
    return WorkModel(value)
