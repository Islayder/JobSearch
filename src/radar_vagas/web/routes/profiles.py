from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.config.loaders import load_ui_config, write_ui_local_config
from radar_vagas.config.schemas import UiConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import ProfileEvidenceType, parse_enum_value
from radar_vagas.domain.errors import RadarError
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
from radar_vagas.web.dependencies import get_session, get_settings
from radar_vagas.web.queries import active_profile_version, profile_versions
from radar_vagas.web.routes.common import redirect, render
from radar_vagas.web.security import (
    csrf_protect,
    form_value,
    positive_id,
    read_limited_profile_upload,
)

router = APIRouter()


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
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    file: Annotated[UploadFile, File()] | None = None,
    timezone: Annotated[str, Form()] = "America/Sao_Paulo",
) -> RedirectResponse:
    _ = request, _csrf
    if file is None:
        raise HTTPException(status_code=400, detail="Arquivo nao enviado.")
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
    _csrf: Annotated[None, Depends(csrf_protect)] = None,
    file: Annotated[UploadFile, File()] | None = None,
) -> RedirectResponse:
    _ = request, _csrf
    if file is None:
        raise HTTPException(status_code=400, detail="Arquivo nao enviado.")
    content = await read_limited_profile_upload(file)
    imported = import_professional_profile_bytes(
        session,
        content,
        filename=file.filename or "profile.txt",
        activate=True,
    )
    return redirect("/profile", message=f"Perfil {imported.profile_name} importado.")


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
