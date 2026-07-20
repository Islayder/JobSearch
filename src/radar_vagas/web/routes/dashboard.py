from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from starlette.responses import Response

from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import active_profile_version, dashboard_context
from radar_vagas.web.routes.common import redirect, render

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    if active_profile_version(session) is None:
        return redirect("/onboarding")
    ui = load_ui_config(settings.config_dir)
    return render(
        request,
        "dashboard.html",
        {"dashboard": dashboard_context(session, page_size=ui.page_size, timezone=ui.timezone)},
    )
