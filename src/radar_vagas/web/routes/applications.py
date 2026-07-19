from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from radar_vagas.applications.review import add_application_event
from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    parse_enum_value,
)
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import (
    APPLICATION_SHORTCUTS,
    application_detail,
    applications_list,
    parse_application_filters,
)
from radar_vagas.web.routes.common import parse_local_datetime, redirect, render
from radar_vagas.web.security import csrf_protect, form_value, positive_id
from radar_vagas.web.view_models import (
    APPLICATION_EVENT_LABELS,
    APPLICATION_STAGE_LABELS,
    APPLICATION_STATUS_LABELS,
)

router = APIRouter(prefix="/applications")


@router.get("", response_class=HTMLResponse)
def applications(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    company: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    platform: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    shortcut: str | None = None,
) -> HTMLResponse:
    raw_filters = {
        "company": company,
        "status": status,
        "stage": stage,
        "platform": platform,
        "from_date": from_date,
        "to_date": to_date,
        "shortcut": shortcut,
    }
    filters = parse_application_filters(raw_filters)
    return render(
        request,
        "applications.html",
        {
            "applications": applications_list(session, filters=filters),
            "filters": {key: value or "" for key, value in raw_filters.items()},
            "shortcuts": APPLICATION_SHORTCUTS,
            "statuses": APPLICATION_STATUS_LABELS,
            "stages": APPLICATION_STAGE_LABELS,
        },
    )


@router.get("/{application_id}", response_class=HTMLResponse)
def application(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    application_id: int,
) -> HTMLResponse:
    application_id = positive_id(application_id, "candidatura")
    item = application_detail(session, application_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Candidatura nao encontrada.")
    return render(
        request,
        "application_detail.html",
        {
            "application": item,
            "application_event_types": APPLICATION_EVENT_LABELS,
            "event_shortcuts": APPLICATION_EVENT_SHORTCUTS,
            "terminal_events": {
                ApplicationEventType.REJECTED,
                ApplicationEventType.OFFER_RECEIVED,
                ApplicationEventType.WITHDRAWN,
            },
        },
    )


@router.post("/{application_id}/events")
def application_add_event(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    application_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    event_type: Annotated[str, Form()] = ApplicationEventType.PROCESS_UPDATE.value,
    occurred_at: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
    confirm_terminal: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    parsed_event = parse_enum_value(ApplicationEventType, event_type)
    if (
        parsed_event
        in {
            ApplicationEventType.REJECTED,
            ApplicationEventType.OFFER_RECEIVED,
            ApplicationEventType.WITHDRAWN,
        }
        and confirm_terminal != "on"
    ):
        raise HTTPException(status_code=400, detail="Confirme a mudanca final da candidatura.")
    ui = load_ui_config(settings.config_dir)
    add_application_event(
        session,
        positive_id(application_id, "candidatura"),
        event_type=parsed_event,
        occurred_at=parse_local_datetime(occurred_at, ui.timezone),
        notes=form_value(notes),
        source="web",
    )
    return redirect(f"/applications/{application_id}", message="Evento registrado.")


APPLICATION_EVENT_SHORTCUTS = [
    (ApplicationEventType.CONFIRMATION_RECEIVED, "Confirmacao recebida"),
    (ApplicationEventType.ASSESSMENT_INVITED, "Teste recebido"),
    (ApplicationEventType.ASSESSMENT_COMPLETED, "Teste concluido"),
    (ApplicationEventType.CASE_RECEIVED, "Case recebido"),
    (ApplicationEventType.CASE_SUBMITTED, "Case enviado"),
    (ApplicationEventType.INTERVIEW_INVITED, "Entrevista marcada"),
    (ApplicationEventType.INTERVIEW_COMPLETED, "Entrevista concluida"),
    (ApplicationEventType.PROCESS_UPDATE, "Atualizacao"),
    (ApplicationEventType.REJECTED, "Rejeicao"),
    (ApplicationEventType.OFFER_RECEIVED, "Oferta"),
    (ApplicationEventType.WITHDRAWN, "Retirada"),
]
