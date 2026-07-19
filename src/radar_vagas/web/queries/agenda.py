from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.domain.enums import (
    ApplicationStatus,
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
    JobStatus,
    ReadableEnum,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.models import Application, CareerEvent, Job


@dataclass(frozen=True)
class AgendaFilters:
    status: CareerEventConfirmationStatus | None = None
    event_type: CareerEventType | None = None
    source: CareerEventSource | None = None
    job_id: int | None = None
    application_id: int | None = None


@dataclass(frozen=True)
class CalendarDay:
    day: date
    in_month: bool
    is_today: bool
    events: list[CareerEvent]


@dataclass(frozen=True)
class AgendaContext:
    year: int
    month: int
    month_label: str
    previous_year: int
    previous_month: int
    next_year: int
    next_month: int
    weeks: list[list[CalendarDay]]
    upcoming_events: list[CareerEvent]
    undated_events: list[CareerEvent]
    job_options: list[Job]
    application_options: list[Application]


def parse_agenda_filters(raw: dict[str, str | None]) -> AgendaFilters:
    return AgendaFilters(
        status=_enum(CareerEventConfirmationStatus, raw.get("status"), "status da agenda"),
        event_type=_enum(CareerEventType, raw.get("event_type"), "tipo de evento"),
        source=_enum(CareerEventSource, raw.get("source"), "origem do evento"),
        job_id=_optional_positive_int(raw.get("job_id"), "vaga"),
        application_id=_optional_positive_int(raw.get("application_id"), "candidatura"),
    )


def agenda_context(
    session: Session,
    *,
    year: int,
    month: int,
    timezone: str,
    filters: AgendaFilters,
) -> AgendaContext:
    year, month = _valid_month(year, month)
    tz = ZoneInfo(timezone)
    events = agenda_events(session, filters=filters)
    today = datetime.now(tz).date()
    month_calendar = calendar.Calendar(firstweekday=0)
    events_by_day: dict[date, list[CareerEvent]] = {}
    undated: list[CareerEvent] = []
    for event in events:
        if event.starts_at is None:
            undated.append(event)
            continue
        local_day = _event_day(event, tz)
        events_by_day.setdefault(local_day, []).append(event)
    weeks = [
        [
            CalendarDay(
                day=day,
                in_month=day.month == month,
                is_today=day == today,
                events=events_by_day.get(day, []),
            )
            for day in week
        ]
        for week in month_calendar.monthdatescalendar(year, month)
    ]
    previous_month = month - 1 or 12
    previous_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year
    return AgendaContext(
        year=year,
        month=month,
        month_label=f"{calendar.month_name[month]} {year}",
        previous_year=previous_year,
        previous_month=previous_month,
        next_year=next_year,
        next_month=next_month,
        weeks=weeks,
        upcoming_events=[event for event in events if event.starts_at is not None],
        undated_events=undated,
        job_options=job_options(session),
        application_options=application_options(session),
    )


def agenda_events(session: Session, *, filters: AgendaFilters) -> list[CareerEvent]:
    statement = (
        select(CareerEvent)
        .options(
            selectinload(CareerEvent.job).selectinload(Job.company),
            selectinload(CareerEvent.application).selectinload(Application.job),
        )
        .order_by(CareerEvent.starts_at.asc().nullslast(), CareerEvent.id.asc())
    )
    statement = _apply_event_filters(statement, filters)
    return list(session.scalars(statement).unique().all())


def job_options(session: Session) -> list[Job]:
    return list(
        session.scalars(
            select(Job)
            .options(selectinload(Job.company))
            .where(Job.status.not_in([JobStatus.CLOSED, JobStatus.EXPIRED]))
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .limit(200)
        )
    )


def application_options(session: Session) -> list[Application]:
    return list(
        session.scalars(
            select(Application)
            .options(selectinload(Application.job).selectinload(Job.company))
            .where(
                Application.status.not_in(
                    [
                        ApplicationStatus.REJECTED,
                        ApplicationStatus.WITHDRAWN,
                        ApplicationStatus.CLOSED,
                    ]
                )
            )
            .order_by(Application.updated_at.desc(), Application.id.desc())
            .limit(200)
        )
    )


def _apply_event_filters(
    statement: Select[tuple[CareerEvent]],
    filters: AgendaFilters,
) -> Select[tuple[CareerEvent]]:
    if filters.status:
        statement = statement.where(CareerEvent.confirmation_status == filters.status)
    if filters.event_type:
        statement = statement.where(CareerEvent.event_type == filters.event_type)
    if filters.source:
        statement = statement.where(CareerEvent.source == filters.source)
    if filters.job_id:
        statement = statement.where(CareerEvent.job_id == filters.job_id)
    if filters.application_id:
        statement = statement.where(CareerEvent.application_id == filters.application_id)
    return statement


def _event_day(event: CareerEvent, timezone: ZoneInfo) -> date:
    starts_at = event.starts_at
    if starts_at is None:
        raise RadarError("Evento sem data nao possui dia local.")
    aware = starts_at if starts_at.tzinfo is not None else starts_at.replace(tzinfo=UTC)
    return aware.astimezone(timezone).date()


def _valid_month(year: int, month: int) -> tuple[int, int]:
    if year < 2000 or year > 2100:
        raise RadarError("Ano da agenda invalido.")
    if month < 1 or month > 12:
        raise RadarError("Mes da agenda invalido.")
    return year, month


def _optional_positive_int(value: str | None, label: str) -> int | None:
    text = _text(value)
    if text is None:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise RadarError(f"ID de {label} deve ser numerico.") from exc
    if parsed <= 0:
        raise RadarError(f"ID de {label} invalido.")
    return parsed


def _enum[EnumType: ReadableEnum](
    enum_type: type[EnumType],
    value: str | None,
    label: str,
) -> EnumType | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return parse_enum_value(enum_type, text)
    except ValueError as exc:
        raise RadarError(f"Filtro invalido para {label}: {exc}") from exc


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
