from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from radar_vagas.config.settings import Settings
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import sources_context
from radar_vagas.web.routes.common import collection_runner, redirect, render, response_payload
from radar_vagas.web.security import csrf_protect

router = APIRouter(prefix="/sources")


@router.get("", response_class=HTMLResponse)
def sources(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    runner = collection_runner(request)
    return render(
        request,
        "sources.html",
        {**sources_context(session), "collection_status": runner.status},
    )


@router.post("/collect-search-plan")
def sources_collect_search_plan(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = _csrf
    runner = collection_runner(request)
    status = runner.start_search_plan(settings)
    if status.state == "running":
        return redirect("/sources", message="Coleta iniciada em segundo plano.")
    return redirect("/sources", message=status.message)


@router.get("/collection-status")
def collection_status(request: Request) -> JSONResponse:
    runner = collection_runner(request)
    return JSONResponse(response_payload(runner.status))
