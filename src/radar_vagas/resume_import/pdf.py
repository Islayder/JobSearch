from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from radar_vagas.domain.enums import ExtractedBlockType
from radar_vagas.domain.errors import RadarError
from radar_vagas.resume_import.models import ExtractedBlock, ExtractedDocument
from radar_vagas.resume_import.sections import is_section_heading, section_heading_label
from radar_vagas.resume_import.security import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_PDF_PAGES,
    MIN_TEXT_CHARS,
)

PDF_EXTRACTION_MODES = ("automatic", "plain", "layout", "geometric")
PDF_EXTRACTION_LABELS = {
    "automatic": "Automatico",
    "plain": "Texto normal",
    "layout": "Layout",
    "geometric": "Geometrico",
}
QUALITY_ORDER = {"UNUSABLE": 0, "DEGRADED": 1, "ACCEPTABLE": 2, "GOOD": 3}
DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{4}|(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.?\s*\d{4}|"
    r"20\d{2}|19\d{2})\b",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9][A-Za-zÀ-ÖØ-öø-ÿ0-9+.#_-]*")
GLUED_PUNCTUATION_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][,;:.][A-Za-zÀ-ÖØ-öø-ÿ]")
CAMEL_GLUE_RE = re.compile(r"[a-zà-ÿ][A-ZÀ-Ý]")
SEPARATOR_RE = re.compile(r"[-\u2013\u2014:|•·○]")


@dataclass(frozen=True)
class PdfTextMetrics:
    character_count: int
    letter_count: int
    word_count: int
    space_ratio: float
    average_token_length: float
    long_token_count: int
    line_count: int
    heading_count: int
    date_count: int
    separator_count: int
    glued_incidence: int
    printable_ratio: float


@dataclass(frozen=True)
class PageExtraction:
    mode: str
    text: str
    metrics: PdfTextMetrics
    quality: str
    score: float
    error: str | None = None


@dataclass(frozen=True)
class TextFragment:
    text: str
    x: float
    y: float
    font_size: float


def extract_pdf_document(
    content: bytes,
    *,
    extraction_mode: str = "automatic",
) -> ExtractedDocument:
    mode = _validate_extraction_mode(extraction_mode)
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RadarError("Instale o extra web para importar curriculos PDF.") from exc

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise RadarError("PDF corrompido ou ilegivel nao pode ser importado.") from exc

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
    selected_pages: list[PageExtraction] = []
    extracted_any_text = False
    for page_index, page in enumerate(reader.pages, start=1):
        selected = _extract_page(page, mode=mode)
        selected_pages.append(selected)
        if selected.text.strip():
            extracted_any_text = True
        if selected.error:
            warnings.append(f"Pagina {page_index}: {selected.error}")
        warnings.append(_strategy_warning(page_index, selected))
        if not selected.text.strip():
            warnings.append(f"Pagina {page_index} sem texto extraivel.")
            continue
        if selected.quality in {"DEGRADED", "UNUSABLE"}:
            warnings.append(
                f"Pagina {page_index}: qualidade {selected.quality}; revise como baixa confianca."
            )
        for line in _page_lines(selected.text):
            block_type = _line_block_type(line)
            heading = (
                section_heading_label(line) if block_type == ExtractedBlockType.HEADING else None
            )
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
        reason = (
            "O PDF parece escaneado ou nao possui texto selecionavel."
            if not extracted_any_text
            else "O PDF textual nao gerou texto suficiente para revisao."
        )
        raise RadarError(f"Não foi possível encontrar texto suficiente neste PDF. {reason}")

    document_quality = _document_quality(selected_pages)
    if document_quality in {"DEGRADED", "UNUSABLE"}:
        warnings.append(
            "O PDF possui texto, mas sua estrutura e seus espacos nao puderam ser "
            "reconstruidos com seguranca. Tente o arquivo DOCX ou use outro PDF."
        )
    if _looks_column_mixed(selected_pages):
        warnings.append(
            "O PDF aparenta ter colunas misturadas; revise a ordem dos trechos antes de confirmar."
        )

    return ExtractedDocument(
        blocks=tuple(blocks),
        warnings=tuple(warnings),
        page_count=page_count,
        source_format="pdf",
        extracted_character_count=total_chars,
        quality=document_quality,
        extraction_mode=mode,
        quality_metrics=_aggregate_metrics(selected_pages),
    )


def _validate_extraction_mode(mode: str) -> str:
    normalized = (mode or "automatic").strip().lower().replace("-", "_")
    aliases = {"auto": "automatic", "normal": "plain", "text": "plain", "geometrico": "geometric"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in PDF_EXTRACTION_MODES:
        allowed = ", ".join(PDF_EXTRACTION_MODES)
        raise RadarError(f"Modo de extracao PDF invalido. Use: {allowed}.")
    return normalized


def _extract_page(page: Any, *, mode: str) -> PageExtraction:
    if mode == "automatic":
        plain = _page_strategy(page, "plain")
        layout = _page_strategy(page, "layout")
        best = _best_page_strategy([plain, layout])
        if QUALITY_ORDER[best.quality] <= QUALITY_ORDER["DEGRADED"]:
            return _best_page_strategy([plain, layout, _page_strategy(page, "geometric")])
        return best
    return _page_strategy(page, mode)


def _page_strategy(page: Any, mode: str) -> PageExtraction:
    error: str | None = None
    try:
        if mode == "plain":
            text = str(page.extract_text() or "")
        elif mode == "layout":
            text = str(page.extract_text(extraction_mode="layout") or "")
        else:
            text = _extract_page_geometric(page)
    except TypeError as exc:
        error = f"estrategia {PDF_EXTRACTION_LABELS[mode]} indisponivel neste PDF."
        text = "" if mode == "geometric" else str(_safe_plain_extract(page))
        if mode != "layout":
            error = f"estrategia {PDF_EXTRACTION_LABELS[mode]} falhou: {exc.__class__.__name__}."
    except Exception as exc:
        error = f"estrategia {PDF_EXTRACTION_LABELS[mode]} falhou: {exc.__class__.__name__}."
        text = ""
    metrics = _text_metrics(text)
    quality = _quality_for_metrics(metrics)
    return PageExtraction(
        mode=mode,
        text=text,
        metrics=metrics,
        quality=quality,
        score=_quality_score(metrics, quality),
        error=error,
    )


def _safe_plain_extract(page: Any) -> str:
    try:
        return str(page.extract_text() or "")
    except Exception:
        return ""


def _extract_page_geometric(page: Any) -> str:
    fragments: list[TextFragment] = []

    def visitor(
        text: str,
        _cm: tuple[float, ...],
        tm: tuple[float, ...],
        _font_dict: object,
        font_size: float,
    ) -> None:
        cleaned = str(text).replace("\r", "\n")
        if not cleaned.strip():
            return
        x = _matrix_value(tm, 4)
        y = _matrix_value(tm, 5)
        safe_font_size = float(font_size or 10.0)
        for line_index, raw_line in enumerate(cleaned.splitlines()):
            line = re.sub(r"\s+", " ", raw_line).strip()
            if line:
                fragments.append(
                    TextFragment(
                        text=line,
                        x=x,
                        y=y - (line_index * safe_font_size * 1.25),
                        font_size=safe_font_size,
                    )
                )

    page.extract_text(visitor_text=visitor)
    if not fragments:
        return ""
    return "\n".join(_reconstruct_lines(fragments))


def _matrix_value(matrix: tuple[float, ...], index: int) -> float:
    try:
        return float(matrix[index])
    except (IndexError, TypeError, ValueError):
        return 0.0


def _reconstruct_lines(fragments: list[TextFragment]) -> list[str]:
    sorted_fragments = sorted(fragments, key=lambda item: (-item.y, item.x))
    line_groups: list[list[TextFragment]] = []
    line_ys: list[float] = []
    for fragment in sorted_fragments:
        tolerance = max(2.5, fragment.font_size * 0.45)
        for index, line_y in enumerate(line_ys):
            if abs(line_y - fragment.y) <= tolerance:
                line_groups[index].append(fragment)
                line_ys[index] = (line_y + fragment.y) / 2
                break
        else:
            line_groups.append([fragment])
            line_ys.append(fragment.y)

    ordered_groups = sorted(zip(line_ys, line_groups, strict=True), key=lambda item: -item[0])
    lines: list[str] = []
    for _line_y, group in ordered_groups:
        lines.extend(_reconstruct_horizontal_line(sorted(group, key=lambda item: item.x)))
    return [line for line in lines if line.strip()]


def _reconstruct_horizontal_line(fragments: list[TextFragment]) -> list[str]:
    lines: list[str] = []
    current = ""
    previous_right: float | None = None
    previous_font = 10.0
    for fragment in fragments:
        text = fragment.text.strip()
        if not text:
            continue
        if previous_right is None:
            current = text
        else:
            gap = fragment.x - previous_right
            if gap > max(72.0, fragment.font_size * 6):
                lines.append(current.strip())
                current = text
            elif gap > max(2.5, min(previous_font, fragment.font_size) * 0.28):
                current = _join_with_space(current, text)
            else:
                current = f"{current}{text}"
        previous_right = fragment.x + _estimated_width(text, fragment.font_size)
        previous_font = fragment.font_size
    if current.strip():
        lines.append(current.strip())
    return lines


def _join_with_space(left: str, right: str) -> str:
    if not left:
        return right
    if left.endswith((" ", "-", "/", "(", "[")) or right.startswith((" ", ",", ".", ";", ":", ")")):
        return f"{left}{right}"
    return f"{left} {right}"


def _estimated_width(text: str, font_size: float) -> float:
    narrow = sum(1 for char in text if char in "ilI.,:;|!")
    wide = len(text) - narrow
    return (wide * font_size * 0.48) + (narrow * font_size * 0.24)


def _best_page_strategy(candidates: list[PageExtraction]) -> PageExtraction:
    return max(
        candidates,
        key=lambda item: (
            QUALITY_ORDER[item.quality],
            item.score,
            item.metrics.word_count,
            item.metrics.character_count,
        ),
    )


def _page_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines


def _line_block_type(line: str) -> ExtractedBlockType:
    if is_section_heading(line):
        return ExtractedBlockType.HEADING
    if re.match(r"^[-*•]\s+", line) or re.match(r"^\d+[.)]\s+", line):
        return ExtractedBlockType.LIST_ITEM
    if line.endswith(":") and len(line) <= 80:
        return ExtractedBlockType.HEADING
    if line.isupper() and len(line) <= 80 and len(line.split()) <= 6:
        return ExtractedBlockType.HEADING
    return ExtractedBlockType.PARAGRAPH


def _text_metrics(text: str) -> PdfTextMetrics:
    stripped = text.strip()
    tokens = TOKEN_RE.findall(stripped)
    token_lengths = [len(token) for token in tokens]
    printable_count = sum(1 for char in stripped if char.isprintable() or char in "\n\t")
    character_count = len(stripped)
    line_count = len(_page_lines(stripped))
    long_token_count = sum(1 for length in token_lengths if length > 25)
    glued_incidence = (
        long_token_count
        + len(GLUED_PUNCTUATION_RE.findall(stripped))
        + len(CAMEL_GLUE_RE.findall(stripped))
    )
    return PdfTextMetrics(
        character_count=character_count,
        letter_count=sum(1 for char in stripped if char.isalpha()),
        word_count=len(tokens),
        space_ratio=stripped.count(" ") / max(character_count, 1),
        average_token_length=sum(token_lengths) / max(len(token_lengths), 1),
        long_token_count=long_token_count,
        line_count=line_count,
        heading_count=sum(1 for line in _page_lines(stripped) if is_section_heading(line)),
        date_count=len(DATE_RE.findall(stripped)),
        separator_count=len(SEPARATOR_RE.findall(stripped)),
        glued_incidence=glued_incidence,
        printable_ratio=printable_count / max(character_count, 1),
    )


def _quality_for_metrics(metrics: PdfTextMetrics) -> str:
    if metrics.character_count < MIN_TEXT_CHARS or metrics.printable_ratio < 0.9:
        return "UNUSABLE"
    long_ratio = metrics.long_token_count / max(metrics.word_count, 1)
    sparse_spacing = metrics.space_ratio < 0.055 and metrics.letter_count >= 120
    weak_structure = (
        metrics.heading_count == 0 and metrics.date_count == 0 and metrics.line_count <= 3
    )
    if sparse_spacing and (long_ratio >= 0.08 or weak_structure):
        return "DEGRADED"
    if metrics.average_token_length > 18 or long_ratio >= 0.18 or metrics.glued_incidence >= 8:
        return "DEGRADED"
    if (
        metrics.word_count >= 55
        and metrics.space_ratio >= 0.1
        and long_ratio <= 0.04
        and metrics.printable_ratio >= 0.98
        and (metrics.heading_count >= 2 or metrics.date_count >= 1)
    ):
        return "GOOD"
    if metrics.word_count >= 12 and metrics.printable_ratio >= 0.97:
        return "ACCEPTABLE"
    return "DEGRADED"


def _quality_score(metrics: PdfTextMetrics, quality: str) -> float:
    score = QUALITY_ORDER[quality] * 100.0
    score += min(metrics.word_count, 220) * 1.4
    score += min(metrics.line_count, 80) * 4
    score += min(metrics.heading_count, 12) * 16
    score += min(metrics.date_count, 12) * 5
    score += min(metrics.separator_count, 80) * 0.5
    score += metrics.printable_ratio * 10
    score -= metrics.long_token_count * 9
    score -= metrics.glued_incidence * 3
    if metrics.space_ratio < 0.055 and metrics.letter_count >= 120:
        score -= 45
    return score


def _document_quality(pages: list[PageExtraction]) -> str:
    qualities = [page.quality for page in pages if page.metrics.character_count >= MIN_TEXT_CHARS]
    if not qualities:
        return "UNUSABLE"
    if any(quality == "DEGRADED" for quality in qualities):
        if not any(quality in {"GOOD", "ACCEPTABLE"} for quality in qualities):
            return "DEGRADED"
        return "ACCEPTABLE"
    if all(quality == "GOOD" for quality in qualities):
        return "GOOD"
    return "ACCEPTABLE"


def _strategy_warning(page_index: int, selected: PageExtraction) -> str:
    metrics = selected.metrics
    return (
        f"Pagina {page_index}: estrategia de PDF escolhida: "
        f"{PDF_EXTRACTION_LABELS[selected.mode]} "
        f"(qualidade {selected.quality}, palavras {metrics.word_count}, "
        f"espacos {metrics.space_ratio:.1%}, tokens longos {metrics.long_token_count}, "
        f"linhas {metrics.line_count})."
    )


def _aggregate_metrics(pages: list[PageExtraction]) -> dict[str, object]:
    total_characters = sum(page.metrics.character_count for page in pages)
    total_words = sum(page.metrics.word_count for page in pages)
    total_long_tokens = sum(page.metrics.long_token_count for page in pages)
    total_lines = sum(page.metrics.line_count for page in pages)
    return {
        "pages": len(pages),
        "characters": total_characters,
        "words": total_words,
        "space_ratio": _weighted_average(
            [(page.metrics.space_ratio, page.metrics.character_count) for page in pages]
        ),
        "average_token_length": _weighted_average(
            [(page.metrics.average_token_length, page.metrics.word_count) for page in pages]
        ),
        "long_token_count": total_long_tokens,
        "lines": total_lines,
        "headings": sum(page.metrics.heading_count for page in pages),
        "dates": sum(page.metrics.date_count for page in pages),
        "separators": sum(page.metrics.separator_count for page in pages),
        "glued_incidence": sum(page.metrics.glued_incidence for page in pages),
        "printable_ratio": _weighted_average(
            [(page.metrics.printable_ratio, page.metrics.character_count) for page in pages]
        ),
        "selected_modes": [page.mode for page in pages],
    }


def _weighted_average(values: list[tuple[float, int]]) -> float:
    total_weight = sum(weight for _value, weight in values)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in values) / total_weight


def _looks_column_mixed(pages: list[PageExtraction]) -> bool:
    return any(page.mode == "geometric" and page.metrics.line_count >= 20 for page in pages)
