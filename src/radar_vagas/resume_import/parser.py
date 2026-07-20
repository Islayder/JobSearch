from __future__ import annotations

import re
from collections.abc import Iterable

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import (
    ExtractedBlockType,
    ResumeImportCandidateType,
)
from radar_vagas.resume_import.confidence import confidence_label
from radar_vagas.resume_import.models import ExtractedBlock, ExtractedDocument, ResumeCandidate
from radar_vagas.resume_import.sections import strip_section_prefix
from radar_vagas.resume_import.security import MAX_CANDIDATE_EXCERPT_CHARS

TECH_TERMS = (
    "Python",
    "SQL",
    "Power BI",
    "Power Query",
    "Excel",
    "Pandas",
    "NumPy",
    "Scikit-learn",
    "Machine Learning",
    "Estatistica",
    "ETL",
    "Airflow",
    "Tableau",
    "Looker",
    "Git",
    "GitHub",
    "Docker",
    "R",
    "Spark",
    "Databricks",
    "BigQuery",
    "PostgreSQL",
    "MySQL",
    "SQLite",
    "AWS",
    "Azure",
    "GCP",
    "JavaScript",
    "TypeScript",
)
LANGUAGE_NAMES = {
    "ingles": "Ingles",
    "english": "Ingles",
    "espanhol": "Espanhol",
    "spanish": "Espanhol",
    "portugues": "Portugues",
    "french": "Frances",
    "frances": "Frances",
}
LANGUAGE_LEVELS = (
    "basico",
    "intermediario",
    "avancado",
    "fluente",
    "nativo",
    "basic",
    "intermediate",
    "advanced",
    "fluent",
    "native",
)
CONTACT_RE = re.compile(
    r"(@|https?://|www\.|(\+?\d[\d\s().-]{7,}\d)|"
    r"\b(e-?mail|telefone|celular|whatsapp|endereco|linkedin|github)\b)",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"(?P<start>(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)?\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})"
    r"\s*(?:-|\u2013|\u2014|a|ate|até)\s*"
    r"(?P<end>atual|presente|momento|(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)?\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})",
    re.IGNORECASE,
)
SINGLE_DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{4}|(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.?\s*\d{4}|"
    r"20\d{2}|19\d{2})\b",
    re.IGNORECASE,
)
PROFESSIONAL_TITLE_TERMS = (
    "analista",
    "cientista",
    "engenheiro",
    "desenvolvedor",
    "estagiario",
    "estagio",
    "assistente",
    "data analyst",
    "analytics",
    "business intelligence",
    "dados",
    "bi",
)
LOCATION_TERMS = (
    "brasil",
    "minas gerais",
    "sao paulo",
    "rio de janeiro",
    "parana",
    "santa catarina",
    "rio grande do sul",
    "bahia",
    "pernambuco",
    "ceara",
    "goias",
    "distrito federal",
)


def parse_resume_document(document: ExtractedDocument) -> list[ResumeCandidate]:
    blocks = [block for block in document.blocks if _usable_block(block)]
    candidates: list[ResumeCandidate] = []
    degraded = document.quality in {"DEGRADED", "UNUSABLE"}
    candidates.extend(_headline_candidates(blocks, degraded=degraded))
    candidates.extend(_summary_candidates(blocks, degraded=degraded))
    candidates.extend(_skill_candidates(blocks, degraded=degraded))
    candidates.extend(_experience_candidates(blocks, degraded=degraded))
    candidates.extend(_project_candidates(blocks, degraded=degraded))
    candidates.extend(_education_candidates(blocks, degraded=degraded))
    candidates.extend(_language_candidates(blocks, degraded=degraded))
    candidates.extend(_ambiguous_candidates(blocks, candidates))
    return _dedupe_candidates(candidates)


def _headline_candidates(blocks: list[ExtractedBlock], *, degraded: bool) -> list[ResumeCandidate]:
    if degraded:
        return []
    fallback: tuple[ExtractedBlock, str] | None = None
    for block in blocks[:10]:
        if block.section_hint in {"skills", "experience", "project", "education", "languages"}:
            continue
        if block.block_type == ExtractedBlockType.HEADING:
            continue
        text = _clean_candidate_text(block)
        if not (12 <= len(text) <= 120) or _looks_like_contact(text):
            continue
        if _looks_like_identity_or_location(text):
            continue
        if _looks_like_professional_title(text):
            return [
                _candidate(
                    ResumeImportCandidateType.HEADLINE,
                    {"headline": text},
                    0.74,
                    "Titulo profissional explicito encontrado no inicio do curriculo.",
                    block,
                )
            ]
        if block.section_hint == "summary" and fallback is None:
            fallback = (block, text)
    if fallback is not None and _looks_like_professional_objective(fallback[1]):
        block, text = fallback
        return [
            _candidate(
                ResumeImportCandidateType.HEADLINE,
                {"headline": text},
                0.56,
                "Objetivo profissional curto encontrado; revise se deve ser o titulo.",
                block,
            )
        ]
    return []


def _summary_candidates(blocks: list[ExtractedBlock], *, degraded: bool) -> list[ResumeCandidate]:
    section_blocks = [block for block in blocks if block.section_hint == "summary"]
    text = _join_text(
        block for block in section_blocks if block.block_type != ExtractedBlockType.HEADING
    )
    if not text:
        return []
    return [
        _candidate(
            ResumeImportCandidateType.SUMMARY,
            {"summary": text[:1800]},
            _quality_score(0.78 if len(text) >= 80 else 0.6, degraded=degraded),
            "Texto encontrado em secao de resumo/objetivo.",
            *section_blocks[:3],
        )
    ]


def _skill_candidates(blocks: list[ExtractedBlock], *, degraded: bool) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    seen: set[str] = set()
    for block in blocks:
        if block.section_hint == "skills" and block.block_type != ExtractedBlockType.HEADING:
            for name in _split_skill_names(_clean_candidate_text(block)):
                _add_skill_candidate(
                    candidates,
                    seen,
                    name,
                    block,
                    _quality_score(0.72, degraded=degraded),
                    "Habilidade declarada na secao de competencias; confirme antes de usar.",
                )
        elif block.section_hint in {"experience", "project", "education"}:
            for name in _known_tech_terms(block.text):
                _add_skill_candidate(
                    candidates,
                    seen,
                    name,
                    block,
                    _quality_score(0.84, degraded=degraded),
                    "Habilidade citada em experiencia, projeto ou formacao.",
                    evidence_title=_evidence_title(block),
                    evidence_type=block.section_hint,
                )
    return candidates


def _experience_candidates(
    blocks: list[ExtractedBlock],
    *,
    degraded: bool,
) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for group in _group_section_items(blocks, "experience"):
        payload = _role_payload_from_group(group)
        if payload is None:
            continue
        text = _join_text(group)
        payload["skills"] = _known_tech_terms(text)
        candidates.append(
            _candidate(
                ResumeImportCandidateType.EXPERIENCE,
                payload,
                _quality_score(0.74 if payload.get("organization") else 0.56, degraded=degraded),
                "Item encontrado na secao de experiencia; revise cargo, empresa e periodo.",
                *group,
            )
        )
    return candidates


def _project_candidates(blocks: list[ExtractedBlock], *, degraded: bool) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for group in _group_section_items(blocks, "project"):
        text = _join_text(group)
        name, description = _project_parts(group, text)
        if not name:
            continue
        candidates.append(
            _candidate(
                ResumeImportCandidateType.PROJECT,
                {
                    "name": name,
                    "description": description,
                    "technologies": _known_tech_terms(text),
                    "source_ref": _first_url(text),
                },
                _quality_score(0.76, degraded=degraded),
                "Projeto encontrado em secao dedicada.",
                *group,
            )
        )
    return candidates


def _education_candidates(blocks: list[ExtractedBlock], *, degraded: bool) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for group in _group_section_items(blocks, "education"):
        text = _join_text(group)
        institution, course = _education_parts_from_group(group, text)
        if not institution or not course:
            continue
        candidates.append(
            _candidate(
                ResumeImportCandidateType.EDUCATION,
                {
                    "institution": institution,
                    "course": course,
                    "status": _education_status(text),
                    "start_date": None,
                    "end_date": _last_year(text),
                },
                _quality_score(0.7, degraded=degraded),
                "Formacao encontrada em secao academica.",
                *group,
            )
        )
    return candidates


def _language_candidates(blocks: list[ExtractedBlock], *, degraded: bool) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for block in blocks:
        if block.section_hint != "languages" or block.block_type == ExtractedBlockType.HEADING:
            continue
        text = _clean_candidate_text(block)
        normalized = normalize_text(text)
        language = next((label for key, label in LANGUAGE_NAMES.items() if key in normalized), None)
        level = next((item for item in LANGUAGE_LEVELS if item in normalized), None)
        if language and level:
            candidates.append(
                _candidate(
                    ResumeImportCandidateType.LANGUAGE,
                    {"name": language, "level": level, "evidence": [text]},
                    _quality_score(0.82, degraded=degraded),
                    "Idioma e nivel encontrados juntos na secao de idiomas.",
                    block,
                )
            )
    return candidates


def _ambiguous_candidates(
    blocks: list[ExtractedBlock],
    existing: list[ResumeCandidate],
) -> list[ResumeCandidate]:
    used_blocks = {block_id for candidate in existing for block_id in candidate.block_ids}
    candidates: list[ResumeCandidate] = []
    for block in blocks:
        if block.block_id in used_blocks or block.block_type == ExtractedBlockType.HEADING:
            continue
        if block.section_hint in {None, "skills", "experience", "project", "education"}:
            text = _clean_candidate_text(block)
            if len(text) >= 18 and (
                _known_tech_terms(text)
                or any(word in normalize_text(text) for word in ("dados", "analise", "projeto"))
            ):
                candidates.append(
                    _candidate(
                        ResumeImportCandidateType.AMBIGUOUS,
                        {"text": text},
                        0.35,
                        "Trecho relevante, mas sem estrutura suficiente para importacao.",
                        block,
                    )
                )
    return candidates[:8]


def _group_section_items(
    blocks: list[ExtractedBlock],
    section: str,
) -> list[list[ExtractedBlock]]:
    groups: list[list[ExtractedBlock]] = []
    current: list[ExtractedBlock] = []
    for block in blocks:
        if block.section_hint != section:
            continue
        if block.block_type == ExtractedBlockType.HEADING:
            continue
        text = _clean_candidate_text(block)
        if not text:
            continue
        if (
            current
            and block.block_type != ExtractedBlockType.LIST_ITEM
            and _starts_new_item(text, section, current)
        ):
            groups.append(current)
            current = [block]
        else:
            current.append(block)
    if current:
        groups.append(current)
    return groups


def _starts_new_item(text: str, section: str, current: list[ExtractedBlock]) -> bool:
    if _is_bullet(text):
        return False
    current_text = _join_text(current)
    normalized = normalize_text(text)
    if section == "experience":
        if _has_date_signal(text) and _has_date_signal(current_text):
            return True
        return _looks_like_role_header(text) and _group_has_details(current)
    if section == "project":
        return _looks_like_project_header(text) and _group_has_details(current)
    if section == "education":
        if _looks_like_education_start(text) and _group_has_details(current):
            return True
        return bool(
            _looks_like_course(text) and _looks_like_course(current_text) and "curso" in normalized
        )
    return False


def _group_has_details(blocks: list[ExtractedBlock]) -> bool:
    if len(blocks) >= 2:
        return True
    text = _join_text(blocks)
    return len(text) > 140 or _is_bullet(text)


def _role_payload_from_group(blocks: list[ExtractedBlock]) -> dict[str, object] | None:
    lines = [_clean_candidate_text(block) for block in blocks]
    lines = [line for line in lines if line]
    if not lines:
        return None
    full_text = "\n".join(lines)
    start_date, end_date = _date_range(full_text)
    header = next((line for line in lines if not _is_bullet(line)), lines[0])
    header_without_dates = _remove_dates(header).strip(" -|,")
    title, organization = _split_role_title_organization(header_without_dates)
    inline_description = None
    if not organization and ":" in header_without_dates:
        title_source, inline_description = _split_name_description(header_without_dates)
        title, organization = _split_role_title_organization(title_source)
    if not title:
        return None
    description_lines = []
    if inline_description:
        description_lines.append(inline_description)
    for line in lines:
        if line == header or _is_only_date_line(line):
            continue
        cleaned = _strip_bullet(_remove_dates(line)).strip()
        if cleaned and cleaned not in description_lines:
            description_lines.append(cleaned)
    description = "\n".join(description_lines).strip() or full_text
    return {
        "title": title[:255],
        "organization": organization[:255] if organization else None,
        "start_date": start_date,
        "end_date": end_date,
        "description": description[:1800],
    }


def _project_parts(blocks: list[ExtractedBlock], text: str) -> tuple[str | None, str | None]:
    lines = [_clean_candidate_text(block) for block in blocks]
    lines = [line for line in lines if line and not _is_only_url(line)]
    if not lines:
        return None, None
    name, inline_description = _split_name_description(lines[0])
    description_lines = []
    if inline_description:
        description_lines.append(inline_description)
    description_lines.extend(_strip_bullet(line) for line in lines[1:] if not _is_only_url(line))
    description = "\n".join(line for line in description_lines if line).strip() or None
    if name == text and len(name) > 120:
        name = name[:120]
    return name[:255] if name else None, description


def _education_parts_from_group(
    blocks: list[ExtractedBlock],
    text: str,
) -> tuple[str | None, str | None]:
    lines = [_clean_candidate_text(block) for block in blocks]
    lines = [line for line in lines if line and not _is_only_date_line(line)]
    if len(lines) == 1:
        return _education_parts(lines[0])
    course = next((line for line in lines if _looks_like_course(line)), None)
    institution = next(
        (
            line
            for line in lines
            if line != course and not _education_status(line) and not _has_date_signal(line)
        ),
        None,
    )
    if institution and course:
        return institution[:255], course[:255]
    return _education_parts(text)


def _looks_like_role_header(text: str) -> bool:
    normalized = normalize_text(text)
    if _looks_like_professional_title(text) and len(text) <= 140:
        return True
    return bool(
        len(text) <= 160
        and (" - " in text or " | " in text or " em " in normalized or " at " in normalized)
    )


def _looks_like_project_header(text: str) -> bool:
    normalized = normalize_text(text)
    return len(text) <= 140 and (
        "projeto" in normalized
        or "dashboard" in normalized
        or "portfolio" in normalized
        or bool(_first_url(text))
        or (" - " in text and not _has_date_signal(text))
    )


def _looks_like_education_start(text: str) -> bool:
    normalized = normalize_text(text)
    return _looks_like_course(text) or any(
        term in normalized
        for term in (
            "universidade",
            "faculdade",
            "instituto",
            "college",
            "university",
            "curso",
            "certificacao",
        )
    )


def _add_skill_candidate(
    candidates: list[ResumeCandidate],
    seen: set[str],
    name: str,
    block: ExtractedBlock,
    score: float,
    explanation: str,
    *,
    evidence_title: str | None = None,
    evidence_type: str | None = None,
) -> None:
    normalized = normalize_text(name)
    if not normalized or normalized in seen:
        return
    seen.add(normalized)
    evidence = []
    if evidence_title is not None:
        evidence = [
            {
                "title": evidence_title,
                "description": block.text,
                "source_ref": _source_reference(block),
                "evidence_type": _evidence_type(evidence_type),
            }
        ]
    candidates.append(
        _candidate(
            ResumeImportCandidateType.SKILL,
            {
                "name": name,
                "category": None,
                "level": _skill_level(block.text),
                "evidence": evidence,
            },
            score,
            explanation,
            block,
        )
    )


def _candidate(
    candidate_type: ResumeImportCandidateType,
    payload: dict[str, object],
    score: float,
    explanation: str,
    *blocks: ExtractedBlock,
) -> ResumeCandidate:
    first_block = blocks[0] if blocks else None
    return ResumeCandidate(
        candidate_type=candidate_type,
        payload=payload,
        confidence_score=score,
        confidence_label=confidence_label(score),
        explanation=explanation,
        source_reference=_source_reference(first_block) if first_block else None,
        source_excerpt=_excerpt(_join_text(blocks)),
        block_ids=tuple(block.block_id for block in blocks),
        page_number=first_block.page_number if first_block else None,
        section_hint=first_block.section_hint if first_block else None,
    )


def _usable_block(block: ExtractedBlock) -> bool:
    if not block.text.strip():
        return False
    return not _looks_like_contact(block.text)


def _looks_like_contact(text: str) -> bool:
    normalized = normalize_text(text)
    if CONTACT_RE.search(text):
        return True
    return normalized.startswith(("email", "telefone", "celular", "linkedin", "github", "endereco"))


def _looks_like_identity_or_location(text: str) -> bool:
    normalized = normalize_text(text)
    if _looks_like_professional_title(text):
        return False
    if any(term in normalized for term in LOCATION_TERMS):
        return True
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", text)
    if 2 <= len(words) <= 5 and "," not in text and not _has_sentence_signal(text):
        titled = sum(1 for word in words if word[:1].isupper())
        return titled >= max(2, len(words) - 1)
    return False


def _looks_like_professional_title(text: str) -> bool:
    normalized = normalize_text(text)
    return any(term in normalized for term in PROFESSIONAL_TITLE_TERMS)


def _looks_like_professional_objective(text: str) -> bool:
    normalized = normalize_text(text)
    return _looks_like_professional_title(text) and any(
        term in normalized for term in ("busco", "objetivo", "oportunidade", "atuar", "vaga")
    )


def _has_sentence_signal(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        term in normalized
        for term in (" com ", " em ", " para ", " de ", " experiencia", " conhecimento")
    )


def _quality_score(score: float, *, degraded: bool) -> float:
    return min(score, 0.45) if degraded else score


def _clean_candidate_text(block: ExtractedBlock) -> str:
    return strip_section_prefix(_strip_bullet(block.text)).strip()


def _split_skill_names(text: str) -> list[str]:
    cleaned = _strip_bullet(text)
    cleaned = re.sub(r"\b(e|and)\b", ",", cleaned, flags=re.IGNORECASE)
    pieces = re.split(r"[,;|/•\n]+|\s+-\s+|\s{2,}", cleaned)
    skills: list[str] = []
    for piece in pieces:
        candidate = piece.strip(" .:-")
        if 2 <= len(candidate) <= 60 and not _looks_like_contact(candidate):
            skills.append(_title_skill(candidate))
    return skills


def _known_tech_terms(text: str) -> list[str]:
    normalized_text = normalize_text(text)
    terms: list[str] = []
    for term in TECH_TERMS:
        normalized_term = normalize_text(term)
        if re.search(rf"(?<!\w){re.escape(normalized_term)}(?!\w)", normalized_text):
            terms.append(term)
    return terms


def _role_payload(text: str) -> dict[str, object] | None:
    cleaned = _strip_bullet(text)
    if len(cleaned) < 12:
        return None
    start_date, end_date = _date_range(cleaned)
    without_dates = DATE_RE.sub("", cleaned).strip(" -|,")
    title, description = _split_name_description(without_dates)
    organization: str | None = None
    if " at " in title.lower():
        title, organization = re.split(r"\s+at\s+", title, maxsplit=1, flags=re.IGNORECASE)
    elif " em " in normalize_text(title):
        parts = re.split(r"\s+em\s+", title, maxsplit=1, flags=re.IGNORECASE)
        title, organization = parts[0], parts[1] if len(parts) > 1 else None
    else:
        parts = [part.strip() for part in re.split(r"\s[-|]\s", title, maxsplit=1)]
        if len(parts) == 2 and len(parts[0]) <= 80 and len(parts[1]) <= 120:
            title, organization = parts
    if not title:
        return None
    return {
        "title": title[:255],
        "organization": organization[:255] if organization else None,
        "start_date": start_date,
        "end_date": end_date,
        "description": description or cleaned,
    }


def _split_role_title_organization(text: str) -> tuple[str, str | None]:
    cleaned = _strip_bullet(text).strip(" -|,")
    organization: str | None = None
    if not cleaned:
        return "", None
    normalized = normalize_text(cleaned)
    if " at " in cleaned.lower():
        title, organization = re.split(r"\s+at\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)
    elif " em " in normalized:
        parts = re.split(r"\s+em\s+", cleaned, maxsplit=1, flags=re.IGNORECASE)
        title, organization = parts[0], parts[1] if len(parts) > 1 else None
    else:
        parts = [part.strip() for part in re.split(r"\s[-|]\s", cleaned, maxsplit=1)]
        if len(parts) == 2 and len(parts[0]) <= 100 and len(parts[1]) <= 140:
            title, organization = parts
        else:
            title = cleaned
    return title.strip(" -|,")[:255], organization.strip(" -|,")[:255] if organization else None


def _split_name_description(text: str) -> tuple[str, str | None]:
    cleaned = _strip_bullet(text)
    parts = [part.strip(" .") for part in re.split(r"\s[-|:]\s|:\s", cleaned, maxsplit=1)]
    if len(parts) == 1:
        return cleaned[:255], None
    name, description = parts[0], parts[1]
    if not name:
        name = description[:80]
    return name[:255], description or None


def _education_parts(text: str) -> tuple[str | None, str | None]:
    cleaned = _strip_bullet(text)
    parts = [part.strip(" .") for part in re.split(r"\s[-|]\s|,\s", cleaned, maxsplit=1)]
    if len(parts) == 2:
        left, right = parts
        if _looks_like_course(left) and not _looks_like_course(right):
            return right[:255], left[:255]
        return left[:255], right[:255]
    if _looks_like_course(cleaned):
        return "Instituicao a revisar", cleaned[:255]
    return None, None


def _looks_like_course(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        term in normalized
        for term in (
            "ciencia",
            "engenharia",
            "analise",
            "sistemas",
            "estatistica",
            "dados",
            "administracao",
            "tecnologia",
            "bacharel",
            "graduacao",
        )
    )


def _education_status(text: str) -> str | None:
    normalized = normalize_text(text)
    if "cursando" in normalized or "em andamento" in normalized:
        return "cursando"
    if "concluido" in normalized or "concluida" in normalized or "completo" in normalized:
        return "concluido"
    return None


def _last_year(text: str) -> str | None:
    years = re.findall(r"\b(20\d{2}|19\d{2})\b", text)
    return years[-1] if years else None


def _date_range(text: str) -> tuple[str | None, str | None]:
    match = DATE_RE.search(normalize_text(text))
    if match is None:
        single_date = SINGLE_DATE_RE.search(normalize_text(text))
        if single_date is None:
            return None, None
        return single_date.group(0).strip(), None
    return match.group("start").strip(), match.group("end").strip()


def _has_date_signal(text: str) -> bool:
    normalized = normalize_text(text)
    return bool(DATE_RE.search(normalized) or SINGLE_DATE_RE.search(normalized))


def _remove_dates(text: str) -> str:
    normalized_safe = DATE_RE.sub("", text)
    return SINGLE_DATE_RE.sub("", normalized_safe)


def _is_only_date_line(text: str) -> bool:
    cleaned = _strip_bullet(text).strip(" -\u2013\u2014|,.;:")
    if not cleaned:
        return False
    without_dates = _remove_dates(cleaned).strip(" -\u2013\u2014|,.;:")
    return not without_dates


def _is_bullet(text: str) -> bool:
    return bool(re.match(r"^[-*•○]\s+", text.strip()))


def _is_only_url(text: str) -> bool:
    return bool(re.fullmatch(r"https?://\S+|www\.\S+", text.strip(), flags=re.IGNORECASE))


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://\S+|www\.\S+", text, flags=re.IGNORECASE)
    return match.group(0).strip(").,;") if match else None


def _skill_level(text: str) -> str | None:
    normalized = normalize_text(text)
    for level in ("basico", "intermediario", "avancado", "fluente"):
        if level in normalized:
            return level
    return None


def _evidence_title(block: ExtractedBlock) -> str:
    text = _clean_candidate_text(block)
    return text[:120] or "Evidencia no curriculo"


def _evidence_type(section: str | None) -> str:
    if section == "experience":
        return "EXPERIENCE"
    if section == "project":
        return "PROJECT"
    if section == "education":
        return "EDUCATION"
    return "RESUME"


def _source_reference(block: ExtractedBlock | None) -> str | None:
    if block is None:
        return None
    if block.page_number is not None:
        return f"pagina {block.page_number}, bloco {block.block_id}"
    return f"bloco {block.block_id}"


def _excerpt(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return None
    return cleaned[:MAX_CANDIDATE_EXCERPT_CHARS]


def _join_text(blocks: Iterable[ExtractedBlock]) -> str:
    return "\n".join(_clean_candidate_text(block) for block in blocks if block.text.strip())


def _strip_bullet(text: str) -> str:
    return re.sub(r"^[-*•]\s+", "", text.strip())


def _title_skill(text: str) -> str:
    known_by_normalized = {normalize_text(term): term for term in TECH_TERMS}
    return known_by_normalized.get(normalize_text(text), text.strip())


def _dedupe_candidates(candidates: list[ResumeCandidate]) -> list[ResumeCandidate]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ResumeCandidate] = []
    for candidate in candidates:
        key = (
            candidate.candidate_type.value,
            normalize_text(str(candidate.payload)),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped
