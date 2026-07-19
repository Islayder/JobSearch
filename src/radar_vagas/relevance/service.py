from __future__ import annotations

from dataclasses import dataclass

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.config.schemas import RelevanceRulesConfig
from radar_vagas.domain.enums import RelevanceStatus


@dataclass(frozen=True)
class RoleRelevanceInput:
    title: str
    department: str | None = None
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


def evaluate_role_relevance(
    role: RoleRelevanceInput,
    rules: RelevanceRulesConfig,
) -> RoleRelevanceResult:
    title = normalize_text(role.title)
    department = normalize_text(role.department)
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
    adjacent_matches = _matches(
        rules.adjacent_terms,
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
    adjacent_score = _score_matches(adjacent_matches, rules)
    technology_score = _score_matches(technology_matches, rules, technology=True)
    negative_score = _score_matches(negative_matches, rules, negative=True)
    score = max(core_score + technology_score, adjacent_score) - negative_score

    status = _status(
        core_score=core_score + technology_score,
        adjacent_score=adjacent_score,
        negative_score=negative_score,
        score=score,
        rules=rules,
    )
    reason = {
        "explanation": rules.explanations.get(status.value.lower(), status.value),
        "core_matches": _flatten_match_terms(core_matches),
        "adjacent_matches": _flatten_match_terms(adjacent_matches),
        "technology_matches": _flatten_match_terms(technology_matches),
        "negative_matches": _flatten_match_terms(negative_matches),
        "core_score": core_score + technology_score,
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
    if len(term) <= 3:
        return f" {term} " in f" {text} "
    return term in text


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
    if adjacent_score >= rules.thresholds.adjacent:
        return RelevanceStatus.ADJACENT
    if score >= rules.thresholds.manual_review:
        return RelevanceStatus.MANUAL_REVIEW
    return RelevanceStatus.UNRELATED


def _flatten_match_terms(matches: dict[str, set[str]]) -> dict[str, list[str]]:
    return {field: sorted(values) for field, values in matches.items() if values}
