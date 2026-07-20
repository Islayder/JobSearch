from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Any

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
from radar_vagas.company_intelligence.service import (
    CompanyFactInput,
    CompanyProfileInput,
    CompanyReviewSnapshotInput,
    add_company_fact,
    add_company_review_snapshot,
    generate_interview_preparation,
    latest_interview_preparation,
    parse_company_information_source_type,
    upsert_company_profile,
)
from radar_vagas.config.loaders import load_ui_config
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    CareerEventSource,
    CareerEventType,
    CompanyInformationSourceType,
    EmploymentType,
    WorkModel,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.profile.service import (
    compare_job_to_profile,
    comparison_freshness,
    current_comparison_for_job,
)
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import (
    JOB_TABS,
    active_profile_version,
    historical_comparisons,
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
    profile_version = active_profile_version(session)
    comparison = current_comparison_for_job(item, profile_version)
    latest = latest_comparison(item)
    stale_comparison = latest if comparison is None else None
    freshness = comparison_freshness(item, stale_comparison or comparison, profile_version)
    return render(
        request,
        "job_detail.html",
        {
            "job": item,
            "review_state": review_state_for(item),
            "actions": valid_job_actions(item),
            "comparison": comparison,
            "stale_comparison": stale_comparison,
            "comparison_freshness": freshness,
            "historical_comparisons": historical_comparisons(item),
            "comparison_identity": _comparison_identity(comparison),
            "stale_comparison_identity": _comparison_identity(stale_comparison),
            "dismiss_reasons": DISMISS_REASONS,
            "event_types": CAREER_EVENT_LABELS,
            "application_url": safe_external_url(item.application_url),
            "company_profile": item.company.intelligence_profile,
            "company_facts": _company_fact_rows(item.company.facts),
            "company_review_snapshots": _review_snapshot_rows(item.company.review_snapshots),
            "company_origin_types": COMPANY_ORIGIN_TYPE_LABELS,
            "latest_interview_preparation": latest_interview_preparation(item),
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


@router.post("/{job_id}/company/profile")
def job_company_profile(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    name: Annotated[str, Form()] = "",
    official_website: Annotated[str, Form()] = "",
    industry: Annotated[str, Form()] = "",
    company_size: Annotated[str, Form()] = "",
    location: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    sources: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    item = job_detail(session, positive_id(job_id, "vaga"))
    if item is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    upsert_company_profile(
        session,
        item.company_id,
        CompanyProfileInput(
            name=name or item.company.canonical_name,
            official_website=form_value(official_website),
            industry=form_value(industry),
            company_size=form_value(company_size),
            location=form_value(location),
            description=form_value(description),
            sources=_split_lines(sources),
        ),
    )
    return redirect(f"/jobs/{job_id}", message="Perfil da empresa salvo.")


@router.post("/{job_id}/company/facts")
def job_company_fact(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    category: Annotated[str, Form()] = "",
    content: Annotated[str, Form()] = "",
    origin_type: Annotated[str, Form()] = CompanyInformationSourceType.OFFICIAL_INFO.value,
    source_url: Annotated[str, Form()] = "",
    source_date: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    item = job_detail(session, positive_id(job_id, "vaga"))
    if item is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    add_company_fact(
        session,
        item.company_id,
        CompanyFactInput(
            category=category,
            content=content,
            origin_type=parse_company_information_source_type(origin_type),
            source_url=form_value(source_url),
            source_date=form_value(source_date),
            note=form_value(note),
        ),
    )
    return redirect(f"/jobs/{job_id}", message="Informacao da empresa adicionada.")


@router.post("/{job_id}/company/reviews")
def job_company_review_snapshot(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    platform: Annotated[str, Form()] = "",
    overall_rating: Annotated[str, Form()] = "",
    review_count: Annotated[str, Form()] = "",
    positives: Annotated[str, Form()] = "",
    negatives: Annotated[str, Form()] = "",
    period: Annotated[str, Form()] = "",
    source_url: Annotated[str, Form()] = "",
    source_note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    item = job_detail(session, positive_id(job_id, "vaga"))
    if item is None:
        raise HTTPException(status_code=404, detail="Vaga nao encontrada.")
    add_company_review_snapshot(
        session,
        item.company_id,
        CompanyReviewSnapshotInput(
            platform=platform,
            overall_rating=_optional_float(overall_rating, "avaliacao geral"),
            review_count=_optional_int(review_count, "quantidade de relatos"),
            positives=_split_lines(positives),
            negatives=_split_lines(negatives),
            period=form_value(period),
            source_url=form_value(source_url),
            source_note=form_value(source_note),
        ),
    )
    return redirect(f"/jobs/{job_id}", message="Relatos de funcionarios adicionados.")


@router.post("/{job_id}/interview-preparation")
def job_interview_preparation(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    job_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    preparation = generate_interview_preparation(session, positive_id(job_id, "vaga"))
    return redirect(
        f"/jobs/{job_id}#interview-preparation",
        message=f"Preparacao de entrevista {preparation.id} gerada.",
    )


def _comparison_identity(comparison: object | None) -> str | None:
    if comparison is None:
        return None
    raw = (
        f"{getattr(comparison, 'profile_version_id', '')}:"
        f"{getattr(comparison, 'rules_version', '')}"
    )
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


COMPANY_ORIGIN_TYPE_LABELS = {
    CompanyInformationSourceType.OFFICIAL_INFO: "Informacao oficial",
    CompanyInformationSourceType.EMPLOYEE_REPORT: "Relato de funcionarios",
    CompanyInformationSourceType.RADAR_INFERENCE: "Inferencia do Radar",
    CompanyInformationSourceType.USER_NOTE: "Anotacao do usuario",
}


def _company_fact_rows(facts: Iterable[Any]) -> list[dict[str, object]]:
    rows = []
    for fact in sorted(facts, key=lambda item: (item.origin_type.value, item.id)):
        rows.append(
            {
                "fact": fact,
                "origin_label": COMPANY_ORIGIN_TYPE_LABELS[fact.origin_type],
                "safe_url": safe_external_url(fact.source_url),
            }
        )
    return rows


def _review_snapshot_rows(snapshots: Iterable[Any]) -> list[dict[str, object]]:
    rows = []
    for snapshot in sorted(snapshots, key=lambda item: (item.created_at, item.id), reverse=True):
        rows.append(
            {
                "snapshot": snapshot,
                "safe_url": safe_external_url(snapshot.source_url),
            }
        )
    return rows


def _split_lines(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


def _optional_float(value: str, label: str) -> float | None:
    text = form_value(value)
    if text is None:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError as exc:
        raise RadarError(f"{label} deve ser numerico.") from exc


def _optional_int(value: str, label: str) -> int | None:
    text = form_value(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise RadarError(f"{label} deve ser numerico.") from exc
