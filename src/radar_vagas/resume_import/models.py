from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from radar_vagas.domain.enums import (
    ExtractedBlockType,
    ResumeImportCandidateType,
    ResumeImportConfidenceLabel,
)


@dataclass(frozen=True)
class ExtractedBlock:
    block_id: str
    order: int
    text: str
    page_number: int | None
    block_type: ExtractedBlockType
    heading: str | None = None
    table_index: int | None = None
    row_index: int | None = None
    cell_index: int | None = None
    section_hint: str | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    blocks: tuple[ExtractedBlock, ...]
    warnings: tuple[str, ...]
    page_count: int
    source_format: str
    extracted_character_count: int
    quality: str
    extraction_mode: str | None = None
    quality_metrics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeCandidate:
    candidate_type: ResumeImportCandidateType
    payload: dict[str, Any]
    confidence_score: float
    confidence_label: ResumeImportConfidenceLabel
    explanation: str
    source_reference: str | None
    source_excerpt: str | None
    block_ids: tuple[str, ...] = field(default_factory=tuple)
    page_number: int | None = None
    section_hint: str | None = None
