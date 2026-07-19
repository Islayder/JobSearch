from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationStage,
    ApplicationStatus,
    CareerEventConfirmationStatus,
    CareerEventType,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    RelevanceStatus,
    RequirementMatchStatus,
    ReviewState,
    WorkModel,
)

JOB_STATUS_LABELS = {
    JobStatus.NEW: "Nova",
    JobStatus.PENDING_REVIEW: "Revisao",
    JobStatus.ELIGIBLE: "Elegivel",
    JobStatus.RECOMMENDED: "Recomendada",
    JobStatus.SEEN: "Vista",
    JobStatus.DISMISSED: "Descartada",
    JobStatus.ARCHIVED: "Arquivada",
    JobStatus.APPLIED: "Aplicada",
    JobStatus.CLOSED: "Fechada",
    JobStatus.EXPIRED: "Expirada",
}
REVIEW_STATE_LABELS = {
    ReviewState.UNREVIEWED: "Sem revisao",
    ReviewState.SEEN: "Vista",
    ReviewState.SHORTLISTED: "Favorita",
    ReviewState.DISMISSED: "Descartada",
    ReviewState.APPLIED: "Aplicada",
}
WORK_MODEL_LABELS = {
    WorkModel.REMOTE: "Remota",
    WorkModel.HYBRID: "Hibrida",
    WorkModel.ONSITE: "Presencial",
    WorkModel.UNKNOWN: "Nao informado",
}
EMPLOYMENT_TYPE_LABELS = {
    EmploymentType.INTERNSHIP: "Estagio",
    EmploymentType.TRAINEE: "Trainee",
    EmploymentType.JUNIOR: "Junior",
    EmploymentType.SCHOLARSHIP: "Bolsa",
    EmploymentType.OTHER: "Outra",
    EmploymentType.UNKNOWN: "Nao informado",
}
RELEVANCE_LABELS = {
    RelevanceStatus.CORE: "Foco",
    RelevanceStatus.ADJACENT: "Adjacente",
    RelevanceStatus.UNRELATED: "Fora do alvo",
    RelevanceStatus.MANUAL_REVIEW: "Revisao",
}
ELIGIBILITY_LABELS = {
    EligibilityStatus.ELIGIBLE: "Elegivel",
    EligibilityStatus.INELIGIBLE: "Incompativel",
    EligibilityStatus.MANUAL_REVIEW: "Revisao manual",
    EligibilityStatus.TRACK_ONLY: "Acompanhamento",
}
APPLICATION_STATUS_LABELS = {
    ApplicationStatus.PREPARING: "Preparando",
    ApplicationStatus.AWAITING_REVIEW: "Em revisao",
    ApplicationStatus.READY: "Pronta",
    ApplicationStatus.SUBMITTED: "Enviada",
    ApplicationStatus.TEST: "Teste",
    ApplicationStatus.INTERVIEW: "Entrevista",
    ApplicationStatus.FINAL_STAGE: "Final",
    ApplicationStatus.REJECTED: "Rejeitada",
    ApplicationStatus.OFFER: "Oferta",
    ApplicationStatus.WITHDRAWN: "Retirada",
    ApplicationStatus.CLOSED: "Encerrada",
}
APPLICATION_STAGE_LABELS = {
    ApplicationStage.APPLIED: "Aplicada",
    ApplicationStage.AWAITING_UPDATE: "Aguardando retorno",
    ApplicationStage.ASSESSMENT_RECEIVED: "Teste recebido",
    ApplicationStage.ASSESSMENT_COMPLETED: "Teste concluido",
    ApplicationStage.CASE_RECEIVED: "Case recebido",
    ApplicationStage.CASE_SUBMITTED: "Case enviado",
    ApplicationStage.INTERVIEW_SCHEDULED: "Entrevista marcada",
    ApplicationStage.INTERVIEW_COMPLETED: "Entrevista concluida",
    ApplicationStage.OFFER_RECEIVED: "Oferta recebida",
    ApplicationStage.REJECTED: "Rejeitada",
    ApplicationStage.WITHDRAWN: "Retirada",
}
APPLICATION_EVENT_LABELS = {
    ApplicationEventType.SUBMITTED: "Candidatura enviada",
    ApplicationEventType.CONFIRMATION_RECEIVED: "Confirmacao recebida",
    ApplicationEventType.ASSESSMENT_INVITED: "Teste recebido",
    ApplicationEventType.ASSESSMENT_COMPLETED: "Teste concluido",
    ApplicationEventType.INTERVIEW_INVITED: "Entrevista convidada",
    ApplicationEventType.INTERVIEW_COMPLETED: "Entrevista concluida",
    ApplicationEventType.CASE_RECEIVED: "Case recebido",
    ApplicationEventType.CASE_SUBMITTED: "Case enviado",
    ApplicationEventType.PROCESS_UPDATE: "Atualizacao",
    ApplicationEventType.REJECTED: "Rejeicao",
    ApplicationEventType.OFFER_RECEIVED: "Oferta",
    ApplicationEventType.WITHDRAWN: "Retirada",
    ApplicationEventType.UNKNOWN: "Outro",
}
CAREER_EVENT_LABELS = {
    CareerEventType.APPLICATION_DEADLINE: "Prazo de candidatura",
    CareerEventType.ASSESSMENT: "Teste",
    CareerEventType.ASSESSMENT_DEADLINE: "Prazo de teste",
    CareerEventType.CASE_DEADLINE: "Prazo de case",
    CareerEventType.INTERVIEW: "Entrevista",
    CareerEventType.GROUP_DYNAMICS: "Dinamica",
    CareerEventType.DOCUMENT_DEADLINE: "Prazo de documento",
    CareerEventType.OFFER_RESPONSE_DEADLINE: "Prazo de oferta",
    CareerEventType.FOLLOW_UP: "Follow-up",
    CareerEventType.CUSTOM: "Outro",
}
CAREER_STATUS_LABELS = {
    CareerEventConfirmationStatus.SUGGESTED: "Sugerido",
    CareerEventConfirmationStatus.CONFIRMED: "Confirmado",
    CareerEventConfirmationStatus.DISMISSED: "Dispensado",
    CareerEventConfirmationStatus.COMPLETED: "Concluido",
    CareerEventConfirmationStatus.CANCELLED: "Cancelado",
}
REQUIREMENT_STATUS_LABELS = {
    RequirementMatchStatus.MATCHED: "Atendido",
    RequirementMatchStatus.PARTIAL: "Parcial",
    RequirementMatchStatus.NOT_PROVEN: "Nao comprovado",
    RequirementMatchStatus.NOT_MATCHED: "Nao atende",
    RequirementMatchStatus.AMBIGUOUS: "Ambiguo",
}


def label(value: Any) -> str:
    if value is None:
        return "-"
    maps = (
        JOB_STATUS_LABELS,
        REVIEW_STATE_LABELS,
        WORK_MODEL_LABELS,
        EMPLOYMENT_TYPE_LABELS,
        RELEVANCE_LABELS,
        ELIGIBILITY_LABELS,
        APPLICATION_STATUS_LABELS,
        APPLICATION_STAGE_LABELS,
        APPLICATION_EVENT_LABELS,
        CAREER_EVENT_LABELS,
        CAREER_STATUS_LABELS,
        REQUIREMENT_STATUS_LABELS,
    )
    for mapping in maps:
        if value in mapping:
            return mapping[value]
    return str(getattr(value, "value", value)).replace("_", " ").title()


def format_datetime(value: datetime | None, timezone: str) -> str:
    if value is None:
        return "-"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")


def format_date(value: date | datetime | None, timezone: str) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return format_datetime(value, timezone).split(" ", 1)[0]
    return value.strftime("%d/%m/%Y")


def preview(value: str | None, length: int = 180) -> str:
    if not value:
        return "-"
    collapsed = " ".join(value.split())
    return collapsed if len(collapsed) <= length else f"{collapsed[: length - 1]}..."


def json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded]


def status_class(value: Any) -> str:
    raw = str(getattr(value, "value", value)).lower()
    return raw.replace("_", "-")
