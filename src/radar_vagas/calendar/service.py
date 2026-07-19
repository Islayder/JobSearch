from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import (
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.persistence.models import Application, CareerEvent, CareerEventAudit, Job

_UNSET: Any = object()
TERMINAL_EVENT_STATUSES = {
    CareerEventConfirmationStatus.DISMISSED,
    CareerEventConfirmationStatus.COMPLETED,
    CareerEventConfirmationStatus.CANCELLED,
}
VALID_CONFIRMATION_TRANSITIONS = {
    CareerEventConfirmationStatus.SUGGESTED: {
        CareerEventConfirmationStatus.CONFIRMED,
        CareerEventConfirmationStatus.DISMISSED,
        CareerEventConfirmationStatus.CANCELLED,
    },
    CareerEventConfirmationStatus.CONFIRMED: {
        CareerEventConfirmationStatus.COMPLETED,
        CareerEventConfirmationStatus.CANCELLED,
    },
    CareerEventConfirmationStatus.DISMISSED: set(),
    CareerEventConfirmationStatus.COMPLETED: set(),
    CareerEventConfirmationStatus.CANCELLED: set(),
}


def create_event(
    session: Session,
    *,
    event_type: CareerEventType,
    title: str,
    job_id: int | None = None,
    application_id: int | None = None,
    event_key: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    all_day: bool = False,
    timezone: str = "UTC",
    source: CareerEventSource = CareerEventSource.MANUAL,
    confidence: float | None = None,
    confirmation_status: CareerEventConfirmationStatus | None = None,
    location: str | None = None,
    meeting_url: str | None = None,
    notes: str | None = None,
) -> CareerEvent:
    job_id = _validated_job_id(session, job_id=job_id, application_id=application_id)
    status = confirmation_status or _default_confirmation_status(source)
    _validate_initial_confirmation_status(source, status)
    starts_at_utc = _to_utc(starts_at, field_name="starts_at")
    ends_at_utc = _to_utc(ends_at, field_name="ends_at")
    _validate_time_range(starts_at_utc, ends_at_utc)
    timezone = _validate_timezone(timezone)
    meeting_url = _validate_meeting_url(meeting_url)
    _validate_confidence(confidence)
    title = _required_text(title, "title")
    if event_key is not None:
        existing = session.scalar(select(CareerEvent).where(CareerEvent.event_key == event_key))
        if existing is not None:
            _raise_if_event_key_conflicts(
                existing,
                event_type=event_type,
                title=title,
                job_id=job_id,
                application_id=application_id,
                starts_at=starts_at_utc,
                ends_at=ends_at_utc,
                all_day=all_day,
                timezone=timezone,
                source=source,
            )
            return existing
    event = CareerEvent(
        job_id=job_id,
        application_id=application_id,
        event_key=event_key,
        event_type=event_type,
        title=title,
        starts_at=starts_at_utc,
        ends_at=ends_at_utc,
        all_day=all_day,
        timezone=timezone,
        source=source,
        confidence=confidence,
        confirmation_status=status,
        location=_optional_text(location),
        meeting_url=meeting_url,
        notes=_optional_text(notes),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(event)
    session.flush()
    audit_event(session, event, "created", previous=None, new=_snapshot(event), source="system")
    return event


def update_event(
    session: Session,
    event_id: int,
    *,
    title: str | None = None,
    starts_at: datetime | None | Any = _UNSET,
    ends_at: datetime | None | Any = _UNSET,
    all_day: bool | None = None,
    timezone: str | None = None,
    location: str | None | Any = _UNSET,
    meeting_url: str | None | Any = _UNSET,
    notes: str | None | Any = _UNSET,
    confidence: float | None | Any = _UNSET,
    source: str = "manual",
) -> CareerEvent:
    event = get_event(session, event_id)
    if event.confirmation_status in TERMINAL_EVENT_STATUSES and _terminal_edit_requested(
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        timezone=timezone,
        location=location,
        meeting_url=meeting_url,
        notes=notes,
        confidence=confidence,
    ):
        raise RadarError("Evento terminal nao pode ser editado.")
    previous = _snapshot(event)
    if title is not None:
        event.title = _required_text(title, "title")
    if starts_at is not _UNSET:
        event.starts_at = _to_utc(starts_at, field_name="starts_at")
    if ends_at is not _UNSET:
        event.ends_at = _to_utc(ends_at, field_name="ends_at")
    if all_day is not None:
        event.all_day = all_day
    if timezone is not None:
        event.timezone = _validate_timezone(timezone)
    if location is not _UNSET:
        event.location = _optional_text(location)
    if meeting_url is not _UNSET:
        event.meeting_url = _validate_meeting_url(meeting_url)
    if notes is not _UNSET:
        event.notes = _optional_text(notes)
    if confidence is not _UNSET:
        _validate_confidence(confidence)
        event.confidence = confidence
    _validate_time_range(event.starts_at, event.ends_at)
    new = _snapshot(event)
    event.updated_at = utc_now()
    if previous != new:
        audit_event(session, event, "updated", previous=previous, new=new, source=source)
    return event


def confirm_event(session: Session, event_id: int, *, source: str = "manual") -> CareerEvent:
    event = get_event(session, event_id)
    if event.source is CareerEventSource.ESTIMATED:
        raise RadarError("Evento estimado nao pode ser confirmado como compromisso real.")
    return _set_confirmation_status(
        session,
        event,
        CareerEventConfirmationStatus.CONFIRMED,
        action="confirmed",
        source=source,
    )


def dismiss_event(session: Session, event_id: int, *, source: str = "manual") -> CareerEvent:
    event = get_event(session, event_id)
    return _set_confirmation_status(
        session,
        event,
        CareerEventConfirmationStatus.DISMISSED,
        action="dismissed",
        source=source,
    )


def complete_event(session: Session, event_id: int, *, source: str = "manual") -> CareerEvent:
    event = get_event(session, event_id)
    return _set_confirmation_status(
        session,
        event,
        CareerEventConfirmationStatus.COMPLETED,
        action="completed",
        source=source,
    )


def cancel_event(session: Session, event_id: int, *, source: str = "manual") -> CareerEvent:
    event = get_event(session, event_id)
    return _set_confirmation_status(
        session,
        event,
        CareerEventConfirmationStatus.CANCELLED,
        action="cancelled",
        source=source,
    )


def list_upcoming_events(
    session: Session,
    *,
    days: int = 30,
    event_type: CareerEventType | None = None,
    now: datetime | None = None,
) -> list[CareerEvent]:
    if days <= 0:
        raise RadarError("--days deve ser um inteiro positivo.")
    start = _to_utc(now or utc_now(), field_name="now")
    assert start is not None
    end = start + timedelta(days=days)
    statement = (
        select(CareerEvent)
        .where(CareerEvent.starts_at.is_not(None))
        .where(CareerEvent.starts_at >= start)
        .where(CareerEvent.starts_at <= end)
        .where(CareerEvent.confirmation_status.not_in(TERMINAL_EVENT_STATUSES))
        .order_by(CareerEvent.starts_at.asc(), CareerEvent.id.asc())
    )
    if event_type is not None:
        statement = statement.where(CareerEvent.event_type == event_type)
    return list(session.scalars(statement).all())


def list_events_by_job(session: Session, job_id: int) -> list[CareerEvent]:
    return list(
        session.scalars(
            select(CareerEvent)
            .where(CareerEvent.job_id == job_id)
            .order_by(CareerEvent.starts_at.asc().nullslast(), CareerEvent.id.asc())
        ).all()
    )


def list_events_by_application(session: Session, application_id: int) -> list[CareerEvent]:
    return list(
        session.scalars(
            select(CareerEvent)
            .where(CareerEvent.application_id == application_id)
            .order_by(CareerEvent.starts_at.asc().nullslast(), CareerEvent.id.asc())
        ).all()
    )


def get_event(session: Session, event_id: int) -> CareerEvent:
    event = session.get(CareerEvent, event_id)
    if event is None:
        raise RadarError(f"Evento de agenda nao encontrado: {event_id}")
    return event


def audit_event(
    session: Session,
    event: CareerEvent,
    action: str,
    *,
    previous: dict[str, Any] | None,
    new: dict[str, Any] | None,
    source: str,
) -> CareerEventAudit:
    audit = CareerEventAudit(
        event_id=event.id,
        action=action,
        previous_values_json=(
            json.dumps(previous, ensure_ascii=False, sort_keys=True) if previous else None
        ),
        new_values_json=json.dumps(new, ensure_ascii=False, sort_keys=True) if new else None,
        source=source,
        occurred_at=utc_now(),
        created_at=utc_now(),
    )
    session.add(audit)
    return audit


def _set_confirmation_status(
    session: Session,
    event: CareerEvent,
    status: CareerEventConfirmationStatus,
    *,
    action: str,
    source: str,
) -> CareerEvent:
    previous = _snapshot(event)
    if event.confirmation_status is status:
        return event
    allowed = VALID_CONFIRMATION_TRANSITIONS[event.confirmation_status]
    if status not in allowed:
        raise RadarError(
            f"Transicao de agenda invalida: {event.confirmation_status.value} -> {status.value}."
        )
    event.confirmation_status = status
    if status is CareerEventConfirmationStatus.COMPLETED:
        event.completed_at = utc_now()
        event.cancelled_at = None
    elif status is CareerEventConfirmationStatus.CANCELLED:
        event.cancelled_at = utc_now()
        event.completed_at = None
    else:
        event.completed_at = None
        event.cancelled_at = None
    event.updated_at = utc_now()
    audit_event(session, event, action, previous=previous, new=_snapshot(event), source=source)
    return event


def _validated_job_id(
    session: Session,
    *,
    job_id: int | None,
    application_id: int | None,
) -> int | None:
    if job_id is not None and session.get(Job, job_id) is None:
        raise RadarError(f"Vaga nao encontrada: {job_id}")
    if application_id is None:
        return job_id
    application = session.get(Application, application_id)
    if application is None:
        raise RadarError(f"Candidatura nao encontrada: {application_id}")
    if job_id is not None and application.job_id != job_id:
        raise RadarError("Evento ligado a candidatura deve pertencer a mesma vaga.")
    return application.job_id


def _default_confirmation_status(
    source: CareerEventSource,
) -> CareerEventConfirmationStatus:
    return (
        CareerEventConfirmationStatus.CONFIRMED
        if source is CareerEventSource.MANUAL
        else CareerEventConfirmationStatus.SUGGESTED
    )


def _validate_initial_confirmation_status(
    source: CareerEventSource,
    status: CareerEventConfirmationStatus,
) -> None:
    if status in TERMINAL_EVENT_STATUSES:
        raise RadarError("Evento nao pode nascer em estado terminal.")
    if source is CareerEventSource.ESTIMATED and status is CareerEventConfirmationStatus.CONFIRMED:
        raise RadarError("Evento estimado nao pode ser confirmado.")
    if source is not CareerEventSource.MANUAL and status is CareerEventConfirmationStatus.CONFIRMED:
        raise RadarError("Evento nao manual deve iniciar como sugerido.")


def _to_utc(value: datetime | None, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise RadarError(f"{field_name} deve conter timezone.")
    return value.astimezone(UTC)


def _validate_time_range(
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> None:
    if starts_at is not None and ends_at is not None and ends_at < starts_at:
        raise RadarError("ends_at nao pode ser anterior a starts_at.")


def _terminal_edit_requested(
    *,
    title: str | None,
    starts_at: datetime | None | Any,
    ends_at: datetime | None | Any,
    all_day: bool | None,
    timezone: str | None,
    location: str | None | Any,
    meeting_url: str | None | Any,
    notes: str | None | Any,
    confidence: float | None | Any,
) -> bool:
    return any(
        [
            title is not None,
            starts_at is not _UNSET,
            ends_at is not _UNSET,
            all_day is not None,
            timezone is not None,
            location is not _UNSET,
            meeting_url is not _UNSET,
            notes is not _UNSET,
            confidence is not _UNSET,
        ]
    )


def _validate_timezone(value: str) -> str:
    timezone = _required_text(value, "timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RadarError(f"timezone invalido: {timezone}") from exc
    return timezone


def _validate_meeting_url(value: str | None) -> str | None:
    url = _optional_text(value)
    if url is None:
        return None
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise RadarError("meeting_url aceita somente http ou https.")
    if not parts.hostname:
        raise RadarError("meeting_url deve ter host.")
    if parts.username or parts.password:
        raise RadarError("meeting_url nao pode conter credenciais.")
    hostname = parts.hostname.strip().strip(".").lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        raise RadarError("meeting_url nao pode apontar para host local.")
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return url
    if not ip.is_global:
        raise RadarError("meeting_url nao pode apontar para endereco privado.")
    return url


def _validate_confidence(value: float | None) -> None:
    if value is not None and not 0 <= value <= 1:
        raise RadarError("confidence deve ficar entre 0 e 1.")


def _snapshot(event: CareerEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type.value,
        "title": event.title,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "all_day": event.all_day,
        "timezone": event.timezone,
        "source": event.source.value,
        "confidence": event.confidence,
        "confirmation_status": event.confirmation_status.value,
        "location": event.location,
        "meeting_url": event.meeting_url,
        "notes": event.notes,
        "completed_at": event.completed_at.isoformat() if event.completed_at else None,
        "cancelled_at": event.cancelled_at.isoformat() if event.cancelled_at else None,
    }


def _raise_if_event_key_conflicts(
    event: CareerEvent,
    *,
    event_type: CareerEventType,
    title: str,
    job_id: int | None,
    application_id: int | None,
    starts_at: datetime | None,
    ends_at: datetime | None,
    all_day: bool,
    timezone: str,
    source: CareerEventSource,
) -> None:
    expected = {
        "event_type": event_type.value,
        "title": normalize_text(title),
        "job_id": job_id,
        "application_id": application_id,
        "starts_at": starts_at.isoformat() if starts_at else None,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "all_day": all_day,
        "timezone": timezone,
        "source": source.value,
    }
    actual = {
        "event_type": event.event_type.value,
        "title": normalize_text(event.title),
        "job_id": event.job_id,
        "application_id": event.application_id,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "all_day": event.all_day,
        "timezone": event.timezone,
        "source": event.source.value,
    }
    if actual != expected:
        raise RadarError("event_key ja existe para outro evento de agenda.")


def _required_text(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise RadarError(f"{field_name} nao pode ficar vazio.")
    return text


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None
