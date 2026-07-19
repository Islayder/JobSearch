from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar_vagas.applications.review import (
    add_application_event,
    application_key_for_job,
    exact_job_by_provider_identity,
    exact_job_by_url,
    mark_applied,
    probable_jobs_by_company_title,
)
from radar_vagas.canonicalization.normalize import normalize_url
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationMatchKind,
    ApplicationMatchStatus,
    ApplicationStatus,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.persistence.models import (
    Application,
    ApplicationEvent,
    ApplicationMatch,
    Job,
)

REQUIRED_COLUMNS = {
    "provider_identity_key",
    "application_url",
    "company",
    "title",
    "platform",
    "applied_at",
    "status",
    "external_reference",
    "notes",
}

MISSING_IDENTITY_ERROR = (
    "informe provider_identity_key, application_url, external_reference ou empresa+titulo"
)
SUPPORTED_IMPORT_STATUSES = {
    ApplicationStatus.SUBMITTED,
    ApplicationStatus.TEST,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.REJECTED,
    ApplicationStatus.OFFER,
    ApplicationStatus.WITHDRAWN,
}


@dataclass(frozen=True)
class ApplicationHistoryRow:
    index: int
    provider_identity_key: str | None
    application_url: str | None
    company: str | None
    title: str | None
    platform: str | None
    applied_at: str | None
    status: ApplicationStatus
    external_reference: str | None
    notes: str | None


@dataclass(frozen=True)
class ApplicationHistoryItemResult:
    index: int
    status: str
    match_kind: ApplicationMatchKind | None = None
    confidence: float | None = None
    job_id: int | None = None
    application_id: int | None = None
    errors: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApplicationHistoryImportResult:
    dry_run: bool
    total: int
    valid: int
    invalid: int
    linked: int
    probable: int
    unmatched: int
    conflicts: int
    created_applications: int
    updated_applications: int
    unchanged: int
    needs_review: int
    created_matches: int
    items: list[ApplicationHistoryItemResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "summary": {
                "total": self.total,
                "valid": self.valid,
                "invalid": self.invalid,
                "linked": self.linked,
                "probable": self.probable,
                "unmatched": self.unmatched,
                "conflicts": self.conflicts,
                "created_applications": self.created_applications,
                "updated_applications": self.updated_applications,
                "unchanged": self.unchanged,
                "needs_review": self.needs_review,
                "created_matches": self.created_matches,
            },
            "items": [
                {
                    "index": item.index,
                    "status": item.status,
                    "match_kind": item.match_kind.value if item.match_kind else None,
                    "confidence": item.confidence,
                    "job_id": item.job_id,
                    "application_id": item.application_id,
                    "errors": item.errors,
                    "evidence": item.evidence,
                }
                for item in self.items
            ],
        }


def validate_application_history_file(
    file_path: Path,
    *,
    delimiter: str | None = None,
    allow_probable_matches: bool = False,
) -> ApplicationHistoryImportResult:
    rows, invalid = _parse_history_rows(file_path, delimiter=delimiter)
    items = [
        ApplicationHistoryItemResult(index=row.index, status="valid")
        for row in rows
        if _has_useful_identity(row)
    ]
    missing_identity = [
        ApplicationHistoryItemResult(
            index=row.index,
            status="invalid",
            errors=[MISSING_IDENTITY_ERROR],
        )
        for row in rows
        if not _has_useful_identity(row)
    ]
    all_items = [*items, *missing_identity, *invalid]
    _ = allow_probable_matches
    return _result_from_items(
        dry_run=True,
        items=all_items,
        created=0,
        updated=0,
        created_matches=0,
    )


def import_application_history(
    session: Session,
    settings: Settings,
    file_path: Path,
    *,
    dry_run: bool,
    delimiter: str | None = None,
    allow_probable_matches: bool = False,
) -> ApplicationHistoryImportResult:
    rows, invalid = _parse_history_rows(file_path, delimiter=delimiter)
    items: list[ApplicationHistoryItemResult] = [*invalid]
    created = 0
    updated = 0
    created_matches = 0
    seen_rows: set[str] = set()
    for row in rows:
        if not _has_useful_identity(row):
            items.append(
                ApplicationHistoryItemResult(
                    index=row.index,
                    status="invalid",
                    errors=[MISSING_IDENTITY_ERROR],
                )
            )
            continue
        row_fingerprint = _row_fingerprint(row)
        if row_fingerprint in seen_rows:
            items.append(
                ApplicationHistoryItemResult(
                    index=row.index,
                    status="unchanged",
                    errors=[],
                    evidence={"deduplicated": True, "reason": "linha semantica repetida"},
                )
            )
            continue
        seen_rows.add(row_fingerprint)
        match = _match_history_row(session, row)
        if match.match_kind is ApplicationMatchKind.EXACT:
            application = None
            status = "linked" if dry_run else "unchanged"
            if not dry_run and match.job_id is not None:
                application, outcome = _upsert_application_from_history(
                    session,
                    settings,
                    row,
                    match.job_id,
                    row_fingerprint=row_fingerprint,
                )
                created += 1 if outcome == "created" else 0
                updated += 1 if outcome == "updated" else 0
                match_created = _record_match(
                    session,
                    row,
                    match,
                    application.id,
                    row_fingerprint=row_fingerprint,
                    application_key=application.application_key,
                )
                created_matches += 1 if match_created else 0
                if outcome == "unchanged" and match_created:
                    status = "updated"
                    updated += 1
                else:
                    status = outcome
            items.append(
                ApplicationHistoryItemResult(
                    index=row.index,
                    status=status,
                    match_kind=match.match_kind,
                    confidence=match.confidence,
                    job_id=match.job_id,
                    application_id=application.id if application is not None else None,
                    evidence=match.evidence,
                )
            )
            continue
        if match.match_kind is ApplicationMatchKind.PROBABLE:
            if allow_probable_matches and match.job_id is not None and not dry_run:
                application, outcome = _upsert_application_from_history(
                    session,
                    settings,
                    row,
                    match.job_id,
                    row_fingerprint=row_fingerprint,
                )
                created += 1 if outcome == "created" else 0
                updated += 1 if outcome == "updated" else 0
                match_created = _record_match(
                    session,
                    row,
                    match,
                    application.id,
                    row_fingerprint=row_fingerprint,
                    application_key=application.application_key,
                )
                created_matches += 1 if match_created else 0
                status = "updated" if outcome == "unchanged" and match_created else outcome
                updated += 1 if outcome == "unchanged" and match_created else 0
                application_id = application.id
            else:
                match_created = False
                if not dry_run:
                    match_created = _record_match(
                        session,
                        row,
                        match,
                        None,
                        row_fingerprint=row_fingerprint,
                        application_key=None,
                    )
                    created_matches += 1 if match_created else 0
                status = "needs_review" if dry_run or match_created else "unchanged"
                application_id = None
            items.append(
                ApplicationHistoryItemResult(
                    index=row.index,
                    status=status,
                    match_kind=match.match_kind,
                    confidence=match.confidence,
                    job_id=match.job_id,
                    application_id=application_id,
                    evidence=match.evidence,
                )
            )
            continue
        match_created = False
        if not dry_run:
            match_created = _record_match(
                session,
                row,
                match,
                None,
                row_fingerprint=row_fingerprint,
                application_key=None,
            )
            created_matches += 1 if match_created else 0
        items.append(
            ApplicationHistoryItemResult(
                index=row.index,
                status="needs_review" if dry_run or match_created else "unchanged",
                match_kind=match.match_kind,
                confidence=match.confidence,
                job_id=match.job_id,
                evidence=match.evidence,
            )
        )
    return _result_from_items(
        dry_run=dry_run,
        items=items,
        created=created,
        updated=updated,
        created_matches=created_matches,
    )


def write_application_history_report(
    result: ApplicationHistoryImportResult,
    report_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class _MatchResult:
    match_kind: ApplicationMatchKind
    confidence: float
    evidence: dict[str, Any]
    job_id: int | None = None


def _parse_history_rows(
    file_path: Path,
    *,
    delimiter: str | None,
) -> tuple[list[ApplicationHistoryRow], list[ApplicationHistoryItemResult]]:
    if not file_path.exists():
        raise RadarError(f"Arquivo nao encontrado: {file_path}")
    suffix = file_path.suffix.lower()
    raw_rows = _read_json(file_path) if suffix == ".json" else _read_csv(file_path, delimiter)
    rows: list[ApplicationHistoryRow] = []
    invalid: list[ApplicationHistoryItemResult] = []
    for index, raw in enumerate(raw_rows, start=1):
        try:
            rows.append(_row_from_mapping(index, raw))
        except ValueError as exc:
            invalid.append(
                ApplicationHistoryItemResult(
                    index=index,
                    status="invalid",
                    errors=[str(exc)],
                )
            )
    return rows, invalid


def _read_json(file_path: Path) -> list[dict[str, Any]]:
    data = json.loads(file_path.read_text(encoding="utf-8"))
    rows = data.get("applications") or data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RadarError("JSON deve conter uma lista de objetos ou chave applications/items.")
    return list(rows)


def _read_csv(file_path: Path, delimiter: str | None) -> list[dict[str, Any]]:
    with file_path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file, delimiter=delimiter or ",")
        if reader.fieldnames is None:
            raise RadarError("CSV sem cabecalho.")
        return [dict(row) for row in reader]


def _row_from_mapping(index: int, raw: dict[str, Any]) -> ApplicationHistoryRow:
    normalized = {str(key): value for key, value in raw.items()}
    unknown = sorted(set(normalized) - REQUIRED_COLUMNS)
    if unknown:
        raise ValueError(f"campos desconhecidos: {', '.join(unknown)}")
    status_raw = _text(normalized.get("status")) or "SUBMITTED"
    try:
        status = parse_enum_value(ApplicationStatus, status_raw)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if status not in SUPPORTED_IMPORT_STATUSES:
        accepted = ", ".join(sorted(status.value for status in SUPPORTED_IMPORT_STATUSES))
        raise ValueError(
            f"status de importacao nao suportado: {status.value}. Valores aceitos: {accepted}."
        )
    applied_at = _text(normalized.get("applied_at"))
    if applied_at:
        _parse_datetime(applied_at)
    return ApplicationHistoryRow(
        index=index,
        provider_identity_key=_text(normalized.get("provider_identity_key")),
        application_url=normalize_url(_text(normalized.get("application_url"))) or None,
        company=_text(normalized.get("company")),
        title=_text(normalized.get("title")),
        platform=_text(normalized.get("platform")),
        applied_at=applied_at,
        status=status,
        external_reference=_text(normalized.get("external_reference")),
        notes=_text(normalized.get("notes")),
    )


def _match_history_row(session: Session, row: ApplicationHistoryRow) -> _MatchResult:
    exact = exact_job_by_provider_identity(session, row.provider_identity_key)
    if exact is not None:
        return _match(ApplicationMatchKind.EXACT, 1.0, exact.id, "provider_identity_key", row)
    exact = exact_job_by_url(session, row.application_url)
    if exact is not None:
        return _match(ApplicationMatchKind.EXACT, 0.98, exact.id, "application_url", row)
    if row.external_reference:
        apps = session.scalars(
            select(Application).where(Application.external_reference == row.external_reference)
        ).all()
        job_ids = {application.job_id for application in apps}
        if len(job_ids) == 1:
            return _match(
                ApplicationMatchKind.EXACT,
                0.95,
                next(iter(job_ids)),
                "external_reference",
                row,
            )
        if len(job_ids) > 1:
            return _match(ApplicationMatchKind.CONFLICT, 0.0, None, "external_reference", row)
    probable = probable_jobs_by_company_title(session, company=row.company, title=row.title)
    if len(probable) == 1:
        return _match(ApplicationMatchKind.PROBABLE, 0.72, probable[0].id, "company_title", row)
    if len(probable) > 1:
        return _match(ApplicationMatchKind.CONFLICT, 0.0, None, "company_title", row)
    return _match(ApplicationMatchKind.UNMATCHED, 0.0, None, "no_match", row)


def _upsert_application_from_history(
    session: Session,
    settings: Settings,
    row: ApplicationHistoryRow,
    job_id: int,
    *,
    row_fingerprint: str,
) -> tuple[Application, str]:
    job = session.get(Job, job_id)
    if job is None:
        raise RadarError(f"Vaga nao encontrada: {job_id}")
    key = application_key_for_job(
        job,
        external_reference=row.external_reference,
        application_url=row.application_url,
    )
    application = session.scalar(select(Application).where(Application.application_key == key))
    created = application is None
    if application is None:
        application = mark_applied(
            session,
            settings,
            job_id,
            applied_at=_parse_datetime(row.applied_at) if row.applied_at else None,
            platform=row.platform,
            external_reference=row.external_reference,
            notes=row.notes,
            application_url=row.application_url,
            source="history_import",
            submitted_event_key=_event_key_for_row(row, key, ApplicationEventType.SUBMITTED),
        )
    else:
        changed = _update_application_fields(application, row)
        if changed:
            application.updated_at = utc_now()
    if row.status is not ApplicationStatus.SUBMITTED:
        event_type = _event_type_for_status(row.status)
        if event_type is not None:
            event_key = _event_key_for_row(row, key, event_type)
            had_event = _application_event_exists(session, application.id, event_key)
            add_application_event(
                session,
                application.id,
                event_type=event_type,
                occurred_at=_parse_datetime(row.applied_at) if row.applied_at else None,
                notes=row.notes,
                source="history_import",
                event_key=event_key,
            )
            changed = changed or not had_event if not created else not had_event
    if created:
        return application, "created"
    return application, "updated" if changed else "unchanged"


def _record_match(
    session: Session,
    row: ApplicationHistoryRow,
    match: _MatchResult,
    application_id: int | None,
    *,
    row_fingerprint: str,
    application_key: str | None,
) -> bool:
    fingerprint = _match_fingerprint(
        row_fingerprint=row_fingerprint,
        match=match,
        application_key=application_key,
    )
    existing = session.scalar(
        select(ApplicationMatch).where(ApplicationMatch.fingerprint == fingerprint)
    )
    if existing is not None:
        if application_id is not None and existing.application_id is None:
            existing.application_id = application_id
            existing.status = ApplicationMatchStatus.LINKED
            return True
        return False
    status = (
        ApplicationMatchStatus.LINKED
        if application_id is not None
        else ApplicationMatchStatus.NEEDS_REVIEW
    )
    session.add(
        ApplicationMatch(
            application_id=application_id,
            job_id=match.job_id,
            match_kind=match.match_kind,
            confidence=match.confidence,
            fingerprint=fingerprint,
            evidence_json=json.dumps(match.evidence, ensure_ascii=False, sort_keys=True),
            status=status,
            created_at=utc_now(),
        )
    )
    return True


def _event_type_for_status(status: ApplicationStatus) -> ApplicationEventType | None:
    if status is ApplicationStatus.SUBMITTED:
        return ApplicationEventType.SUBMITTED
    if status is ApplicationStatus.TEST:
        return ApplicationEventType.ASSESSMENT_INVITED
    if status is ApplicationStatus.INTERVIEW:
        return ApplicationEventType.INTERVIEW_INVITED
    if status is ApplicationStatus.REJECTED:
        return ApplicationEventType.REJECTED
    if status is ApplicationStatus.OFFER:
        return ApplicationEventType.OFFER_RECEIVED
    if status is ApplicationStatus.WITHDRAWN:
        return ApplicationEventType.WITHDRAWN
    return None


def _result_from_items(
    *,
    dry_run: bool,
    items: list[ApplicationHistoryItemResult],
    created: int,
    updated: int,
    created_matches: int,
) -> ApplicationHistoryImportResult:
    valid = sum(1 for item in items if item.status != "invalid")
    return ApplicationHistoryImportResult(
        dry_run=dry_run,
        total=len(items),
        valid=valid,
        invalid=len(items) - valid,
        linked=sum(
            1
            for item in items
            if item.application_id is not None or item.status in {"linked", "created", "updated"}
        ),
        probable=sum(1 for item in items if item.match_kind is ApplicationMatchKind.PROBABLE),
        unmatched=sum(1 for item in items if item.match_kind is ApplicationMatchKind.UNMATCHED),
        conflicts=sum(1 for item in items if item.match_kind is ApplicationMatchKind.CONFLICT),
        created_applications=created,
        updated_applications=updated,
        unchanged=sum(1 for item in items if item.status == "unchanged"),
        needs_review=sum(1 for item in items if item.status == "needs_review"),
        created_matches=created_matches,
        items=items,
    )


def _update_application_fields(application: Application, row: ApplicationHistoryRow) -> bool:
    changed = False
    updates = {
        "platform": row.platform,
        "external_reference": row.external_reference,
        "application_url": row.application_url,
        "notes": row.notes,
    }
    for field_name, value in updates.items():
        if value is not None and getattr(application, field_name) != value:
            setattr(application, field_name, value)
            changed = True
    return changed


def _application_event_exists(session: Session, application_id: int, event_key: str) -> bool:
    return (
        session.scalar(
            select(ApplicationEvent.id).where(
                ApplicationEvent.application_id == application_id,
                ApplicationEvent.event_key == event_key,
            )
        )
        is not None
    )


def _row_fingerprint(row: ApplicationHistoryRow) -> str:
    return _stable_hash(
        {
            "provider_identity_key": row.provider_identity_key,
            "application_url": row.application_url,
            "company": row.company,
            "title": row.title,
            "platform": row.platform,
            "applied_at": _canonical_datetime_text(row.applied_at),
            "status": row.status.value,
            "external_reference": row.external_reference,
            "notes": row.notes,
        }
    )


def _event_key_for_row(
    row: ApplicationHistoryRow,
    application_key: str,
    event_type: ApplicationEventType,
) -> str:
    return "history:event:" + _stable_hash(
        {
            "source": "history_import",
            "row": _row_fingerprint(row),
            "application_key": application_key,
            "event_type": event_type.value,
            "occurred_at": _canonical_datetime_text(row.applied_at),
        }
    )


def _match_fingerprint(
    *,
    row_fingerprint: str,
    match: _MatchResult,
    application_key: str | None,
) -> str:
    return _stable_hash(
        {
            "source": "history_import",
            "row": row_fingerprint,
            "match_kind": match.match_kind.value,
            "job_id": match.job_id,
            "application_key": application_key,
            "method": match.evidence.get("method"),
        }
    )


def _stable_hash(payload: dict[str, Any]) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _canonical_datetime_text(value: str | None) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _match(
    kind: ApplicationMatchKind,
    confidence: float,
    job_id: int | None,
    method: str,
    row: ApplicationHistoryRow,
) -> _MatchResult:
    return _MatchResult(
        match_kind=kind,
        confidence=confidence,
        job_id=job_id,
        evidence={
            "method": method,
            "provider_identity_key_present": bool(row.provider_identity_key),
            "application_url_present": bool(row.application_url),
            "external_reference_present": bool(row.external_reference),
            "company_present": bool(row.company),
            "title_present": bool(row.title),
        },
    )


def _has_useful_identity(row: ApplicationHistoryRow) -> bool:
    return bool(
        row.provider_identity_key
        or row.application_url
        or row.external_reference
        or (row.company and row.title)
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"data invalida: {value}") from exc
