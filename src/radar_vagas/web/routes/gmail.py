from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from radar_vagas.config.settings import Settings
from radar_vagas.gmail_insights.service import (
    gmail_config_status,
    recent_gmail_messages,
    sync_gmail_application_insights,
)
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.routes.common import redirect, render
from radar_vagas.web.security import csrf_protect
from radar_vagas.web.view_models import APPLICATION_EVENT_LABELS

router = APIRouter(prefix="/gmail")


@router.get("", response_class=HTMLResponse)
def gmail(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    config, status_text = gmail_config_status(settings)
    if config.enabled and getattr(request.app.state, "gmail_client", None) is not None:
        status_text = "Gmail fake conectado"
    return render(
        request,
        "gmail.html",
        {
            "gmail_enabled": config.enabled,
            "gmail_status_text": status_text,
            "gmail_max_results": config.max_results,
            "gmail_messages": recent_gmail_messages(session),
            "application_event_labels": {
                event_type.value: label for event_type, label in APPLICATION_EVENT_LABELS.items()
            },
        },
    )


@router.post("/sync")
def gmail_sync(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = _csrf
    client = getattr(request.app.state, "gmail_client", None)
    result = sync_gmail_application_insights(session, settings, client=client)
    return redirect("/gmail", message=result.message)
