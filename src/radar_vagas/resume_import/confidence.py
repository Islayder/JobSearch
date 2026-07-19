from __future__ import annotations

from radar_vagas.domain.enums import ResumeImportConfidenceLabel


def confidence_label(score: float) -> ResumeImportConfidenceLabel:
    if score >= 0.82:
        return ResumeImportConfidenceLabel.HIGH
    if score >= 0.55:
        return ResumeImportConfidenceLabel.MEDIUM
    return ResumeImportConfidenceLabel.LOW
