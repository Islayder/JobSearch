from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from radar_vagas.calendar.service import (
    cancel_event,
    complete_event,
    confirm_event,
    create_event,
    dismiss_event,
    get_event,
    update_event,
)
from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
    parse_enum_value,
)
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import (
    agenda_context,
    application_options,
    job_options,
    parse_agenda_filters,
)
from radar_vagas.web.routes.common import (
    optional_positive_int,
    parse_local_datetime,
    redirect,
    render,
)
from radar_vagas.web.security import csrf_protect, form_value, positive_id
from radar_vagas.web.view_models import CAREER_EVENT_LABELS, CAREER_STATUS_LABELS

router = APIRouter(prefix="/agenda")


@router.get("", response_class=HTMLResponse)
def agenda(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
    event_type: str | None = None,
    source: str | None = None,
    job_id: str | None = None,
    application_id: str | None = None,
    year: int | None = None,
    month: int | None = None,
) -> HTMLResponse:
    ui = load_ui_config(settings.config_dir)
    now = datetime.now()
    selected_year = year or now.year
    selected_month = month or now.month
    raw_filters = {
        "status": status,
        "event_type": event_type,
        "source": source,
        "job_id": job_id,
        "application_id": application_id,
    }
    filters = parse_agenda_filters(raw_filters)
    return render(
        request,
        "agenda.html",
        {
            "agenda": agenda_context(
                session,
                year=selected_year,
                month=selected_month,
                timezone=ui.timezone,
                filters=filters,
            ),
            "filters": {key: value or "" for key, value in raw_filters.items()},
            "event_types": CAREER_EVENT_LABELS,
            "event_statuses": CAREER_STATUS_LABELS,
            "event_sources": {item: item.value for item in CareerEventSource},
        },
    )


@router.post("/events")
def agenda_create_event(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    event_type: Annotated[str, Form()] = CareerEventType.CUSTOM.value,
    title: Annotated[str, Form()] = "",
    starts_at: Annotated[str, Form()] = "",
    ends_at: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = CareerEventConfirmationStatus.CONFIRMED.value,
    job_id: Annotated[str, Form()] = "",
    application_id: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    meeting_url: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    ui = load_ui_config(settings.config_dir)
    create_event(
        session,
        event_type=parse_enum_value(CareerEventType, event_type),
        title=title,
        job_id=optional_positive_int(job_id),
        application_id=optional_positive_int(application_id),
        starts_at=parse_local_datetime(starts_at, ui.timezone),
        ends_at=parse_local_datetime(ends_at, ui.timezone),
        timezone=ui.timezone,
        source=CareerEventSource.MANUAL,
        confirmation_status=parse_enum_value(CareerEventConfirmationStatus, status),
        location=form_value(location),
        meeting_url=form_value(meeting_url),
        notes=form_value(notes),
    )
    return redirect("/agenda", message="Evento criado.")


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def agenda_edit_event(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    event_id: int,
) -> HTMLResponse:
    event = get_event(session, positive_id(event_id, "evento"))
    return render(
        request,
        "agenda_edit.html",
        {
            "event": event,
            "event_types": CAREER_EVENT_LABELS,
            "jobs": job_options(session),
            "applications": application_options(session),
        },
    )


@router.post("/events/{event_id}/edit")
def agenda_update_event(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    event_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    title: Annotated[str, Form()] = "",
    starts_at: Annotated[str, Form()] = "",
    ends_at: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    meeting_url: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    ui = load_ui_config(settings.config_dir)
    update_event(
        session,
        positive_id(event_id, "evento"),
        title=title,
        starts_at=parse_local_datetime(starts_at, ui.timezone),
        ends_at=parse_local_datetime(ends_at, ui.timezone),
        timezone=ui.timezone,
        location=form_value(location),
        meeting_url=form_value(meeting_url),
        notes=form_value(notes),
        source="web",
    )
    return redirect("/agenda", message="Evento atualizado.")


@router.post("/events/{event_id}/{action}")
def agenda_event_action(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    event_id: int,
    action: str,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    event_id = positive_id(event_id, "evento")
    actions = {
        "confirm": confirm_event,
        "dismiss": dismiss_event,
        "complete": complete_event,
        "cancel": cancel_event,
    }
    handler = actions.get(action)
    if handler is None:
        raise HTTPException(status_code=404, detail="Acao de agenda desconhecida.")
    handler(session, event_id, source="web")
    return redirect("/agenda", message="Agenda atualizada.")
