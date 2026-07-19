from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.canonicalization.normalize import normalize_company_name
from radar_vagas.domain.enums import (
    ApplicationStage,
    ApplicationStatus,
    ReadableEnum,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.models import Application, Company, Job

APPLICATION_SHORTCUTS = {
    "waiting-update": "Aguardando retorno",
    "test-case": "Teste ou case",
    "interview": "Entrevistas",
    "offer": "Ofertas",
    "rejected": "Rejeitadas",
    "withdrawn": "Retiradas",
}


@dataclass(frozen=True)
class ApplicationFilters:
    company: str | None = None
    status: ApplicationStatus | None = None
    stage: ApplicationStage | None = None
    platform: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    shortcut: str | None = None


def parse_application_filters(raw: dict[str, str | None]) -> ApplicationFilters:
    shortcut = _text(raw.get("shortcut"))
    if shortcut and shortcut not in APPLICATION_SHORTCUTS:
        raise RadarError("Atalho de candidatura invalido.")
    return ApplicationFilters(
        company=_text(raw.get("company")),
        status=_enum(ApplicationStatus, raw.get("status"), "status da candidatura"),
        stage=_enum(ApplicationStage, raw.get("stage"), "etapa da candidatura"),
        platform=_text(raw.get("platform")),
        from_date=_date(raw.get("from_date"), "periodo inicial"),
        to_date=_date(raw.get("to_date"), "periodo final"),
        shortcut=shortcut,
    )


def applications_list(session: Session, *, filters: ApplicationFilters) -> list[Application]:
    statement = (
        select(Application)
        .join(Job)
        .join(Company)
        .options(
            selectinload(Application.job).selectinload(Job.company),
            selectinload(Application.events),
            selectinload(Application.career_events),
        )
        .order_by(Application.updated_at.desc(), Application.id.desc())
    )
    if filters.company:
        statement = statement.where(
            Company.normalized_name.contains(normalize_company_name(filters.company))
        )
    if filters.status:
        statement = statement.where(Application.status == filters.status)
    if filters.stage:
        statement = statement.where(Application.stage == filters.stage)
    if filters.platform:
        statement = statement.where(
            func.lower(Application.platform).contains(filters.platform.lower())
        )
    if filters.from_date:
        statement = statement.where(Application.applied_at >= filters.from_date)
    if filters.to_date:
        statement = statement.where(Application.applied_at <= filters.to_date)
    statement = _apply_shortcut(statement, filters.shortcut)
    return list(session.scalars(statement).unique().all())


def application_detail(session: Session, application_id: int) -> Application | None:
    return session.scalar(
        select(Application)
        .options(
            selectinload(Application.job).selectinload(Job.company),
            selectinload(Application.events),
            selectinload(Application.career_events),
        )
        .where(Application.id == application_id)
    )


def _apply_shortcut(statement, shortcut: str | None):  # type: ignore[no-untyped-def]
    if shortcut == "waiting-update":
        return statement.where(Application.stage == ApplicationStage.AWAITING_UPDATE)
    if shortcut == "test-case":
        return statement.where(
            Application.stage.in_(
                [
                    ApplicationStage.ASSESSMENT_RECEIVED,
                    ApplicationStage.ASSESSMENT_COMPLETED,
                    ApplicationStage.CASE_RECEIVED,
                    ApplicationStage.CASE_SUBMITTED,
                ]
            )
        )
    if shortcut == "interview":
        return statement.where(
            Application.stage.in_(
                [ApplicationStage.INTERVIEW_SCHEDULED, ApplicationStage.INTERVIEW_COMPLETED]
            )
        )
    if shortcut == "offer":
        return statement.where(Application.status == ApplicationStatus.OFFER)
    if shortcut == "rejected":
        return statement.where(Application.status == ApplicationStatus.REJECTED)
    if shortcut == "withdrawn":
        return statement.where(Application.status == ApplicationStatus.WITHDRAWN)
    return statement


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _enum[EnumType: ReadableEnum](
    enum_type: type[EnumType],
    value: str | None,
    label: str,
) -> EnumType | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return parse_enum_value(enum_type, text)
    except ValueError as exc:
        raise RadarError(f"Filtro invalido para {label}: {exc}") from exc


def _date(value: str | None, label: str) -> datetime | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=UTC)
    except ValueError as exc:
        raise RadarError(f"Data invalida para {label}.") from exc
