from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CollectionSummary:
    found: int = 0
    new: int = 0
    unchanged: int = 0
    changed: int = 0
    exact_duplicates: int = 0
    probable_duplicates: int = 0
    eligible: int = 0
    manual_review: int = 0
    ineligible: int = 0
    core: int = 0
    adjacent: int = 0
    unrelated: int = 0
    closed: int = 0
    reopened: int = 0
    invalid_items: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "found": self.found,
            "new": self.new,
            "unchanged": self.unchanged,
            "changed": self.changed,
            "exact_duplicates": self.exact_duplicates,
            "probable_duplicates": self.probable_duplicates,
            "eligible": self.eligible,
            "manual_review": self.manual_review,
            "ineligible": self.ineligible,
            "core": self.core,
            "adjacent": self.adjacent,
            "unrelated": self.unrelated,
            "closed": self.closed,
            "reopened": self.reopened,
            "invalid_items": self.invalid_items,
        }


@dataclass(frozen=True)
class CollectionExecutionReport:
    collector: str
    board: str | None
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    network: dict[str, int]
    summary: CollectionSummary
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "board": self.board,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "dry_run": self.dry_run,
            "network": self.network,
            "summary": self.summary.to_dict(),
            "warnings": self.warnings,
            "errors": self.errors,
            "metadata": self.metadata,
        }


def write_collection_report(report: CollectionExecutionReport, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
