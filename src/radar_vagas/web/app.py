from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.database import create_sqlite_engine, session_factory
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.web.collection import LocalCollectionRunner
from radar_vagas.web.routes import router
from radar_vagas.web.security import (
    apply_security_headers,
    resolve_csrf_token,
    safe_external_url,
    set_csrf_cookie,
)
from radar_vagas.web.view_models import (
    format_date,
    format_datetime,
    json_list,
    label,
    preview,
    status_class,
)

RequestHandler = Callable[[Request], Awaitable[Response]]


def create_app(settings: Settings | None = None, *, debug: bool = False) -> FastAPI:
    resolved_settings = settings or Settings.from_env(debug=debug)
    run_migrations(resolved_settings)
    engine = create_sqlite_engine(resolved_settings)
    templates = _templates()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Radar de Vagas",
        debug=debug,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.radar_settings = resolved_settings
    app.state.radar_engine = engine
    app.state.radar_session_factory = session_factory(engine)
    app.state.templates = templates
    app.state.csrf_secret = secrets.token_urlsafe(32)
    app.state.collection_runner = LocalCollectionRunner()

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(router)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next: RequestHandler) -> Response:
        request.state.csrf_token = resolve_csrf_token(request)
        response = await call_next(request)
        apply_security_headers(response)
        set_csrf_cookie(request, response)
        return response

    @app.exception_handler(RadarError)
    async def radar_error_handler(request: Request, exc: RadarError) -> Response:
        if debug:
            raise exc
        return _error_response(request, str(exc), status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> Response:
        if debug and exc.status_code >= 500:
            raise exc
        detail = exc.detail if isinstance(exc.detail, str) else "Solicitacao invalida."
        return _error_response(request, detail, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> Response:
        if debug:
            raise exc
        return _error_response(request, "Falha inesperada na interface local.", status_code=500)

    return app


def _templates() -> Jinja2Templates:
    template_dir = Path(__file__).resolve().parent / "templates"
    templates = Jinja2Templates(directory=template_dir)
    templates.env.filters["label"] = label
    templates.env.filters["datetime"] = format_datetime
    templates.env.filters["date"] = format_date
    templates.env.filters["preview"] = preview
    templates.env.filters["json_list"] = json_list
    templates.env.filters["status_class"] = status_class
    templates.env.globals["safe_external_url"] = safe_external_url
    return templates


def _error_response(request: Request, message: str, *, status_code: int) -> Response:
    templates = request.app.state.templates
    settings = request.app.state.radar_settings
    ui = load_ui_config(settings.config_dir) if isinstance(settings, Settings) else None
    if isinstance(templates, Jinja2Templates):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "request": request,
                "ui": ui,
                "message": None,
                "error_message": message,
                "status_code": status_code,
                "csrf_token": getattr(request.state, "csrf_token", ""),
            },
            status_code=status_code,
        )
    if status_code >= 500:
        return PlainTextResponse("Falha inesperada na interface local.", status_code=500)
    return HTMLResponse(message, status_code=status_code)
