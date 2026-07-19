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
from radar_vagas.resume_import.security import MAX_CANDIDATE_EXCERPT_CHARS

TECH_TERMS = (
    "Python",
    "SQL",
    "Power BI",
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
    r"(@|(\+?\d[\d\s().-]{7,}\d)|\b(e-?mail|telefone|celular|whatsapp|endereco|linkedin)\b)",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"(?P<start>(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)?\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})"
    r"\s*(?:-|a|ate|até)\s*"
    r"(?P<end>atual|presente|momento|(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)?\.?\s*\d{4}|\d{1,2}/\d{4}|\d{4})",
    re.IGNORECASE,
)


def parse_resume_document(document: ExtractedDocument) -> list[ResumeCandidate]:
    blocks = [block for block in document.blocks if _usable_block(block)]
    candidates: list[ResumeCandidate] = []
    candidates.extend(_headline_candidates(blocks))
    candidates.extend(_summary_candidates(blocks))
    candidates.extend(_skill_candidates(blocks))
    candidates.extend(_experience_candidates(blocks))
    candidates.extend(_project_candidates(blocks))
    candidates.extend(_education_candidates(blocks))
    candidates.extend(_language_candidates(blocks))
    candidates.extend(_ambiguous_candidates(blocks, candidates))
    return _dedupe_candidates(candidates)


def _headline_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
    for block in blocks[:6]:
        if block.section_hint in {"skills", "experience", "project", "education", "languages"}:
            continue
        if block.block_type == ExtractedBlockType.HEADING:
            continue
        text = _strip_bullet(block.text)
        if 12 <= len(text) <= 120 and not _looks_like_contact(text):
            return [
                _candidate(
                    ResumeImportCandidateType.HEADLINE,
                    {"headline": text},
                    0.58,
                    "Linha curta no inicio do curriculo; revise se deve ser o titulo profissional.",
                    block,
                )
            ]
    return []


def _summary_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
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
            0.78 if len(text) >= 80 else 0.6,
            "Texto encontrado em secao de resumo/objetivo.",
            *section_blocks[:3],
        )
    ]


def _skill_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    seen: set[str] = set()
    for block in blocks:
        if block.section_hint == "skills" and block.block_type != ExtractedBlockType.HEADING:
            for name in _split_skill_names(block.text):
                _add_skill_candidate(
                    candidates,
                    seen,
                    name,
                    block,
                    0.72,
                    "Habilidade declarada na secao de competencias; confirme antes de usar.",
                )
        elif block.section_hint in {"experience", "project", "education"}:
            for name in _known_tech_terms(block.text):
                _add_skill_candidate(
                    candidates,
                    seen,
                    name,
                    block,
                    0.84,
                    "Habilidade citada em experiencia, projeto ou formacao.",
                    evidence_title=_evidence_title(block),
                    evidence_type=block.section_hint,
                )
    return candidates


def _experience_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for block in blocks:
        if block.section_hint != "experience" or block.block_type == ExtractedBlockType.HEADING:
            continue
        payload = _role_payload(block.text)
        if payload is None:
            continue
        payload["skills"] = _known_tech_terms(block.text)
        candidates.append(
            _candidate(
                ResumeImportCandidateType.EXPERIENCE,
                payload,
                0.74 if payload.get("organization") else 0.56,
                "Item encontrado na secao de experiencia; revise cargo, empresa e periodo.",
                block,
            )
        )
    return candidates


def _project_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for block in blocks:
        if block.section_hint != "project" or block.block_type == ExtractedBlockType.HEADING:
            continue
        name, description = _split_name_description(block.text)
        if not name:
            continue
        candidates.append(
            _candidate(
                ResumeImportCandidateType.PROJECT,
                {
                    "name": name,
                    "description": description,
                    "technologies": _known_tech_terms(block.text),
                    "source_ref": None,
                },
                0.76,
                "Projeto encontrado em secao dedicada.",
                block,
            )
        )
    return candidates


def _education_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for block in blocks:
        if block.section_hint != "education" or block.block_type == ExtractedBlockType.HEADING:
            continue
        institution, course = _education_parts(block.text)
        if not institution or not course:
            continue
        candidates.append(
            _candidate(
                ResumeImportCandidateType.EDUCATION,
                {
                    "institution": institution,
                    "course": course,
                    "status": _education_status(block.text),
                    "start_date": None,
                    "end_date": _last_year(block.text),
                },
                0.7,
                "Formacao encontrada em secao academica.",
                block,
            )
        )
    return candidates


def _language_candidates(blocks: list[ExtractedBlock]) -> list[ResumeCandidate]:
    candidates: list[ResumeCandidate] = []
    for block in blocks:
        if block.section_hint != "languages" or block.block_type == ExtractedBlockType.HEADING:
            continue
        normalized = normalize_text(block.text)
        language = next((label for key, label in LANGUAGE_NAMES.items() if key in normalized), None)
        level = next((item for item in LANGUAGE_LEVELS if item in normalized), None)
        if language and level:
            candidates.append(
                _candidate(
                    ResumeImportCandidateType.LANGUAGE,
                    {"name": language, "level": level, "evidence": [block.text]},
                    0.82,
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
            text = _strip_bullet(block.text)
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


def _split_skill_names(text: str) -> list[str]:
    cleaned = _strip_bullet(text)
    cleaned = re.sub(r"\b(e|and)\b", ",", cleaned, flags=re.IGNORECASE)
    pieces = re.split(r"[,;|/]+|\s{2,}", cleaned)
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
        return None, None
    return match.group("start").strip(), match.group("end").strip()


def _skill_level(text: str) -> str | None:
    normalized = normalize_text(text)
    for level in ("basico", "intermediario", "avancado", "fluente"):
        if level in normalized:
            return level
    return None


def _evidence_title(block: ExtractedBlock) -> str:
    text = _strip_bullet(block.text)
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
    return "\n".join(_strip_bullet(block.text) for block in blocks if block.text.strip())


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
