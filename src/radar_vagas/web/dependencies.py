from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from radar_vagas.config.settings import Settings


def get_settings(request: Request) -> Settings:
    settings = request.app.state.radar_settings
    if not isinstance(settings, Settings):
        raise RuntimeError("Settings da interface nao inicializado.")
    return settings


def get_session(request: Request) -> Generator[Session]:
    factory = request.app.state.radar_session_factory
    if not isinstance(factory, sessionmaker):
        raise RuntimeError("Sessao da interface nao inicializada.")
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
