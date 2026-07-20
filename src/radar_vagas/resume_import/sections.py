from __future__ import annotations

from dataclasses import replace

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import ExtractedBlockType
from radar_vagas.resume_import.models import ExtractedBlock, ExtractedDocument

SECTION_ALIASES: dict[str, str] = {
    "objetivo": "summary",
    "objective": "summary",
    "resumo": "summary",
    "sobre": "summary",
    "perfil": "summary",
    "profile": "summary",
    "summary": "summary",
    "experiencia": "experience",
    "experiencias": "experience",
    "experiencia profissional": "experience",
    "historico profissional": "experience",
    "professional experience": "experience",
    "work experience": "experience",
    "projetos": "project",
    "projetos academicos": "project",
    "projetos pessoais": "project",
    "projects": "project",
    "formacao": "education",
    "formacao academica": "education",
    "educacao": "education",
    "education": "education",
    "academic background": "education",
    "habilidades": "skills",
    "competencias": "skills",
    "habilidades e competencias": "skills",
    "conhecimentos": "skills",
    "tecnologias": "skills",
    "skills": "skills",
    "competencies": "skills",
    "technical skills": "skills",
    "idiomas": "languages",
    "linguas": "languages",
    "languages": "languages",
    "cursos": "education",
    "courses": "education",
    "certificacoes": "education",
    "certificados": "education",
    "certifications": "education",
}


def assign_sections(document: ExtractedDocument) -> ExtractedDocument:
    current_section: str | None = None
    blocks: list[ExtractedBlock] = []
    for block in document.blocks:
        text = block.heading or block.text
        heading_section = section_for_text(text)
        if heading_section and (
            block.block_type == ExtractedBlockType.HEADING or is_section_heading(text)
        ):
            current_section = heading_section
            blocks.append(
                replace(
                    block,
                    block_type=ExtractedBlockType.HEADING,
                    heading=section_heading_label(text),
                    section_hint=current_section,
                )
            )
            continue
        blocks.append(
            replace(block, section_hint=block.section_hint or heading_section or current_section)
        )
    return replace(document, blocks=tuple(blocks))


def section_for_text(text: str) -> str | None:
    normalized = normalize_text(text).strip()
    heading = normalized.rstrip(":").strip()
    if heading in SECTION_ALIASES:
        return SECTION_ALIASES[heading]
    for alias, section in SECTION_ALIASES.items():
        if normalized.startswith(f"{alias}:"):
            return section
    return None


def is_section_heading(text: str) -> bool:
    return normalize_text(text).strip().rstrip(":").strip() in SECTION_ALIASES


def section_heading_label(text: str) -> str:
    cleaned = text.strip()
    if ":" in cleaned and not is_section_heading(cleaned):
        cleaned = cleaned.split(":", 1)[0]
    return cleaned.rstrip(":").strip()


def strip_section_prefix(text: str) -> str:
    normalized = normalize_text(text).strip()
    for alias in SECTION_ALIASES:
        prefix = f"{alias}:"
        if normalized.startswith(prefix):
            original = text.split(":", 1)
            if len(original) == 2:
                return original[1].strip()
    return text.strip()


def _section_for_text(text: str) -> str | None:
    normalized = normalize_text(text).strip(":")
    if normalized in SECTION_ALIASES:
        return SECTION_ALIASES[normalized]
    for alias, section in SECTION_ALIASES.items():
        if normalized.startswith(f"{alias}:"):
            return section
    return None
