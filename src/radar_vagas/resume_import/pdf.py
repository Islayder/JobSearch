from __future__ import annotations

import re
from io import BytesIO
from typing import Any

from radar_vagas.domain.enums import ExtractedBlockType
from radar_vagas.domain.errors import RadarError
from radar_vagas.resume_import.models import ExtractedBlock, ExtractedDocument
from radar_vagas.resume_import.security import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_PDF_PAGES,
    MIN_TEXT_CHARS,
)


def extract_pdf_document(content: bytes) -> ExtractedDocument:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RadarError("Instale o extra web para importar curriculos PDF.") from exc

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise RadarError("Nao foi possivel ler este PDF.") from exc

    if reader.is_encrypted:
        raise RadarError("PDF protegido por senha nao pode ser importado.")
    page_count = len(reader.pages)
    if page_count == 0:
        raise RadarError("PDF sem paginas para importar.")
    if page_count > MAX_PDF_PAGES:
        raise RadarError("PDF com mais de 30 paginas nao pode ser importado.")

    warnings: list[str] = []
    blocks: list[ExtractedBlock] = []
    total_chars = 0
    order = 0
    for page_index, page in enumerate(reader.pages, start=1):
        text = _extract_page_text(page)
        if not text.strip():
            warnings.append(f"Pagina {page_index} sem texto extraivel.")
            continue
        for line in _page_lines(text):
            block_type = _line_block_type(line)
            heading = line.rstrip(":") if block_type == ExtractedBlockType.HEADING else None
            blocks.append(
                ExtractedBlock(
                    block_id=f"p{page_index}-b{order + 1}",
                    order=order,
                    text=line,
                    page_number=page_index,
                    block_type=block_type,
                    heading=heading,
                )
            )
            order += 1
            total_chars += len(line)
            if total_chars > MAX_EXTRACTED_TEXT_CHARS:
                raise RadarError("Texto extraido grande demais para importacao local segura.")

    if total_chars < MIN_TEXT_CHARS:
        raise RadarError("Não foi possível encontrar texto suficiente neste PDF.")
    return ExtractedDocument(
        blocks=tuple(blocks),
        warnings=tuple(warnings),
        page_count=page_count,
        source_format="pdf",
        extracted_character_count=total_chars,
        quality="textual",
    )


def _extract_page_text(page: Any) -> str:
    try:
        return str(page.extract_text(extraction_mode="layout") or "")
    except TypeError:
        return str(page.extract_text() or "")
    except Exception:
        return ""


def _page_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def _line_block_type(line: str) -> ExtractedBlockType:
    if re.match(r"^[-*•]\s+", line) or re.match(r"^\d+[.)]\s+", line):
        return ExtractedBlockType.LIST_ITEM
    if line.endswith(":") and len(line) <= 80:
        return ExtractedBlockType.HEADING
    if line.isupper() and len(line) <= 80 and len(line.split()) <= 6:
        return ExtractedBlockType.HEADING
    return ExtractedBlockType.PARAGRAPH
