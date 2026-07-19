from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from radar_vagas.canonicalization.normalize import (
    normalize_employment_type,
    normalize_text,
    normalize_work_model,
)
from radar_vagas.domain.enums import EmploymentType, WorkModel


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    for item in soup.find_all("li"):
        item.insert_before("\n- ")
    for block in soup.find_all(["p", "div", "section", "article", "br", "h1", "h2", "h3"]):
        block.append("\n")
    lines = [compact_text(line) for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def compact_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def join_unique(values: Iterable[str | None], *, separator: str = "\n") -> str:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = compact_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return separator.join(result)


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return compact_text(value)
    if isinstance(value, bool | int | float):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "value", "@id", "text"):
            if key in value:
                text = as_text(value[key])
                if text:
                    return text
    if isinstance(value, list):
        return ", ".join(text for item in value if (text := as_text(item)))
    return compact_text(str(value))


def metadata_text(metadata: Any) -> str:
    if metadata is None:
        return ""
    if isinstance(metadata, dict):
        return " ".join(filter(None, [as_text(key) for key in metadata.values()]))
    if isinstance(metadata, list):
        return " ".join(filter(None, [as_text(item) for item in metadata]))
    return as_text(metadata) or ""


def infer_employment_type(*values: str | None) -> EmploymentType:
    return normalize_employment_type(" ".join(value for value in values if value))


def infer_work_model(*values: str | None, remote_hint: bool = False) -> WorkModel:
    if remote_hint:
        return WorkModel.REMOTE
    return normalize_work_model(" ".join(value for value in values if value))


def parse_location_text(value: str | None, *, remote_hint: bool = False) -> dict[str, str | None]:
    text = compact_text(value)
    normalized = normalize_text(text)
    work_model = infer_work_model(text, remote_hint=remote_hint)
    if "hibrido" in normalized or "hybrid" in normalized:
        work_model = WorkModel.HYBRID
    elif "presencial" in normalized or "onsite" in normalized or "on site" in normalized:
        work_model = WorkModel.ONSITE
    elif "remoto" in normalized or "remote" in normalized:
        work_model = WorkModel.REMOTE

    city: str | None = None
    state: str | None = None
    country: str | None = None
    if "belo horizonte" in normalized or re.search(r"\bbh\b", normalized):
        city = "Belo Horizonte"
        state = "MG"
    elif "sao paulo" in normalized or "são paulo" in text.lower():
        city = "Sao Paulo"
        state = "SP"
    if "minas gerais" in normalized or re.search(r"\bmg\b", normalized):
        state = "MG"
    if "brasil" in normalized or "brazil" in normalized or re.search(r"\bbr\b", normalized):
        country = "Brasil"

    remote_scope = None
    if work_model is WorkModel.REMOTE:
        remote_scope = "Brasil" if country == "Brasil" else "UNKNOWN"

    return {
        "location": text or None,
        "work_model": work_model.value,
        "city": city,
        "state": state,
        "country": country,
        "remote_country_scope": remote_scope,
    }


def parse_epoch_millis(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def short_metadata_change(old: Any, new: Any, *, max_length: int = 500) -> dict[str, Any]:
    old_text = _shorten_for_revision(old, max_length=max_length)
    new_text = _shorten_for_revision(new, max_length=max_length)
    return {"old": old_text, "new": new_text}


def _shorten_for_revision(value: Any, *, max_length: int) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}...[truncated]"
