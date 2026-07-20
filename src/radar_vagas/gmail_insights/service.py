from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.canonicalization.normalize import normalize_company_name, normalize_text
from radar_vagas.config.loaders import load_gmail_config
from radar_vagas.config.schemas import GmailConfig
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationMatchKind,
    ApplicationStage,
    ApplicationStatus,
    CareerEventType,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.gmail_insights.types import GmailMessage, GmailReadOnlyClient
from radar_vagas.persistence.models import Application, EmailMessage, Job

PROVIDER = "gmail"
MAX_BODY_EXCERPT = 2000


@dataclass(frozen=True)
class GmailSyncResult:
    enabled: bool
    fetched: int = 0
    imported: int = 0
    updated: int = 0
    linked: int = 0
    needs_review: int = 0
    suggestions: int = 0
    message: str = "Gmail desconectado."


@dataclass(frozen=True)
class GmailMessageRow:
    message: EmailMessage
    suggestion: dict[str, Any]


@dataclass(frozen=True)
class MessageClassification:
    event_type: ApplicationEventType
    confidence: float
    reasons: list[str]
    suggested_status: ApplicationStatus | None = None
    suggested_stage: ApplicationStage | None = None
    suggested_career_event_type: CareerEventType | None = None


@dataclass(frozen=True)
class ApplicationMatch:
    kind: ApplicationMatchKind
    confidence: float
    application: Application | None
    reasons: list[str]


def sync_gmail_application_insights(
    session: Session,
    settings: Settings,
    *,
    client: GmailReadOnlyClient | None = None,
) -> GmailSyncResult:
    config = load_gmail_config(settings.config_dir)
    if not config.enabled:
        return GmailSyncResult(enabled=False)
    selected_client = client or _configured_client(settings, config)
    messages = list(selected_client.search_messages(config.query, config.max_results))
    imported = 0
    updated = 0
    linked = 0
    needs_review = 0
    suggestions = 0
    for raw_message in messages:
        classification = classify_gmail_message(raw_message)
        match = match_application_for_message(session, raw_message)
        if match.application is not None:
            linked += 1
        if classification.event_type is not ApplicationEventType.UNKNOWN:
            suggestions += 1
        if match.kind is not ApplicationMatchKind.EXACT:
            needs_review += 1
        created = _upsert_message(
            session,
            raw_message,
            classification,
            match,
            source_query=config.query,
        )
        imported += 1 if created else 0
        updated += 0 if created else 1
    return GmailSyncResult(
        enabled=True,
        fetched=len(messages),
        imported=imported,
        updated=updated,
        linked=linked,
        needs_review=needs_review,
        suggestions=suggestions,
        message=(
            f"{len(messages)} mensagens lidas em modo somente leitura; "
            f"{suggestions} sugestoes aguardam revisao humana."
        ),
    )


def recent_gmail_messages(session: Session, *, limit: int = 50) -> list[GmailMessageRow]:
    statement = (
        select(EmailMessage)
        .options(
            selectinload(EmailMessage.application)
            .selectinload(Application.job)
            .selectinload(Job.company),
            selectinload(EmailMessage.job).selectinload(Job.company),
            selectinload(EmailMessage.company),
        )
        .where(EmailMessage.provider == PROVIDER)
        .order_by(EmailMessage.received_at.desc(), EmailMessage.id.desc())
        .limit(limit)
    )
    return [
        GmailMessageRow(message=message, suggestion=_json_object(message.suggestion_json))
        for message in session.scalars(statement).unique().all()
    ]


def classify_gmail_message(message: GmailMessage) -> MessageClassification:
    text = normalize_text(f"{message.subject} {message.body}")
    for event_type, confidence, status, stage, career_event_type, phrases in _CLASSIFICATION_RULES:
        matched = [phrase for phrase in phrases if phrase in text]
        if matched:
            return MessageClassification(
                event_type=event_type,
                confidence=confidence,
                reasons=[f"sinal encontrado: {phrase}" for phrase in matched[:3]],
                suggested_status=status,
                suggested_stage=stage,
                suggested_career_event_type=career_event_type,
            )
    return MessageClassification(
        event_type=ApplicationEventType.UNKNOWN,
        confidence=0.2,
        reasons=["nenhum sinal especifico encontrado"],
    )


def match_application_for_message(
    session: Session,
    message: GmailMessage,
) -> ApplicationMatch:
    text = normalize_text(f"{message.sender} {message.subject} {message.body}")
    candidates: list[tuple[float, Application, list[str]]] = []
    for application in _applications_for_matching(session):
        score, reasons = _application_match_score(application, text)
        if score >= 0.5:
            candidates.append((score, application, reasons))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return ApplicationMatch(
            kind=ApplicationMatchKind.UNMATCHED,
            confidence=0.0,
            application=None,
            reasons=["candidatura local nao encontrada"],
        )
    if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return ApplicationMatch(
            kind=ApplicationMatchKind.CONFLICT,
            confidence=candidates[0][0],
            application=None,
            reasons=["mais de uma candidatura local com a mesma evidência"],
        )
    score, application, reasons = candidates[0]
    kind = ApplicationMatchKind.EXACT if score >= 0.9 else ApplicationMatchKind.PROBABLE
    return ApplicationMatch(kind=kind, confidence=score, application=application, reasons=reasons)


def gmail_config_status(settings: Settings) -> tuple[GmailConfig, str]:
    config = load_gmail_config(settings.config_dir)
    if not config.enabled:
        return config, "Gmail desconectado"
    if config.credentials_path is None or config.token_path is None:
        return config, "Gmail ativado sem credenciais locais completas"
    return config, "Gmail configurado em modo somente leitura"


def _upsert_message(
    session: Session,
    message: GmailMessage,
    classification: MessageClassification,
    match: ApplicationMatch,
    *,
    source_query: str,
) -> bool:
    external_message_id = f"{PROVIDER}:{_clean_text(message.message_id, limit=220)}"
    stored = session.scalar(
        select(EmailMessage).where(EmailMessage.external_message_id == external_message_id)
    )
    created = stored is None
    if stored is None:
        stored = EmailMessage(
            external_message_id=external_message_id,
            sender=_clean_text(message.sender, limit=255),
            subject=_clean_text(message.subject, limit=500),
            received_at=_aware_utc(message.received_at),
        )
        session.add(stored)
    application = match.application
    stored.thread_id = _clean_optional_text(message.thread_id, limit=255)
    stored.provider = PROVIDER
    stored.sender = _clean_text(message.sender, limit=255)
    stored.subject = _clean_text(message.subject, limit=500)
    stored.received_at = _aware_utc(message.received_at)
    stored.body_excerpt = _body_excerpt(message.body)
    stored.classified_event_type = classification.event_type.value
    stored.classification_confidence = classification.confidence
    stored.application_id = application.id if application else None
    stored.job_id = application.job_id if application else None
    stored.company_id = application.job.company_id if application else None
    stored.suggestion_json = _suggestion_json(message, classification, match)
    stored.source_query = _clean_text(source_query, limit=500)
    stored.fetched_at = utc_now()
    session.flush()
    return created


def _configured_client(settings: Settings, config: GmailConfig) -> GmailReadOnlyClient:
    credentials_path = _private_path(settings, config.credentials_path, "credentials_path")
    token_path = _private_path(settings, config.token_path, "token_path")
    if credentials_path is None or token_path is None:
        raise RadarError(
            "Gmail esta ativado, mas credentials_path e token_path locais nao foram configurados."
        )
    from radar_vagas.gmail_insights.client import GmailApiReadOnlyClient

    return GmailApiReadOnlyClient(
        credentials_path=credentials_path,
        token_path=token_path,
        scopes=tuple(config.scopes),
    )


def _private_path(settings: Settings, value: Path | None, label: str) -> Path | None:
    if value is None:
        return None
    path = value if value.is_absolute() else PROJECT_ROOT / value
    resolved = path.resolve(strict=False)
    project_root = PROJECT_ROOT.resolve(strict=False)
    try:
        relative = resolved.relative_to(project_root)
    except ValueError:
        return resolved
    if len(relative.parts) >= 2 and relative.parts[:2] == ("data", "personal"):
        return resolved
    if resolved.is_relative_to(settings.config_dir.resolve(strict=False)):
        raise RadarError(f"{label} do Gmail nao deve ficar em config versionavel.")
    raise RadarError(
        f"{label} do Gmail deve ficar fora do Git, por exemplo em data/personal/gmail."
    )


def _applications_for_matching(session: Session) -> list[Application]:
    statement = (
        select(Application)
        .options(selectinload(Application.job).selectinload(Job.company))
        .order_by(Application.updated_at.desc(), Application.id.desc())
    )
    return list(session.scalars(statement).unique().all())


def _application_match_score(
    application: Application,
    normalized_text: str,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    if (
        application.external_reference
        and normalize_text(application.external_reference) in normalized_text
    ):
        score += 0.95
        reasons.append("referencia externa encontrada")
    if (
        application.application_url
        and normalize_text(application.application_url) in normalized_text
    ):
        score += 0.92
        reasons.append("url da candidatura encontrada")
    company = application.job.company
    normalized_company = normalize_company_name(company.canonical_name)
    if normalized_company and normalized_company in normalized_text:
        score += 0.35
        reasons.append("empresa encontrada")
    title_terms = [
        term for term in normalize_text(application.job.canonical_title).split() if len(term) >= 4
    ]
    matched_title_terms = [term for term in title_terms if term in normalized_text]
    if title_terms and matched_title_terms:
        ratio = len(matched_title_terms) / len(title_terms)
        score += min(0.4, ratio * 0.4)
        reasons.append("titulo da vaga parcialmente encontrado")
    if application.platform and normalize_text(application.platform) in normalized_text:
        score += 0.1
        reasons.append("plataforma encontrada")
    return min(score, 0.99), reasons


def _suggestion_json(
    message: GmailMessage,
    classification: MessageClassification,
    match: ApplicationMatch,
) -> str:
    application = match.application
    payload: dict[str, Any] = {
        "source": "gmail_read_only",
        "requires_human_confirmation": True,
        "event_type": classification.event_type.value,
        "suggested_application_status": (
            classification.suggested_status.value if classification.suggested_status else None
        ),
        "suggested_application_stage": (
            classification.suggested_stage.value if classification.suggested_stage else None
        ),
        "suggested_career_event_type": (
            classification.suggested_career_event_type.value
            if classification.suggested_career_event_type
            else None
        ),
        "suggested_event_start": "nao encontrado",
        "confidence": classification.confidence,
        "classification_reasons": classification.reasons,
        "match_kind": match.kind.value,
        "match_confidence": match.confidence,
        "match_reasons": match.reasons,
        "application_id": application.id if application else None,
        "job_id": application.job_id if application else None,
        "message_fingerprint": _message_fingerprint(message),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _message_fingerprint(message: GmailMessage) -> str:
    payload = {
        "provider": PROVIDER,
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "subject": message.subject,
        "received_at": _aware_utc(message.received_at).isoformat(),
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _body_excerpt(value: str) -> str:
    text = _clean_text(value, limit=MAX_BODY_EXCERPT)
    return text


def _clean_text(value: str, *, limit: int) -> str:
    collapsed = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    collapsed = " ".join(collapsed.split())
    return collapsed[:limit]


def _clean_optional_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    text = _clean_text(value, limit=limit)
    return text or None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


_CLASSIFICATION_RULES = (
    (
        ApplicationEventType.OFFER_RECEIVED,
        0.9,
        ApplicationStatus.OFFER,
        ApplicationStage.OFFER_RECEIVED,
        CareerEventType.OFFER_RESPONSE_DEADLINE,
        ("oferta", "offer", "proposta de contratacao", "carta proposta"),
    ),
    (
        ApplicationEventType.REJECTED,
        0.9,
        ApplicationStatus.REJECTED,
        ApplicationStage.REJECTED,
        None,
        (
            "infelizmente",
            "nao seguiremos",
            "nao avancaremos",
            "nao foi selecionad",
            "rejeicao",
            "rejected",
        ),
    ),
    (
        ApplicationEventType.INTERVIEW_INVITED,
        0.86,
        ApplicationStatus.INTERVIEW,
        ApplicationStage.INTERVIEW_SCHEDULED,
        CareerEventType.INTERVIEW,
        ("entrevista", "interview", "bate papo", "conversa com", "agenda uma conversa"),
    ),
    (
        ApplicationEventType.CASE_RECEIVED,
        0.84,
        ApplicationStatus.TEST,
        ApplicationStage.CASE_RECEIVED,
        CareerEventType.CASE_DEADLINE,
        ("case", "desafio tecnico", "desafio pratico"),
    ),
    (
        ApplicationEventType.ASSESSMENT_INVITED,
        0.84,
        ApplicationStatus.TEST,
        ApplicationStage.ASSESSMENT_RECEIVED,
        CareerEventType.ASSESSMENT,
        ("teste", "assessment", "avaliacao online", "prova online"),
    ),
    (
        ApplicationEventType.CONFIRMATION_RECEIVED,
        0.72,
        ApplicationStatus.SUBMITTED,
        ApplicationStage.AWAITING_UPDATE,
        None,
        ("recebemos sua candidatura", "candidatura recebida", "application received"),
    ),
    (
        ApplicationEventType.PROCESS_UPDATE,
        0.6,
        ApplicationStatus.SUBMITTED,
        ApplicationStage.AWAITING_UPDATE,
        None,
        ("processo seletivo", "atualizacao do processo", "convite"),
    ),
)
