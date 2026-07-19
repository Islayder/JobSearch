from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.templating import Jinja2Templates

from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.domain.errors import RadarError
from radar_vagas.web.collection import LocalCollectionRunner
from radar_vagas.web.security import csrf_token_for_request, form_value, positive_id
from radar_vagas.web.view_models import (
    APPLICATION_STAGE_LABELS,
    APPLICATION_STATUS_LABELS,
    CAREER_STATUS_LABELS,
    EMPLOYMENT_TYPE_LABELS,
    JOB_STATUS_LABELS,
    REVIEW_STATE_LABELS,
    WORK_MODEL_LABELS,
)

DISMISS_REASONS = [
    ("not_data", "Fora de dados ou tecnologia"),
    ("location", "Localizacao incompatavel"),
    ("seniority", "Senioridade incompatavel"),
    ("requirements", "Requisitos centrais ausentes"),
    ("company", "Empresa fora do alvo"),
    ("duplicate", "Duplicada"),
    ("other", "Outro motivo"),
]


def render(
    request: Request,
    template: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    templates = request.app.state.templates
    if not isinstance(templates, Jinja2Templates):
        raise RuntimeError("Templates da interface nao inicializados.")
    settings = request.app.state.radar_settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Settings da interface nao inicializado.")
    ui = load_ui_config(settings.config_dir)
    payload: dict[str, Any] = {
        "request": request,
        "csrf_token": csrf_token_for_request(request),
        "ui": ui,
        "message": request.query_params.get("message"),
        "labels": {
            "job_statuses": JOB_STATUS_LABELS,
            "review_states": REVIEW_STATE_LABELS,
            "employment_types": EMPLOYMENT_TYPE_LABELS,
            "work_models": WORK_MODEL_LABELS,
            "application_statuses": APPLICATION_STATUS_LABELS,
            "application_stages": APPLICATION_STAGE_LABELS,
            "career_statuses": CAREER_STATUS_LABELS,
        },
    }
    payload.update(context)
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=payload,
        status_code=status_code,
    )


def redirect(path: str, *, message: str | None = None) -> RedirectResponse:
    if message:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}{urlencode({'message': message})}"
    return RedirectResponse(path, status_code=303)


def parse_local_datetime(value: str | None, timezone: str) -> datetime | None:
    text = form_value(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RadarError(f"Data/hora invalida: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed.astimezone(UTC)


def optional_positive_int(value: str | None) -> int | None:
    text = form_value(value)
    if text is None:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise RadarError("ID informado deve ser numerico.") from exc
    return positive_id(parsed)


def collection_runner(request: Request) -> LocalCollectionRunner:
    runner = request.app.state.collection_runner
    if not isinstance(runner, LocalCollectionRunner):
        raise RuntimeError("Coletor web nao inicializado.")
    return runner


def requested_id(value: int, label: str) -> int:
    try:
        return positive_id(value, label)
    except HTTPException:
        raise


def response_payload(status: Any) -> dict[str, Any]:
    return {
        "state": status.state,
        "started_at": status.started_at.isoformat() if status.started_at else None,
        "finished_at": status.finished_at.isoformat() if status.finished_at else None,
        "message": status.message,
        "found": status.found,
        "created": status.created,
        "errors": status.errors,
    }
