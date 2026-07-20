from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.config.loaders import load_ui_config, write_ui_local_config
from radar_vagas.config.schemas import UiConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ProfileEvidenceType,
    ResumeImportCandidateType,
    ResumeImportDecision,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.models import ResumeImportCandidate, ResumeImportSession
from radar_vagas.profile.service import (
    EducationInput,
    EvidenceInput,
    ExperienceInput,
    LanguageInput,
    ProfessionalProfileInput,
    ProjectInput,
    SkillInput,
    activate_profile_version,
    compare_active_jobs_to_profile,
    create_professional_profile,
    import_professional_profile_bytes,
)
from radar_vagas.resume_import.repository import candidate_for_session, json_load
from radar_vagas.resume_import.service import (
    accept_candidate,
    confirm_import,
    create_import_session,
    discard_import,
    get_import_session,
    list_import_sessions,
    purge_import,
    remove_candidate,
    restore_candidate,
    retry_import_session,
    update_candidate,
    update_import_header,
)
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import active_profile_version, profile_versions
from radar_vagas.web.routes.common import redirect, render
from radar_vagas.web.security import (
    csrf_protect,
    form_value,
    positive_id,
    read_limited_profile_upload,
    read_limited_resume_upload,
)

router = APIRouter()
UPLOAD_FILE = File(...)


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return render(
        request,
        "onboarding.html",
        {
            "active_profile": active_profile_version(session),
            "versions": profile_versions(session),
            "evidence_hint": "Habilidade informada, mas ainda sem evidencia associada.",
        },
    )


@router.post("/onboarding/profile/manual")
async def onboarding_manual_profile(
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
    _ = _csrf
    document = _manual_profile_from_form(
        await request.form(),
        profile_name=profile_name,
        headline=headline,
        summary=summary,
        skills_text=skills,
    )
    imported = create_professional_profile(session, document, activate=True)
    ui = load_ui_config(settings.config_dir)
    write_ui_local_config(settings.config_dir, ui.model_copy(update={"timezone": timezone}))
    return redirect(
        "/profile",
        message=f"Perfil {imported.profile_name} ativado.",
    )


@router.post("/onboarding/profile/upload")
async def onboarding_upload_profile(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, UPLOAD_FILE],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    timezone: Annotated[str, Form()] = "America/Sao_Paulo",
) -> RedirectResponse:
    _ = request, _csrf
    content = await read_limited_profile_upload(file)
    imported = import_professional_profile_bytes(
        session,
        content,
        filename=file.filename or "profile.txt",
        activate=True,
    )
    ui = load_ui_config(settings.config_dir)
    write_ui_local_config(settings.config_dir, ui.model_copy(update={"timezone": timezone}))
    return redirect("/profile", message=f"Perfil {imported.profile_name} importado.")


@router.get("/profile", response_class=HTMLResponse)
def profile(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return render(
        request,
        "profile.html",
        {
            "active_profile": active_profile_version(session),
            "versions": profile_versions(session),
            "ui_config": load_ui_config(settings.config_dir),
            "evidence_hint": "Habilidade informada, mas ainda sem evidencia associada.",
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
    default_job_sort: Annotated[str, Form()] = "recommendation",
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
    return redirect("/profile", message="Configuracao local salva.")


@router.post("/profile/import")
async def profile_import(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, UPLOAD_FILE],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    content = await read_limited_profile_upload(file)
    imported = import_professional_profile_bytes(
        session,
        content,
        filename=file.filename or "profile.txt",
        activate=True,
    )
    return redirect("/profile", message=f"Perfil {imported.profile_name} importado.")


@router.get("/profile/resume/import", response_class=HTMLResponse)
def resume_import(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return render(
        request,
        "resume_import_upload.html",
        {
            "imports": list_import_sessions(session, limit=5),
            "resume_error": None,
        },
    )


@router.post("/profile/resume/import")
async def resume_import_create(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, UPLOAD_FILE],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    extraction_mode: Annotated[str, Form()] = "automatic",
) -> Response:
    _ = _csrf
    try:
        content = await read_limited_resume_upload(file)
        result = create_import_session(
            session,
            filename=file.filename or "curriculo.txt",
            content=content,
            extraction_mode=extraction_mode,
        )
    except RadarError as exc:
        return render(
            request,
            "resume_import_upload.html",
            {"imports": list_import_sessions(session, limit=5), "resume_error": str(exc)},
            status_code=400,
        )
    return redirect(
        f"/profile/resume/imports/{result.import_key}/review",
        message=f"{result.candidate_count} itens extraidos para revisao.",
    )


@router.get("/profile/resume/imports", response_class=HTMLResponse)
def resume_imports(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    return render(
        request,
        "resume_import_list.html",
        {"imports": list_import_sessions(session, limit=50)},
    )


@router.get("/profile/resume/imports/{import_key}/review", response_class=HTMLResponse)
def resume_import_review(
    request: Request,
    import_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    import_session = get_import_session(session, import_key)
    return render(
        request,
        "resume_import_review.html",
        _resume_review_context(import_session),
    )


@router.post("/profile/resume/imports/{import_key}/retry")
async def resume_import_retry(
    request: Request,
    import_key: str,
    session: Annotated[Session, Depends(get_session)],
    file: Annotated[UploadFile, UPLOAD_FILE],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    extraction_mode: Annotated[str, Form()] = "automatic",
) -> Response:
    _ = _csrf
    try:
        content = await read_limited_resume_upload(file)
        result = retry_import_session(
            session,
            import_key,
            filename=file.filename or "curriculo.pdf",
            content=content,
            extraction_mode=extraction_mode,
        )
    except RadarError as exc:
        import_session = get_import_session(session, import_key)
        return render(
            request,
            "resume_import_review.html",
            _resume_review_context(import_session, resume_error=str(exc)),
            status_code=400,
        )
    return redirect(
        f"/profile/resume/imports/{result.import_key}/review",
        message=f"Nova extracao gerou {result.candidate_count} itens para revisao.",
    )


@router.post("/profile/resume/imports/{import_key}/header")
async def resume_import_header(
    request: Request,
    import_key: str,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    profile_name: Annotated[str, Form()] = "",
    headline: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    update_import_header(
        session,
        import_key,
        profile_name=profile_name,
        headline=headline,
        summary=summary,
    )
    return redirect(
        f"/profile/resume/imports/{import_key}/review",
        message="Cabecalho salvo.",
    )


@router.post("/profile/resume/imports/{import_key}/candidates/{candidate_id}")
async def resume_import_candidate(
    request: Request,
    import_key: str,
    candidate_id: int,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = _csrf
    form = await request.form()
    import_session = get_import_session(session, import_key)
    candidate = candidate_for_session(import_session, positive_id(candidate_id, "item"))
    action = form_value(form.get("action")) or "save"
    if action == "accept":
        accept_candidate(session, import_key, candidate.id)
        message = "Item confirmado."
    elif action == "remove":
        remove_candidate(session, import_key, candidate.id)
        message = "Item removido da importacao."
    elif action == "restore":
        restore_candidate(session, import_key, candidate.id)
        message = "Item restaurado."
    elif action == "save_accept":
        update_candidate(
            session,
            import_key,
            candidate.id,
            _candidate_payload_from_form(candidate, form),
        )
        accept_candidate(session, import_key, candidate.id)
        message = "Item salvo e confirmado."
    else:
        update_candidate(
            session,
            import_key,
            candidate.id,
            _candidate_payload_from_form(candidate, form),
        )
        message = "Item salvo."
    return redirect(
        f"/profile/resume/imports/{import_key}/review#candidate-{candidate.id}",
        message=message,
    )


@router.post("/profile/resume/imports/{import_key}/confirm")
def resume_import_confirm(
    request: Request,
    import_key: str,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    activate_now: Annotated[str, Form()] = "",
    analyze_after_confirm: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = request, _csrf
    result = confirm_import(
        session,
        import_key,
        activate_now=activate_now == "on",
        analyze_jobs_after_confirm=analyze_after_confirm == "on",
    )
    message = (
        f"Perfil {result.profile.profile_name} criado a partir do curriculo."
        if result.profile.created_version
        else f"Perfil {result.profile.profile_name} reutilizado a partir do curriculo."
    )
    if result.comparisons_created or result.comparisons_reused or result.comparisons_failed:
        message = (
            f"{message} Analise: {result.comparisons_created} criadas, "
            f"{result.comparisons_reused} reutilizadas, {result.comparisons_failed} falhas."
        )
    return redirect("/profile", message=message)


@router.post("/profile/resume/imports/{import_key}/discard")
def resume_import_discard(
    request: Request,
    import_key: str,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    discard_import(session, import_key)
    return redirect("/profile/resume/imports", message="Importacao descartada.")


@router.post("/profile/resume/imports/{import_key}/purge")
def resume_import_purge(
    request: Request,
    import_key: str,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    purge_import(session, import_key)
    return redirect("/profile/resume/imports", message="Rascunho removido.")


RESUME_SECTION_ORDER = (
    ("summary", "Resumo"),
    ("experiences", "Experiencias"),
    ("projects", "Projetos"),
    ("education", "Formacao"),
    ("skills", "Habilidades"),
    ("languages", "Idiomas"),
    ("ambiguous", "Ambiguos"),
    ("warnings", "Avisos"),
)
RESUME_CANDIDATE_SECTIONS = {
    ResumeImportCandidateType.HEADLINE: "summary",
    ResumeImportCandidateType.SUMMARY: "summary",
    ResumeImportCandidateType.EXPERIENCE: "experiences",
    ResumeImportCandidateType.PROJECT: "projects",
    ResumeImportCandidateType.EDUCATION: "education",
    ResumeImportCandidateType.SKILL: "skills",
    ResumeImportCandidateType.LANGUAGE: "languages",
    ResumeImportCandidateType.AMBIGUOUS: "ambiguous",
}
RESUME_CANDIDATE_LABELS = {
    ResumeImportCandidateType.HEADLINE: "Titulo",
    ResumeImportCandidateType.SUMMARY: "Resumo",
    ResumeImportCandidateType.SKILL: "Habilidade",
    ResumeImportCandidateType.EXPERIENCE: "Experiencia",
    ResumeImportCandidateType.PROJECT: "Projeto",
    ResumeImportCandidateType.EDUCATION: "Formacao",
    ResumeImportCandidateType.LANGUAGE: "Idioma",
    ResumeImportCandidateType.AMBIGUOUS: "Ambiguo",
}
RESUME_DECISION_LABELS = {
    ResumeImportDecision.PENDING: "Pendente",
    ResumeImportDecision.ACCEPTED: "Confirmado",
    ResumeImportDecision.EDITED: "Editado",
    ResumeImportDecision.REMOVED: "Removido",
}
RESUME_EXTRACTION_MODE_LABELS = {
    "automatic": "Automatico",
    "plain": "Texto normal",
    "layout": "Layout",
    "geometric": "Geometrico",
    "native": "Nativo",
    "text": "Texto",
}
RESUME_QUALITY_LABELS = {
    "GOOD": "Boa",
    "ACCEPTABLE": "Aceitavel",
    "DEGRADED": "Degradada",
    "UNUSABLE": "Inutilizavel",
}
RESUME_RETRY_MODES = (
    ("automatic", "Automatico"),
    ("plain", "Texto normal"),
    ("layout", "Layout"),
    ("geometric", "Geometrico"),
)


def _resume_review_context(
    import_session: ResumeImportSession,
    *,
    resume_error: str | None = None,
) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = {key: [] for key, _label in RESUME_SECTION_ORDER}
    counts = {decision: 0 for decision in ResumeImportDecision}
    for candidate in import_session.candidates:
        counts[candidate.decision] += 1
        section = RESUME_CANDIDATE_SECTIONS[candidate.candidate_type]
        grouped[section].append(_candidate_row(candidate))
    warnings = json_load(import_session.warnings_json) or []
    return {
        "import_session": import_session,
        "grouped_candidates": grouped,
        "section_order": RESUME_SECTION_ORDER,
        "decision_counts": counts,
        "decision_summary": {decision.value.lower(): total for decision, total in counts.items()},
        "decision_labels": RESUME_DECISION_LABELS,
        "candidate_labels": RESUME_CANDIDATE_LABELS,
        "warnings": warnings,
        "reviewable": import_session.status.value == "REVIEWING",
        "resume_error": resume_error,
        "extraction_mode_label": RESUME_EXTRACTION_MODE_LABELS.get(
            import_session.extraction_mode,
            import_session.extraction_mode,
        ),
        "extraction_quality_label": RESUME_QUALITY_LABELS.get(
            import_session.extraction_quality,
            import_session.extraction_quality,
        ),
        "retry_modes": RESUME_RETRY_MODES,
        "show_extraction_retry": (
            import_session.source_format == "pdf"
            and import_session.extraction_quality == "DEGRADED"
            and import_session.status.value == "REVIEWING"
        ),
    }


def _candidate_row(candidate: ResumeImportCandidate) -> dict[str, object]:
    payload = (
        json_load(candidate.reviewed_payload_json)
        or json_load(candidate.original_payload_json)
        or {}
    )
    original_payload = json_load(candidate.original_payload_json) or {}
    return {
        "candidate": candidate,
        "payload": payload,
        "original_payload": original_payload,
        "label": RESUME_CANDIDATE_LABELS[candidate.candidate_type],
    }


def _candidate_payload_from_form(
    candidate: ResumeImportCandidate,
    form: FormData,
) -> dict[str, object]:
    if candidate.candidate_type == ResumeImportCandidateType.HEADLINE:
        return {"headline": form_value(form.get("headline"))}
    if candidate.candidate_type == ResumeImportCandidateType.SUMMARY:
        return {"summary": form_value(form.get("summary"))}
    if candidate.candidate_type == ResumeImportCandidateType.SKILL:
        evidence = []
        evidence_title = form_value(form.get("evidence_title"))
        if evidence_title:
            evidence.append(
                {
                    "title": evidence_title,
                    "description": form_value(form.get("evidence_description")),
                    "source_ref": form_value(form.get("evidence_source_ref")),
                    "evidence_type": form_value(form.get("evidence_type"))
                    or ProfileEvidenceType.RESUME.value,
                }
            )
        return {
            "name": form_value(form.get("name")),
            "category": form_value(form.get("category")),
            "level": form_value(form.get("level")),
            "evidence": evidence,
        }
    if candidate.candidate_type == ResumeImportCandidateType.EXPERIENCE:
        return {
            "title": form_value(form.get("title")),
            "organization": form_value(form.get("organization")),
            "start_date": form_value(form.get("start_date")),
            "end_date": form_value(form.get("end_date")),
            "description": form_value(form.get("description")),
            "skills": _split_lines(str(form.get("skills") or "")),
        }
    if candidate.candidate_type == ResumeImportCandidateType.PROJECT:
        return {
            "name": form_value(form.get("name")),
            "description": form_value(form.get("description")),
            "technologies": _split_lines(str(form.get("technologies") or "")),
            "source_ref": form_value(form.get("source_ref")),
        }
    if candidate.candidate_type == ResumeImportCandidateType.EDUCATION:
        return {
            "institution": form_value(form.get("institution")),
            "course": form_value(form.get("course")),
            "status": form_value(form.get("status")),
            "start_date": form_value(form.get("start_date")),
            "end_date": form_value(form.get("end_date")),
        }
    if candidate.candidate_type == ResumeImportCandidateType.LANGUAGE:
        return {
            "name": form_value(form.get("name")),
            "level": form_value(form.get("level")),
            "evidence": _split_lines(str(form.get("evidence") or "")),
        }
    return {"text": form_value(form.get("text"))}


@router.post("/profile/manual")
async def profile_manual(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    profile_name: Annotated[str, Form()] = "",
    headline: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    skills: Annotated[str, Form()] = "",
) -> RedirectResponse:
    _ = _csrf
    document = _manual_profile_from_form(
        await request.form(),
        profile_name=profile_name,
        headline=headline,
        summary=summary,
        skills_text=skills,
    )
    imported = create_professional_profile(session, document, activate=True)
    return redirect("/profile", message=f"Perfil {imported.profile_name} criado.")


@router.post("/profile/{version_id}/activate")
def profile_activate(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    version_id: int,
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
) -> RedirectResponse:
    _ = request, _csrf
    activate_profile_version(session, positive_id(version_id, "perfil"), source="web")
    return redirect("/profile", message="Versao ativada.")


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
    return redirect("/profile", message=message)


def _manual_profile_from_form(
    form: FormData,
    *,
    profile_name: str,
    headline: str,
    summary: str,
    skills_text: str,
) -> ProfessionalProfileInput:
    skills = _skill_inputs(form, skills_text)
    experiences = _experience_inputs(form)
    projects = _project_inputs(form)
    education = _education_inputs(form)
    languages = _language_inputs(form)
    if not skills and not (experiences or projects or education or languages):
        raise RadarError("Informe ao menos uma habilidade, experiencia, projeto, curso ou idioma.")
    try:
        return ProfessionalProfileInput(
            profile_name=profile_name.strip() or "Perfil manual",
            headline=form_value(headline),
            summary=form_value(summary),
            skills=skills,
            experiences=experiences,
            projects=projects,
            education=education,
            languages=languages,
        )
    except ValueError as exc:
        raise RadarError(f"Perfil profissional invalido: {exc}") from exc


def _skill_inputs(form: FormData, skills_text: str) -> list[SkillInput]:
    inputs_by_name: dict[str, SkillInput] = {}
    for name in _split_lines(skills_text):
        _merge_skill_input(
            inputs_by_name,
            SkillInput(name=name),
        )
    explicit_names = _values(form, "skill_name")
    categories = _values(form, "skill_category")
    levels = _values(form, "skill_level")
    evidence_titles = _values(form, "skill_evidence_title")
    evidence_descriptions = _values(form, "skill_evidence_description")
    evidence_sources = _values(form, "skill_evidence_source")
    evidence_types = _values(form, "skill_evidence_type")
    for index, name in enumerate(explicit_names):
        if not name:
            continue
        evidence: list[EvidenceInput] = []
        title = _at(evidence_titles, index)
        if title:
            raw_type = _at(evidence_types, index) or ProfileEvidenceType.SKILL.value
            evidence.append(
                EvidenceInput(
                    title=title,
                    description=_at(evidence_descriptions, index),
                    source_ref=_at(evidence_sources, index),
                    evidence_type=parse_enum_value(ProfileEvidenceType, raw_type),
                )
            )
        _merge_skill_input(
            inputs_by_name,
            SkillInput(
                name=name,
                category=_at(categories, index),
                level=_at(levels, index),
                evidence=evidence,
            ),
        )
    return list(inputs_by_name.values())


def _merge_skill_input(inputs_by_name: dict[str, SkillInput], skill: SkillInput) -> None:
    normalized = normalize_text(skill.name)
    existing = inputs_by_name.get(normalized)
    if existing is None:
        inputs_by_name[normalized] = skill
        return
    inputs_by_name[normalized] = SkillInput(
        name=skill.name or existing.name,
        category=skill.category or existing.category,
        level=skill.level or existing.level,
        evidence=[*existing.evidence, *skill.evidence],
    )


def _experience_inputs(form: FormData) -> list[ExperienceInput]:
    titles = _values(form, "experience_title")
    organizations = _values(form, "experience_organization")
    starts = _values(form, "experience_start_date")
    ends = _values(form, "experience_end_date")
    descriptions = _values(form, "experience_description")
    skills = _values(form, "experience_skills")
    return [
        ExperienceInput(
            title=title,
            organization=_at(organizations, index),
            start_date=_at(starts, index),
            end_date=_at(ends, index),
            description=_at(descriptions, index),
            skills=_split_lines(_at(skills, index) or ""),
        )
        for index, title in enumerate(titles)
        if title
    ]


def _project_inputs(form: FormData) -> list[ProjectInput]:
    names = _values(form, "project_name")
    descriptions = _values(form, "project_description")
    technologies = _values(form, "project_technologies")
    sources = _values(form, "project_source_ref")
    return [
        ProjectInput(
            name=name,
            description=_at(descriptions, index),
            technologies=_split_lines(_at(technologies, index) or ""),
            source_ref=_at(sources, index),
        )
        for index, name in enumerate(names)
        if name
    ]


def _education_inputs(form: FormData) -> list[EducationInput]:
    institutions = _values(form, "education_institution")
    courses = _values(form, "education_course")
    statuses = _values(form, "education_status")
    starts = _values(form, "education_start_date")
    ends = _values(form, "education_end_date")
    inputs: list[EducationInput] = []
    for index, institution in enumerate(institutions):
        course = _at(courses, index)
        if institution and course:
            inputs.append(
                EducationInput(
                    institution=institution,
                    course=course,
                    status=_at(statuses, index),
                    start_date=_at(starts, index),
                    end_date=_at(ends, index),
                )
            )
    return inputs


def _language_inputs(form: FormData) -> list[LanguageInput]:
    names = _values(form, "language_name")
    levels = _values(form, "language_level")
    evidence = _values(form, "language_evidence")
    inputs: list[LanguageInput] = []
    for index, name in enumerate(names):
        level = _at(levels, index)
        if name and level:
            inputs.append(
                LanguageInput(
                    name=name,
                    level=level,
                    evidence=_split_lines(_at(evidence, index) or ""),
                )
            )
    return inputs


def _values(form: FormData, name: str) -> list[str]:
    return [str(value).strip() for value in form.getlist(name) if isinstance(value, str)]


def _at(values: list[str], index: int) -> str | None:
    if index >= len(values):
        return None
    return form_value(values[index])


def _split_lines(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]
