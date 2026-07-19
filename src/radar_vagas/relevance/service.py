from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.config.schemas import RelevanceRulesConfig
from radar_vagas.domain.enums import RelevanceStatus

MAX_STRUCTURED_TEXT_LENGTH = 5_000
MAX_TECHNOLOGIES = 50
MAX_TECHNOLOGY_LENGTH = 120


@dataclass(frozen=True)
class RoleRelevanceInput:
    title: str
    department: str | None = None
    area: str | None = None
    description: str | None = None
    requirements: str | None = None
    responsibilities: str | None = None
    technologies: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleRelevanceResult:
    status: RelevanceStatus
    score: int
    reason: dict[str, object]
    rules_version: str


def build_role_relevance_input(
    *,
    title: str,
    department: str | None = None,
    area: str | None = None,
    description: str | None = None,
    requirements: str | None = None,
    responsibilities: str | None = None,
    technologies: Any = (),
    metadata: dict[str, Any] | None = None,
) -> RoleRelevanceInput:
    """Build the canonical relevance input used by dry-run and persistence."""

    structured_metadata = _structured_metadata(metadata or {})
    return RoleRelevanceInput(
        title=_trim_text(title) or "",
        department=_trim_text(department) or structured_metadata.get("department"),
        area=_trim_text(area) or structured_metadata.get("area"),
        description=_trim_text(description),
        requirements=_trim_text(requirements) or structured_metadata.get("requirements"),
        responsibilities=_trim_text(responsibilities)
        or structured_metadata.get("responsibilities"),
        technologies=normalize_technologies(
            _coerce_technologies(technologies) or structured_metadata.get("technologies", ())
        ),
    )


def build_role_relevance_input_from_posting(posting: Any) -> RoleRelevanceInput:
    return build_role_relevance_input(
        title=str(getattr(posting, "title", "") or ""),
        department=getattr(posting, "department", None),
        area=getattr(posting, "area", None),
        description=(
            posting.description_with_benefits()
            if hasattr(posting, "description_with_benefits")
            else getattr(posting, "description", None)
        ),
        requirements=getattr(posting, "requirements", None),
        responsibilities=getattr(posting, "responsibilities", None),
        technologies=getattr(posting, "technologies", ()),
        metadata=getattr(posting, "metadata", None),
    )


def build_role_relevance_input_from_job(job: Any) -> RoleRelevanceInput:
    return build_role_relevance_input(
        title=str(getattr(job, "canonical_title", "") or ""),
        department=getattr(job, "department", None),
        area=getattr(job, "area", None),
        description=getattr(job, "description", None),
        requirements=getattr(job, "requirements", None),
        responsibilities=getattr(job, "responsibilities", None),
        technologies=technologies_from_json(getattr(job, "technologies_json", None)),
    )


def normalize_technologies(value: Any) -> tuple[str, ...]:
    technologies = _coerce_technologies(value)
    deduped: dict[str, str] = {}
    for technology in technologies:
        stripped = _trim_text(technology, max_length=MAX_TECHNOLOGY_LENGTH)
        normalized = normalize_text(stripped)
        if stripped and normalized and normalized not in deduped:
            deduped[normalized] = stripped
    return tuple(deduped[key] for key in sorted(deduped))


def technologies_from_json(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    import json

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    return normalize_technologies(decoded)


def evaluate_role_relevance(
    role: RoleRelevanceInput,
    rules: RelevanceRulesConfig,
) -> RoleRelevanceResult:
    title = normalize_text(role.title)
    department = normalize_text(" ".join(value for value in [role.department, role.area] if value))
    description = normalize_text(
        " ".join(
            value for value in [role.description, role.requirements, role.responsibilities] if value
        )
    )
    technologies = normalize_text(" ".join(role.technologies))

    core_matches = _matches(
        rules.core_terms,
        title=title,
        department=department,
        description=description,
        technologies=technologies,
    )
    strong_adjacent_matches = _matches(
        rules.strong_adjacent_terms,
        title=title,
        department=department,
        description=description,
        technologies=technologies,
    )
    contextual_adjacent_matches = _matches(
        rules.contextual_adjacent_terms,
        title=title,
        department=department,
        description=description,
        technologies=technologies,
    )
    supporting_context_matches = _matches(
        rules.supporting_context_terms,
        title=title,
        department=department,
        description=description,
        technologies=technologies,
    )
    technology_matches = _matches(
        rules.technology_terms,
        title=title,
        department=department,
        description=description,
        technologies=technologies,
    )
    negative_matches = _matches(
        rules.negative_terms,
        title=title,
        department=department,
        description=description,
        technologies=technologies,
    )

    core_score = _score_matches(core_matches, rules)
    strong_adjacent_score = _score_matches(strong_adjacent_matches, rules)
    contextual_adjacent_score = _score_matches(contextual_adjacent_matches, rules)
    supporting_context_score = _score_matches(supporting_context_matches, rules, technology=True)
    adjacent_score = max(
        strong_adjacent_score,
        contextual_adjacent_score + supporting_context_score,
    )
    technology_score = _score_matches(technology_matches, rules, technology=True)
    negative_score = _score_matches(negative_matches, rules, negative=True)
    score = max(core_score + technology_score, adjacent_score) - negative_score

    status = _status(
        core_score=core_score + technology_score,
        strong_adjacent_score=strong_adjacent_score,
        contextual_adjacent_score=contextual_adjacent_score,
        supporting_context_score=supporting_context_score,
        adjacent_score=adjacent_score,
        negative_score=negative_score,
        score=score,
        rules=rules,
    )
    reason = {
        "explanation": rules.explanations.get(status.value.lower(), status.value),
        "core_matches": _flatten_match_terms(core_matches),
        "strong_adjacent_matches": _flatten_match_terms(strong_adjacent_matches),
        "contextual_adjacent_matches": _flatten_match_terms(contextual_adjacent_matches),
        "supporting_context_matches": _flatten_match_terms(supporting_context_matches),
        "technology_matches": _flatten_match_terms(technology_matches),
        "negative_matches": _flatten_match_terms(negative_matches),
        "core_score": core_score + technology_score,
        "strong_adjacent_score": strong_adjacent_score,
        "contextual_adjacent_score": contextual_adjacent_score,
        "supporting_context_score": supporting_context_score,
        "adjacent_score": adjacent_score,
        "negative_score": negative_score,
    }
    return RoleRelevanceResult(
        status=status,
        score=max(score, 0),
        reason=reason,
        rules_version=rules.version,
    )


def _matches(
    terms: list[str],
    *,
    title: str,
    department: str,
    description: str,
    technologies: str,
) -> dict[str, set[str]]:
    result = {
        "title": set[str](),
        "department": set[str](),
        "description": set[str](),
        "technologies": set[str](),
    }
    for raw_term in terms:
        term = normalize_text(raw_term)
        if not term:
            continue
        if _contains_term(title, term):
            result["title"].add(raw_term)
        if _contains_term(department, term):
            result["department"].add(raw_term)
        if _contains_term(description, term):
            result["description"].add(raw_term)
        if _contains_term(technologies, term):
            result["technologies"].add(raw_term)
    return result


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    escaped_term = re.escape(term).replace("\\ ", r"\s+")
    pattern = rf"(?<![a-z0-9]){escaped_term}(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _score_matches(
    matches: dict[str, set[str]],
    rules: RelevanceRulesConfig,
    *,
    technology: bool = False,
    negative: bool = False,
) -> int:
    if negative:
        return sum(len(values) for values in matches.values()) * rules.weights.negative
    if technology:
        return sum(len(values) for values in matches.values()) * rules.weights.technology
    return (
        len(matches["title"]) * rules.weights.title
        + len(matches["department"]) * rules.weights.department
        + len(matches["description"]) * rules.weights.description
        + len(matches["technologies"]) * rules.weights.technology
    )


def _status(
    *,
    core_score: int,
    strong_adjacent_score: int,
    contextual_adjacent_score: int,
    supporting_context_score: int,
    adjacent_score: int,
    negative_score: int,
    score: int,
    rules: RelevanceRulesConfig,
) -> RelevanceStatus:
    if (
        negative_score >= rules.thresholds.strong_negative
        and core_score <= rules.thresholds.core + 2
    ):
        return RelevanceStatus.UNRELATED
    if core_score >= rules.thresholds.core:
        return RelevanceStatus.CORE
    if strong_adjacent_score >= rules.thresholds.adjacent:
        return RelevanceStatus.ADJACENT
    if (
        contextual_adjacent_score > 0
        and supporting_context_score > 0
        and adjacent_score >= rules.thresholds.adjacent
    ):
        return RelevanceStatus.ADJACENT
    if score >= rules.thresholds.manual_review:
        return RelevanceStatus.MANUAL_REVIEW
    return RelevanceStatus.UNRELATED


def _flatten_match_terms(matches: dict[str, set[str]]) -> dict[str, list[str]]:
    return {field: sorted(values) for field, values in matches.items() if values}


def _structured_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    departments = [
        _text_from_metadata_key(metadata, key)
        for key in ("department", "departments", "team", "teams")
    ]
    areas = [_text_from_metadata_key(metadata, key) for key in ("area", "areas")]
    requirements = _text_from_metadata_key(metadata, "requirements")
    responsibilities = _text_from_metadata_key(metadata, "responsibilities")
    technologies = _metadata_technologies(metadata)
    return {
        "department": _trim_text(" ".join(value for value in departments if value)),
        "area": _trim_text(" ".join(value for value in areas if value)),
        "requirements": _trim_text(requirements),
        "responsibilities": _trim_text(responsibilities),
        "technologies": technologies,
    }


def _text_from_metadata_key(metadata: dict[str, Any], key: str) -> str | None:
    if key not in metadata:
        return None
    value = metadata.get(key)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.extend(str(nested) for nested in item.values() if str(nested).strip())
            elif str(item).strip():
                parts.append(str(item))
        return _trim_text(" ".join(parts))
    if isinstance(value, dict):
        return _trim_text(" ".join(str(item) for item in value.values() if str(item).strip()))
    return _trim_text(str(value))


def _metadata_technologies(metadata: dict[str, Any]) -> tuple[str, ...]:
    values: list[Any] = []
    for key in ("skills", "technologies", "technology", "tech_stack"):
        if key in metadata:
            values.append(metadata[key])
    return normalize_technologies(values)


def _coerce_technologies(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(str(item) for item in value.values() if str(item).strip())
    if isinstance(value, list | tuple | set):
        parts: list[str] = []
        for item in value:
            if isinstance(item, list | tuple | set | dict):
                parts.extend(_coerce_technologies(item))
            elif str(item).strip():
                parts.append(str(item))
        return tuple(parts)
    return (str(value),)


def _trim_text(value: Any, *, max_length: int = MAX_STRUCTURED_TEXT_LENGTH) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        return text[:max_length].rstrip()
    return text
