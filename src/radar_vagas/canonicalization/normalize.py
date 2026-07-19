import json
import re
import unicodedata
from collections.abc import Iterable
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from radar_vagas.domain.enums import EmploymentType, WorkModel

COMPANY_SUFFIXES = {
    "sa",
    "s/a",
    "s.a",
    "s.a.",
    "ltda",
    "eireli",
    "me",
    "epp",
}

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_NAMES = {"fbclid", "gclid", "msclkid"}


def remove_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return normalize_spaces(remove_accents(value).lower())


def normalize_company_name(value: str | None) -> str:
    text = normalize_text(value)
    text = re.sub(r"[^\w\s/]", " ", text)
    words = normalize_spaces(text).split()
    while words and words[-1] in COMPANY_SUFFIXES:
        words.pop()
    if len(words) >= 2 and words[-2:] == ["s", "a"]:
        words = words[:-2]
    return " ".join(words)


def normalize_title(value: str | None) -> str:
    text = normalize_text(value)
    return re.sub(r"[^\w\s+#]", " ", text)


def normalize_state(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    if text in {"mg", "minas gerais"}:
        return "MG"
    return text.upper()


def normalize_city(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    compact = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if compact in {"bh", "belo horizonte", "belo horizonte mg", "belo horizonte minas gerais"}:
        return "Belo Horizonte"
    return " ".join(word.capitalize() for word in compact.split())


def normalize_city_state(city: str | None, state: str | None) -> tuple[str | None, str | None]:
    return normalize_city(city), normalize_state(state)


def is_belo_horizonte(city: str | None, state: str | None = None) -> bool:
    normalized_city = normalize_city(city)
    normalized_state = normalize_state(state)
    return normalized_city == "Belo Horizonte" and normalized_state in {None, "MG"}


def normalize_url(value: str | None) -> str:
    if value is None:
        return ""
    stripped = value.strip()
    if not stripped:
        return ""
    parts = urlsplit(stripped)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    query_items = [
        (name, query_value)
        for name, query_value in parse_qsl(parts.query, keep_blank_values=False)
        if not name.lower().startswith(TRACKING_QUERY_PREFIXES)
        and name.lower() not in TRACKING_QUERY_NAMES
    ]
    query = urlencode(sorted(query_items))
    return urlunsplit((scheme, netloc, path, query, ""))


def generate_content_hash(values: Iterable[str | None]) -> str:
    payload = [normalize_text(value) for value in values]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


EMPLOYMENT_TYPE_TERMS: dict[EmploymentType, tuple[str, ...]] = {
    EmploymentType.INTERNSHIP: (
        "estagio",
        "intern",
        "internship",
        "pessoa estagiaria",
    ),
    EmploymentType.TRAINEE: ("trainee", "programa trainee"),
    EmploymentType.JUNIOR: ("junior", "jr", "analista junior"),
    EmploymentType.SCHOLARSHIP: ("bolsista", "bolsa de inovacao", "scholarship"),
}

WORK_MODEL_TERMS: dict[WorkModel, tuple[str, ...]] = {
    WorkModel.REMOTE: ("remoto", "remote", "home office", "100% remoto"),
    WorkModel.HYBRID: ("hibrido", "hybrid"),
    WorkModel.ONSITE: ("presencial", "onsite", "on site", "on-site"),
}


def normalize_employment_type(value: str | EmploymentType | None) -> EmploymentType:
    if isinstance(value, EmploymentType):
        return value
    normalized_value = normalize_text(value)
    if not normalized_value:
        return EmploymentType.UNKNOWN
    matches = [
        employment_type
        for employment_type, terms in EMPLOYMENT_TYPE_TERMS.items()
        if any(term in normalized_value for term in terms)
    ]
    return matches[0] if len(set(matches)) == 1 else EmploymentType.UNKNOWN


def normalize_work_model(value: str | WorkModel | None) -> WorkModel:
    if isinstance(value, WorkModel):
        return value
    normalized_value = normalize_text(value)
    if not normalized_value:
        return WorkModel.UNKNOWN
    matches = [
        work_model
        for work_model, terms in WORK_MODEL_TERMS.items()
        if any(term in normalized_value for term in terms)
    ]
    return matches[0] if len(set(matches)) == 1 else WorkModel.UNKNOWN
