from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from radar_vagas.applications.review import (
    dismiss_job,
    mark_applied,
    mark_seen,
    restore_job,
    shortlist_job,
    unshortlist_job,
)
from radar_vagas.calendar.service import create_event
from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    CareerEventSource,
    CareerEventType,
    EmploymentType,
    WorkModel,
    parse_enum_value,
)
from radar_vagas.profile.service import compare_job_to_profile
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import (
    JOB_TABS,
    job_detail,
    jobs_page,
    latest_comparison,
    parse_job_filters,
    review_state_for,
    valid_job_actions,
)
from radar_vagas.web.routes.common import (
    DISMISS_REASONS,
    parse_local_datetime,
    redirect,
    render,
)
from radar_vagas.web.security import csrf_protect, form_value, positive_id, safe_external_url
from radar_vagas.web.view_models import (
    CAREER_EVENT_LABELS,
    ELIGIBILITY_LABELS,
    JOB_STATUS_LABELS,
    RELEVANCE_LABELS,
    REVIEW_STATE_LABELS,
)

router = APIRouter(prefix="/jobs")


@router.get("", response_class=HTMLResponse)
def jobs(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    status: str | None = None,
    review: str | None = None,
    employment_type: str | None = None,
    work_model: str | None = None,
    provider: str | None = None,
    eligibility: str | None = None,
    relevance: str | None = None,
    min_ranking: str | None = None,
    min_compatibility: str | None = None,
    company: str | None = None,
    city: str | None = None,
    state: str | None = None,
    only_with_compatibility: str | None = None,
    only_without_compatibility: str | None = None,
    tab: str | None = None,
    page: int = 1,
    sort: str | None = None,
) -> HTMLResponse:
    ui = load_ui_config(settings.config_dir)
    raw_filters = {
        "q": q,
        "status": status,
        "review": review,
        "employment_type": employment_type,
        "work_model": work_model,
        "provider": provider,
        "eligibility": eligibility,
        "relevance": relevance,
        "min_ranking": min_ranking,
        "min_compatibility": min_compatibility,
        "company": company,
        "city": city,
        "state": state,
        "only_with_compatibility": only_with_compatibility,
        "only_without_compatibility": only_without_compatibility,
        "tab": tab,
    }
    filters = parse_job_filters(raw_filters)
    selected_sort = sort or ui.default_job_sort
    page_view = jobs_page(
        session,
        filters=filters,
        page=page,
        page_size=ui.page_size,
        sort=selected_sort,
    )
    return render(
        request,
        "jobs.html",
        {
            "page": page_view,
            "filters": {key: value or "" for key, value in raw_filters.items()},
            "sort": selected_sort,
            "tabs": JOB_TABS,
            "job_statuses": JOB_STATUS_LABELS,
            "review_states": REVIEW_STATE_LABELS,
            "employment_types": {item: item.value for item in EmploymentType},
            "work_models": {item: item.value for item in WorkModel},
            "eligibility_statuses": ELIGIBILITY_LABELS,
            "relevance_statuses": RELEVANCE_LABELS,
        },
    )


@router.get("/{job_id}", response_class=HTMLResponse)
def job(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
) -> HTMLResponse:
    job_id = positive_id(job_id, "vaga")
    item = job_detail(session, job_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    comparison = latest_comparison(item)
    return render(
        request,
        "job_detail.html",
        {
            "job": item,
            "review_state": review_state_for(item),
            "actions": valid_job_actions(item),
            "comparison": comparison,
            "comparison_identity": _comparison_identity(comparison),
            "dismiss_reasons": DISMISS_REASONS,
            "event_types": CAREER_EVENT_LABELS,
            "application_url": safe_external_url(item.application_url),
        },
    )


@router.post("/{job_id}/seen")
def job_seen(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    mark_seen(session, positive_id(job_id, "vaga"), source="web")
    return redirect(f"/jobs/{job_id}", message="Vaga marcada como vista.")


@router.post("/{job_id}/shortlist")
def job_shortlist(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    shortlist_job(session, positive_id(job_id, "vaga"), source="web")
    return redirect(f"/jobs/{job_id}", message="Vaga adicionada aos favoritos.")


@router.post("/{job_id}/unshortlist")
def job_unshortlist(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    unshortlist_job(session, positive_id(job_id, "vaga"), source="web")
    return redirect(f"/jobs/{job_id}", message="Vaga removida dos favoritos.")


@router.post("/{job_id}/dismiss")
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
    return redirect("/jobs?tab=descartadas", message="Vaga descartada.")


@router.post("/{job_id}/restore")
def job_restore(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    restore_job(session, settings, positive_id(job_id, "vaga"), source="web")
    return redirect(f"/jobs/{job_id}", message="Vaga restaurada.")


@router.post("/{job_id}/apply")
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
        applied_at=parse_local_datetime(applied_at, ui.timezone),
        platform=form_value(platform),
        external_reference=form_value(external_reference),
        notes=form_value(notes),
        source="web",
    )
    return redirect(f"/jobs/{job_id}", message="Candidatura registrada.")


@router.post("/{job_id}/compare")
def job_compare(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    result = compare_job_to_profile(session, positive_id(job_id, "vaga"))
    return redirect(
        f"/jobs/{job_id}",
        message=f"Compatibilidade calculada: {result.overall_score}/100.",
    )


@router.post("/{job_id}/events")
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
        starts_at=parse_local_datetime(starts_at, ui.timezone),
        ends_at=parse_local_datetime(ends_at, ui.timezone),
        timezone=ui.timezone,
        source=CareerEventSource.MANUAL,
        notes=form_value(notes),
    )
    return redirect(f"/jobs/{job_id}", message="Evento criado.")


def _comparison_identity(comparison: object | None) -> str | None:
    if comparison is None:
        return None
    raw = (
        f"{getattr(comparison, 'profile_version_id', '')}:"
        f"{getattr(comparison, 'rules_version', '')}"
    )
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
