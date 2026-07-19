from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.responses import Response
from starlette.templating import Jinja2Templates

from radar_vagas.applications.review import (
    add_application_event,
    dismiss_job,
    mark_applied,
    mark_seen,
    restore_job,
    shortlist_job,
)
from radar_vagas.calendar.service import (
    cancel_event,
    complete_event,
    confirm_event,
    create_event,
    dismiss_event,
    get_event,
    update_event,
)
from radar_vagas.config.loaders import load_ui_config, write_ui_local_config
from radar_vagas.config.schemas import UiConfig
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
    ProfileEvidenceType,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.profile.service import (
    activate_profile_version,
    compare_active_jobs_to_profile,
    compare_job_to_profile,
    import_professional_profile,
)
from radar_vagas.web.collection import LocalCollectionRunner
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import (
    active_profile_version,
    agenda_events,
    application_detail,
    applications_list,
    dashboard_context,
    job_detail,
    jobs_page,
    latest_comparison,
    profile_versions,
    review_state_for,
    sources_context,
)
from radar_vagas.web.security import (
    clean_upload_suffix,
    csrf_protect,
    csrf_token_for_request,
    form_value,
    positive_id,
    safe_external_url,
    validate_upload_metadata,
)
from radar_vagas.web.view_models import (
    APPLICATION_EVENT_LABELS,
    CAREER_EVENT_LABELS,
    CAREER_STATUS_LABELS,
    EMPLOYMENT_TYPE_LABELS,
    JOB_STATUS_LABELS,
    REVIEW_STATE_LABELS,
    WORK_MODEL_LABELS,
)

router = APIRouter()

DISMISS_REASONS = [
    ("not_data", "Fora de dados ou tecnologia"),
    ("location", "Localizacao incompatavel"),
    ("seniority", "Senioridade incompatavel"),
    ("requirements", "Requisitos centrais ausentes"),
    ("company", "Empresa fora do alvo"),
    ("duplicate", "Duplicada"),
    ("other", "Outro motivo"),
]


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    if active_profile_version(session) is None:
        return _redirect("/onboarding")
    ui = load_ui_config(settings.config_dir)
    return _render(
        request,
        "dashboard.html",
        {"dashboard": dashboard_context(session, page_size=ui.page_size)},
    )


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return _render(
        request,
        "onboarding.html",
        {
            "active_profile": active_profile_version(session),
            "versions": profile_versions(session),
        },
    )


@router.post("/onboarding/profile/manual")
def onboarding_manual_profile(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    profile_name: Annotated[str, Form()] = "",
    headline: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    skills: Annotated[str, Form()] = "",
    timezone: Annotated[str, Form()] = "America/Sao_Paulo",
) -> RedirectResponse:
    _ = request, _csrf
    profile_path = _write_manual_profile(settings, profile_name, headline, summary, skills)
    imported = import_professional_profile(session, profile_path, activate=True)
    ui = load_ui_config(settings.config_dir)
    write_ui_local_config(settings.config_dir, ui.model_copy(update={"timezone": timezone}))
    return _redirect(
        "/profile",
        message=f"Perfil {imported.profile_name} ativado.",
    )


@router.post("/onboarding/profile/upload")
async def onboarding_upload_profile(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    file: Annotated[UploadFile, File()] | None = None,
    timezone: Annotated[str, Form()] = "America/Sao_Paulo",
) -> RedirectResponse:
    _ = request, _csrf
    if file is None:
        raise HTTPException(status_code=400, detail="Arquivo nao enviado.")
    content = await file.read()
    validate_upload_metadata(file.filename or "profile.txt", content)
    profile_path = _write_uploaded_profile(settings, file.filename or "profile.txt", content)
    imported = import_professional_profile(session, profile_path, activate=True)
    ui = load_ui_config(settings.config_dir)
    write_ui_local_config(settings.config_dir, ui.model_copy(update={"timezone": timezone}))
    return _redirect("/profile", message=f"Perfil {imported.profile_name} importado.")


@router.get("/jobs", response_class=HTMLResponse)
def jobs(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    status: str | None = None,
    review: str | None = None,
    page: int = 1,
    sort: str | None = None,
) -> HTMLResponse:
    ui = load_ui_config(settings.config_dir)
    selected_sort = sort or ui.default_job_sort
    page_view = jobs_page(
        session,
        q=form_value(q),
        status=form_value(status),
        review=form_value(review),
        page=page,
        page_size=ui.page_size,
        sort=selected_sort,
    )
    return _render(
        request,
        "jobs.html",
        {
            "page": page_view,
            "filters": {"q": q or "", "status": status or "", "review": review or ""},
            "sort": selected_sort,
            "job_statuses": JOB_STATUS_LABELS,
            "review_states": REVIEW_STATE_LABELS,
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
) -> HTMLResponse:
    job_id = positive_id(job_id, "vaga")
    item = job_detail(session, job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    return _render(
        request,
        "job_detail.html",
        {
            "job": item,
            "review_state": review_state_for(item),
            "comparison": latest_comparison(item),
            "dismiss_reasons": DISMISS_REASONS,
            "event_types": CAREER_EVENT_LABELS,
            "application_url": safe_external_url(item.application_url),
        },
    )


@router.post("/jobs/{job_id}/seen")
def job_seen(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    mark_seen(session, positive_id(job_id, "vaga"), source="web")
    return _redirect(f"/jobs/{job_id}", message="Vaga marcada como vista.")


@router.post("/jobs/{job_id}/shortlist")
def job_shortlist(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    shortlist_job(session, positive_id(job_id, "vaga"), source="web")
    return _redirect(f"/jobs/{job_id}", message="Vaga adicionada aos favoritos.")


@router.post("/jobs/{job_id}/dismiss")
def job_dismiss(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    reason_code: Annotated[str, Form()] = "other",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    allowed_reasons = {reason for reason, _label in DISMISS_REASONS}
    reason = reason_code if reason_code in allowed_reasons else "other"
    dismiss_job(
        session,
        positive_id(job_id, "vaga"),
        reason_code=reason,
        notes=form_value(notes),
        source="web",
    )
    return _redirect("/jobs", message="Vaga descartada.")


@router.post("/jobs/{job_id}/restore")
def job_restore(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    restore_job(session, settings, positive_id(job_id, "vaga"), source="web")
    return _redirect(f"/jobs/{job_id}", message="Vaga restaurada.")


@router.post("/jobs/{job_id}/apply")
def job_apply(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    applied_at: Annotated[str, Form()] = "",
    platform: Annotated[str, Form()] = "",
    external_reference: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    ui = load_ui_config(settings.config_dir)
    mark_applied(
        session,
        settings,
        positive_id(job_id, "vaga"),
        applied_at=_parse_local_datetime(applied_at, ui.timezone),
        platform=form_value(platform),
        external_reference=form_value(external_reference),
        notes=form_value(notes),
        source="web",
    )
    return _redirect(f"/jobs/{job_id}", message="Candidatura registrada.")


@router.post("/jobs/{job_id}/compare")
def job_compare(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    result = compare_job_to_profile(session, positive_id(job_id, "vaga"))
    return _redirect(
        f"/jobs/{job_id}",
        message=f"Compatibilidade calculada: {result.overall_score}/100.",
    )


@router.post("/jobs/{job_id}/events")
def job_add_event(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    event_type: Annotated[str, Form()] = CareerEventType.CUSTOM.value,
    title: Annotated[str, Form()] = "",
    starts_at: Annotated[str, Form()] = "",
    ends_at: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    ui = load_ui_config(settings.config_dir)
    create_event(
        session,
        job_id=positive_id(job_id, "vaga"),
        event_type=parse_enum_value(CareerEventType, event_type),
        title=title,
        starts_at=_parse_local_datetime(starts_at, ui.timezone),
        ends_at=_parse_local_datetime(ends_at, ui.timezone),
        timezone=ui.timezone,
        source=CareerEventSource.MANUAL,
        notes=form_value(notes),
    )
    return _redirect(f"/jobs/{job_id}", message="Evento criado.")


@router.get("/applications", response_class=HTMLResponse)
def applications(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
) -> HTMLResponse:
    return _render(
        request,
        "applications.html",
        {
            "applications": applications_list(session, status=form_value(status)),
            "status": status or "",
        },
    )


@router.get("/applications/{application_id}", response_class=HTMLResponse)
def application(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    application_id: int,
) -> HTMLResponse:
    application_id = positive_id(application_id, "candidatura")
    item = application_detail(session, application_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Candidatura nao encontrada.")
    return _render(
        request,
        "application_detail.html",
        {"application": item, "application_event_types": APPLICATION_EVENT_LABELS},
    )


@router.post("/applications/{application_id}/events")
def application_add_event(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    application_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    event_type: Annotated[str, Form()] = ApplicationEventType.PROCESS_UPDATE.value,
    occurred_at: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    ui = load_ui_config(settings.config_dir)
    add_application_event(
        session,
        positive_id(application_id, "candidatura"),
        event_type=parse_enum_value(ApplicationEventType, event_type),
        occurred_at=_parse_local_datetime(occurred_at, ui.timezone),
        notes=form_value(notes),
        source="web",
    )
    return _redirect(f"/applications/{application_id}", message="Evento registrado.")


@router.get("/agenda", response_class=HTMLResponse)
def agenda(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    status: str | None = None,
    event_type: str | None = None,
) -> HTMLResponse:
    return _render(
        request,
        "agenda.html",
        {
            "events": agenda_events(
                session,
                status=form_value(status),
                event_type=form_value(event_type),
            ),
            "status": status or "",
            "event_type": event_type or "",
            "event_types": CAREER_EVENT_LABELS,
            "event_statuses": CAREER_STATUS_LABELS,
        },
    )


@router.post("/agenda/events")
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
        job_id=_optional_positive_int(job_id),
        application_id=_optional_positive_int(application_id),
        starts_at=_parse_local_datetime(starts_at, ui.timezone),
        ends_at=_parse_local_datetime(ends_at, ui.timezone),
        timezone=ui.timezone,
        source=CareerEventSource.MANUAL,
        confirmation_status=parse_enum_value(CareerEventConfirmationStatus, status),
        location=form_value(location),
        meeting_url=safe_external_url(meeting_url),
        notes=form_value(notes),
    )
    return _redirect("/agenda", message="Evento criado.")


@router.get("/agenda/events/{event_id}/edit", response_class=HTMLResponse)
def agenda_edit_event(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    event_id: int,
) -> HTMLResponse:
    event = get_event(session, positive_id(event_id, "evento"))
    return _render(
        request,
        "agenda_edit.html",
        {"event": event, "event_types": CAREER_EVENT_LABELS},
    )


@router.post("/agenda/events/{event_id}/edit")
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
        starts_at=_parse_local_datetime(starts_at, ui.timezone),
        ends_at=_parse_local_datetime(ends_at, ui.timezone),
        timezone=ui.timezone,
        location=form_value(location),
        meeting_url=safe_external_url(meeting_url),
        notes=form_value(notes),
        source="web",
    )
    return _redirect("/agenda", message="Evento atualizado.")


@router.post("/agenda/events/{event_id}/{action}")
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
    return _redirect("/agenda", message="Agenda atualizada.")


@router.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return _render(
        request,
        "profile.html",
        {
            "active_profile": active_profile_version(session),
            "versions": profile_versions(session),
            "ui_config": load_ui_config(settings.config_dir),
        },
    )


@router.post("/profile/config")
def profile_config(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    timezone: Annotated[str, Form()] = "America/Sao_Paulo",
    page_size: Annotated[int, Form()] = 25,
    auto_open_browser: Annotated[str, Form()] = "",
    default_job_sort: Annotated[str, Form()] = "score",
    theme_preference: Annotated[str, Form()] = "system",
) -> RedirectResponse:
    _ = request, _csrf
    config = UiConfig(
        timezone=timezone,
        page_size=page_size,
        auto_open_browser=auto_open_browser == "on",
        default_job_sort=default_job_sort,
        theme_preference=theme_preference,
    )
    write_ui_local_config(settings.config_dir, config)
    return _redirect("/profile", message="Configuracao local salva.")


@router.post("/profile/import")
async def profile_import(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    file: Annotated[UploadFile, File()] | None = None,
) -> RedirectResponse:
    _ = request, _csrf
    if file is None:
        raise HTTPException(status_code=400, detail="Arquivo nao enviado.")
    content = await file.read()
    validate_upload_metadata(file.filename or "profile.txt", content)
    imported = import_professional_profile(
        session,
        _write_uploaded_profile(settings, file.filename or "profile.txt", content),
        activate=True,
    )
    return _redirect("/profile", message=f"Perfil {imported.profile_name} importado.")


@router.post("/profile/manual")
def profile_manual(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    profile_name: Annotated[str, Form()] = "",
    headline: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    skills: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    imported = import_professional_profile(
        session,
        _write_manual_profile(settings, profile_name, headline, summary, skills),
        activate=True,
    )
    return _redirect("/profile", message=f"Perfil {imported.profile_name} criado.")


@router.post("/profile/{version_id}/activate")
def profile_activate(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    version_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    activate_profile_version(session, positive_id(version_id, "perfil"), source="web")
    return _redirect("/profile", message="Versao ativada.")


@router.post("/profile/batch-compare")
def profile_batch_compare(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    limit: Annotated[int, Form()] = 50,
) -> RedirectResponse:
    _ = request, _csrf
    result = compare_active_jobs_to_profile(session, limit=limit)
    message = (
        f"Analise em lote: {result.created} criadas, {result.reused} reutilizadas, "
        f"{result.failed} falhas."
    )
    return _redirect("/profile", message=message)


@router.get("/sources", response_class=HTMLResponse)
def sources(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    runner = _collection_runner(request)
    return _render(
        request,
        "sources.html",
        {**sources_context(session), "collection_status": runner.status},
    )


@router.post("/sources/collect-search-plan")
def sources_collect_search_plan(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = _csrf
    runner = _collection_runner(request)
    result = runner.run_search_plan(settings)
    found = sum(execution.summary.found for _query, execution in result.executions)
    return _redirect("/sources", message=f"Coleta manual finalizada: {found} vagas processadas.")


def _render(
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
            "employment_types": EMPLOYMENT_TYPE_LABELS,
            "work_models": WORK_MODEL_LABELS,
        },
    }
    payload.update(context)
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=payload,
        status_code=status_code,
    )


def _redirect(path: str, *, message: str | None = None) -> RedirectResponse:
    if message:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}{urlencode({'message': message})}"
    return RedirectResponse(path, status_code=303)


def _parse_local_datetime(value: str | None, timezone: str) -> datetime | None:
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


def _optional_positive_int(value: str) -> int | None:
    text = form_value(value)
    if text is None:
        return None
    try:
        parsed = int(text)
    except ValueError as exc:
        raise RadarError("ID informado deve ser numerico.") from exc
    return positive_id(parsed)


def _write_uploaded_profile(settings: Settings, filename: str, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()[:16]
    suffix = clean_upload_suffix(filename)
    path = _web_import_dir(settings) / f"web-profile-{digest}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _write_manual_profile(
    settings: Settings,
    profile_name: str,
    headline: str,
    summary: str,
    skills: str,
) -> Path:
    names = [line.strip() for line in skills.replace(",", "\n").splitlines() if line.strip()]
    if not names:
        raise RadarError("Informe ao menos uma habilidade no perfil manual.")
    payload = {
        "profile_name": profile_name.strip() or "Perfil manual",
        "headline": headline.strip() or None,
        "summary": summary.strip() or "Perfil criado pela interface local.",
        "skills": [
            {
                "name": name,
                "category": "manual",
                "evidence": [
                    {
                        "title": f"Habilidade informada manualmente: {name}",
                        "evidence_type": ProfileEvidenceType.SKILL.value,
                    }
                ],
            }
            for name in names
        ],
        "experiences": [],
        "projects": [],
        "education": [],
        "languages": [],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:16]
    path = _web_import_dir(settings) / f"web-manual-profile-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return path


def _collection_runner(request: Request) -> LocalCollectionRunner:
    runner = request.app.state.collection_runner
    if not isinstance(runner, LocalCollectionRunner):
        raise RuntimeError("Coletor web nao inicializado.")
    return runner


def _web_import_dir(settings: Settings) -> Path:
    database_path = settings.database_path
    if database_path is not None and PROJECT_ROOT not in database_path.resolve().parents:
        return database_path.parent / "imports"
    return PROJECT_ROOT / "data" / "imports"
