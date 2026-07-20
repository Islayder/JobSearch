from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    href: str
    icon: str
    section: str = "primary"


@dataclass(frozen=True)
class Breadcrumb:
    label: str
    href: str | None = None


@dataclass(frozen=True)
class PageAction:
    label: str
    href: str
    icon: str = "arrow-right"


@dataclass(frozen=True)
class PageChrome:
    title: str
    description: str
    active_key: str
    breadcrumbs: tuple[Breadcrumb, ...]
    primary_action: PageAction | None


NAV_ITEMS = (
    NavItem("dashboard", "Visao geral", "/", "dashboard"),
    NavItem("jobs", "Vagas", "/jobs", "briefcase"),
    NavItem("applications", "Candidaturas", "/applications", "send"),
    NavItem("agenda", "Agenda", "/agenda", "calendar"),
    NavItem("profile", "Perfil profissional", "/profile", "user"),
    NavItem("sources", "Fontes", "/sources", "database"),
    NavItem("resume-import", "Importar curriculo", "/profile/resume/import", "upload", "secondary"),
    NavItem("gmail", "Gmail", "/gmail", "mail", "secondary"),
    NavItem("settings", "Configuracoes", "/settings", "settings", "secondary"),
)


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
        "nav_items": NAV_ITEMS,
        "page_chrome": _page_chrome(request.url.path, context),
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


def _page_chrome(path: str, context: dict[str, Any]) -> PageChrome:
    if path == "/":
        return PageChrome(
            title="Visao geral",
            description="Fila diaria de revisao, candidaturas, agenda e saude das fontes.",
            active_key="dashboard",
            breadcrumbs=(Breadcrumb("Visao geral"),),
            primary_action=PageAction("Revisar vagas", "/jobs?tab=aguardando-revisao", "check"),
        )
    if path == "/onboarding":
        return PageChrome(
            title="Primeiro acesso",
            description="Configure seu perfil local antes de analisar vagas.",
            active_key="profile",
            breadcrumbs=(Breadcrumb("Perfil", "/profile"), Breadcrumb("Primeiro acesso")),
            primary_action=PageAction(
                "Importar curriculo PDF ou DOCX",
                "/profile/resume/import",
                "upload",
            ),
        )
    if path == "/jobs":
        return PageChrome(
            title="Vagas",
            description="Revise, filtre e acompanhe oportunidades encontradas localmente.",
            active_key="jobs",
            breadcrumbs=(Breadcrumb("Vagas"),),
            primary_action=PageAction("Ver recomendadas", "/jobs?tab=recomendadas", "star"),
        )
    if path.startswith("/jobs/"):
        job = context.get("job")
        title = str(getattr(job, "canonical_title", "Detalhe da vaga"))
        return PageChrome(
            title=title,
            description="Detalhe da oportunidade, acoes manuais e compatibilidade.",
            active_key="jobs",
            breadcrumbs=(Breadcrumb("Vagas", "/jobs"), Breadcrumb("Detalhes"), Breadcrumb(title)),
            primary_action=None,
        )
    if path == "/applications":
        return PageChrome(
            title="Candidaturas",
            description="Acompanhamento manual dos processos que voce registrou.",
            active_key="applications",
            breadcrumbs=(Breadcrumb("Candidaturas"),),
            primary_action=PageAction("Ver vagas", "/jobs", "briefcase"),
        )
    if path.startswith("/applications/"):
        application = context.get("application")
        job = getattr(application, "job", None)
        title = str(getattr(job, "canonical_title", "Candidatura"))
        return PageChrome(
            title=title,
            description="Timeline, etapa atual, compromissos e atualizacoes do processo.",
            active_key="applications",
            breadcrumbs=(
                Breadcrumb("Candidaturas", "/applications"),
                Breadcrumb("Acompanhar"),
                Breadcrumb(title),
            ),
            primary_action=None,
        )
    if path == "/agenda":
        agenda = context.get("agenda")
        month_label = str(getattr(agenda, "month_label", "Agenda"))
        return PageChrome(
            title=month_label,
            description="Compromissos, prazos e follow-ups ligados a vagas e candidaturas.",
            active_key="agenda",
            breadcrumbs=(Breadcrumb("Agenda"), Breadcrumb(month_label)),
            primary_action=PageAction("Novo compromisso", "#new-event", "plus"),
        )
    if path.startswith("/agenda/events/"):
        return PageChrome(
            title="Editar compromisso",
            description="Atualize dados locais do evento sem enviar notificacoes externas.",
            active_key="agenda",
            breadcrumbs=(Breadcrumb("Agenda", "/agenda"), Breadcrumb("Editar compromisso")),
            primary_action=None,
        )
    if path == "/profile":
        return PageChrome(
            title="Perfil profissional",
            description="Versoes locais usadas na analise de compatibilidade.",
            active_key="profile",
            breadcrumbs=(Breadcrumb("Perfil profissional"),),
            primary_action=PageAction("Importar curriculo", "/profile/resume/import", "upload"),
        )
    if path == "/profile/resume/import":
        return PageChrome(
            title="Importar curriculo",
            description="Extraia PDF textual, DOCX, TXT ou Markdown para revisao humana.",
            active_key="resume-import",
            breadcrumbs=(Breadcrumb("Perfil", "/profile"), Breadcrumb("Importar curriculo")),
            primary_action=PageAction("Ver rascunhos", "/profile/resume/imports", "folder"),
        )
    if path == "/profile/resume/imports":
        return PageChrome(
            title="Rascunhos de curriculo",
            description="Importacoes locais aguardando revisao, confirmadas ou descartadas.",
            active_key="resume-import",
            breadcrumbs=(Breadcrumb("Perfil", "/profile"), Breadcrumb("Rascunhos")),
            primary_action=PageAction("Novo importador", "/profile/resume/import", "upload"),
        )
    if path.startswith("/profile/resume/imports/"):
        return PageChrome(
            title="Revisar curriculo",
            description="Confira os itens extraidos antes de criar uma nova versao de perfil.",
            active_key="resume-import",
            breadcrumbs=(
                Breadcrumb("Perfil", "/profile"),
                Breadcrumb("Importacoes", "/profile/resume/imports"),
                Breadcrumb("Revisao"),
            ),
            primary_action=None,
        )
    if path == "/sources":
        return PageChrome(
            title="Fontes",
            description="Saude local de boards, consultas e execucoes recentes.",
            active_key="sources",
            breadcrumbs=(Breadcrumb("Fontes"),),
            primary_action=None,
        )
    if path == "/gmail":
        return PageChrome(
            title="Gmail",
            description="Mensagens lidas localmente em modo somente leitura.",
            active_key="gmail",
            breadcrumbs=(Breadcrumb("Gmail"),),
            primary_action=None,
        )
    return PageChrome(
        title="Radar de Vagas",
        description="Interface local para organizar sua busca profissional.",
        active_key="settings" if path.startswith("/settings") else "dashboard",
        breadcrumbs=(Breadcrumb("Radar de Vagas"),),
        primary_action=None,
    )
