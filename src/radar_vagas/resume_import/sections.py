from __future__ import annotations

from dataclasses import replace

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import ExtractedBlockType
from radar_vagas.resume_import.models import ExtractedBlock, ExtractedDocument

SECTION_ALIASES: dict[str, str] = {
    "objetivo": "summary",
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
    "conhecimentos": "skills",
    "tecnologias": "skills",
    "skills": "skills",
    "technical skills": "skills",
    "idiomas": "languages",
    "linguas": "languages",
    "languages": "languages",
    "certificacoes": "education",
    "certificados": "education",
    "certifications": "education",
}


def assign_sections(document: ExtractedDocument) -> ExtractedDocument:
    current_section: str | None = None
    blocks: list[ExtractedBlock] = []
    for block in document.blocks:
        heading_section = _section_for_text(block.heading or block.text)
        if block.block_type == ExtractedBlockType.HEADING and heading_section:
            current_section = heading_section
            blocks.append(replace(block, section_hint=current_section))
            continue
        blocks.append(replace(block, section_hint=block.section_hint or current_section))
    return replace(document, blocks=tuple(blocks))


def _section_for_text(text: str) -> str | None:
    normalized = normalize_text(text).strip(":")
    if normalized in SECTION_ALIASES:
        return SECTION_ALIASES[normalized]
    for alias, section in SECTION_ALIASES.items():
        if normalized.startswith(f"{alias}:"):
            return section
    return None
