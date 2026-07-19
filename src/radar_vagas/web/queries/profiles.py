from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.persistence.models import ProfessionalProfileVersion


def active_profile_version(session: Session) -> ProfessionalProfileVersion | None:
    return session.scalar(
        select(ProfessionalProfileVersion)
        .options(selectinload(ProfessionalProfileVersion.profile))
        .where(ProfessionalProfileVersion.is_active.is_(True))
        .order_by(
            ProfessionalProfileVersion.created_at.desc(),
            ProfessionalProfileVersion.id.desc(),
        )
    )


def profile_versions(session: Session) -> list[ProfessionalProfileVersion]:
    return list(
        session.scalars(
            select(ProfessionalProfileVersion)
            .options(
                selectinload(ProfessionalProfileVersion.profile),
                selectinload(ProfessionalProfileVersion.skills),
                selectinload(ProfessionalProfileVersion.evidences),
                selectinload(ProfessionalProfileVersion.experiences),
                selectinload(ProfessionalProfileVersion.projects),
                selectinload(ProfessionalProfileVersion.education),
                selectinload(ProfessionalProfileVersion.languages),
            )
            .order_by(
                ProfessionalProfileVersion.is_active.desc(),
                ProfessionalProfileVersion.created_at.desc(),
                ProfessionalProfileVersion.id.desc(),
            )
        ).all()
    )
