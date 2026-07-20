from __future__ import annotations

import re

from radar_vagas.domain.enums import ExtractedBlockType
from radar_vagas.domain.errors import RadarError
from radar_vagas.resume_import.models import ExtractedBlock, ExtractedDocument
from radar_vagas.resume_import.security import MAX_EXTRACTED_TEXT_CHARS, MIN_TEXT_CHARS


def extract_text_document(content: bytes, *, source_format: str) -> ExtractedDocument:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RadarError("Arquivo de texto deve estar em UTF-8.") from exc
    if "\x00" in text:
        raise RadarError("Arquivo de texto invalido para importacao de curriculo.")
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        raise RadarError("Texto extraido grande demais para importacao local segura.")

    blocks: list[ExtractedBlock] = []
    order = 0
    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue
        block_type = _block_type(line, source_format=source_format)
        heading = _heading_text(line, block_type)
        blocks.append(
            ExtractedBlock(
                block_id=f"t{order + 1}",
                order=order,
                text=_strip_markdown_marker(line),
                page_number=None,
                block_type=block_type,
                heading=heading,
            )
        )
        order += 1

    character_count = sum(len(block.text) for block in blocks)
    if character_count < MIN_TEXT_CHARS:
        raise RadarError("O arquivo tem pouca informacao para montar um perfil revisavel.")
    return ExtractedDocument(
        blocks=tuple(blocks),
        warnings=(),
        page_count=1,
        source_format=source_format,
        extracted_character_count=character_count,
        quality="GOOD",
        extraction_mode="text",
    )


def _block_type(line: str, *, source_format: str) -> ExtractedBlockType:
    if source_format == "markdown" and re.match(r"^#{1,4}\s+\S", line):
        return ExtractedBlockType.HEADING
    if line.endswith(":") and len(line) <= 80:
        return ExtractedBlockType.HEADING
    if re.match(r"^[-*•]\s+", line) or re.match(r"^\d+[.)]\s+", line):
        return ExtractedBlockType.LIST_ITEM
    return ExtractedBlockType.PARAGRAPH


def _heading_text(line: str, block_type: ExtractedBlockType) -> str | None:
    if block_type != ExtractedBlockType.HEADING:
        return None
    return _strip_markdown_marker(line).rstrip(":").strip()


def _strip_markdown_marker(line: str) -> str:
    return re.sub(r"^#{1,4}\s+", "", line).strip()


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()
