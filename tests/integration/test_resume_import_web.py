from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import ResumeImportCandidateType
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.models import ProfessionalProfileVersion, ResumeImportSession
from radar_vagas.web.app import create_app

_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_CREATE_CONNECTION = socket.create_connection


@pytest.fixture(autouse=True)
def allow_testclient_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    def guarded_connect(instance: socket.socket, address: object) -> object:
        if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1"}:
            return _ORIGINAL_CONNECT(instance, address)
        raise AssertionError("Testes nao podem acessar rede real.")

    def guarded_create_connection(
        address: object,
        *args: object,
        **kwargs: object,
    ) -> socket.socket:
        if isinstance(address, tuple) and address and address[0] in {"127.0.0.1", "::1"}:
            return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)
        raise AssertionError("Testes nao podem acessar rede real.")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


def test_resume_import_web_review_and_confirm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with TestClient(create_app(settings)) as client:
        upload_page = client.get("/profile/resume/import")
        assert upload_page.status_code == 200
        assert "PDF textual, DOCX, TXT ou Markdown" in upload_page.text

        uploaded = client.post(
            "/profile/resume/import",
            data={"csrf_token": _csrf(upload_page.text)},
            files={"file": ("curriculo.md", _resume_markdown(), "text/markdown")},
            follow_redirects=False,
        )
        assert uploaded.status_code == 303
        review_path = uploaded.headers["location"].split("?", 1)[0]
        assert review_path.startswith("/profile/resume/imports/")

        review_page = client.get(review_path)
        assert review_page.status_code == 200
        assert "Confirmar e criar perfil" in review_page.text

        with session_scope(settings) as session:
            import_session = session.scalar(select(ResumeImportSession))
            assert import_session is not None
            skill = next(
                candidate
                for candidate in import_session.candidates
                if candidate.candidate_type == ResumeImportCandidateType.SKILL
            )
            import_key = import_session.import_key
            skill_id = skill.id

        saved = client.post(
            f"/profile/resume/imports/{import_key}/candidates/{skill_id}",
            data={
                "csrf_token": _csrf(review_page.text),
                "action": "save_accept",
                "name": "Python",
                "category": "dados",
                "level": "intermediario",
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303

        review_page = client.get(review_path)
        confirmed = client.post(
            f"/profile/resume/imports/{import_key}/confirm",
            data={
                "csrf_token": _csrf(review_page.text),
                "activate_now": "on",
            },
            follow_redirects=False,
        )
        assert confirmed.status_code == 303
        assert confirmed.headers["location"].startswith("/profile")

        profile_page = client.get("/profile")
        assert profile_page.status_code == 200
        assert "Perfil importado - curriculo" in profile_page.text

    with session_scope(settings) as session:
        version = session.scalar(select(ProfessionalProfileVersion))
        assert version is not None
        assert version.is_active is True
        assert version.source_format == "markdown"


def _resume_markdown() -> bytes:
    return b"""
# Perfil
Analista de dados junior
## Experiencia
Estagiario de Dados - Empresa X - 2024 - atual: Consultas SQL e Python.
## Habilidades
Python, SQL
"""


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if match is None:
        raise AssertionError("token CSRF nao encontrado")
    return match.group(1)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'radar.sqlite3'}",
        config_dir=tmp_path / "config",
    )
