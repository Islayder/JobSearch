from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.models import ResumeImportCandidate, ResumeImportSession


def get_import_session(session: Session, import_key: str) -> ResumeImportSession:
    import_session = session.scalar(
        select(ResumeImportSession)
        .where(ResumeImportSession.import_key == import_key)
        .options(selectinload(ResumeImportSession.candidates))
    )
    if import_session is None:
        raise RadarError("Importacao de curriculo nao encontrada.")
    return import_session


def list_import_sessions(session: Session, *, limit: int = 20) -> list[ResumeImportSession]:
    return list(
        session.scalars(
            select(ResumeImportSession)
            .options(selectinload(ResumeImportSession.candidates))
            .order_by(ResumeImportSession.created_at.desc(), ResumeImportSession.id.desc())
            .limit(limit)
        ).all()
    )


def candidate_for_session(
    import_session: ResumeImportSession,
    candidate_id: int,
) -> ResumeImportCandidate:
    for candidate in import_session.candidates:
        if candidate.id == candidate_id:
            return candidate
    raise RadarError("Item de revisao nao pertence a esta importacao.")


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_load(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)
