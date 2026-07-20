from __future__ import annotations

import re
import socket
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import ResumeImportCandidateType, ResumeImportStatus
from radar_vagas.domain.time import utc_now
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import (
    ProfessionalProfileVersion,
    ResumeImportSession,
)
from radar_vagas.resume_import.repository import json_dump
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


def test_resume_import_web_degraded_pdf_retry_requires_post_and_same_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    content = b"%PDF-1.4\nsynthetic"
    import_key = "retry-web"
    with session_scope(settings) as session:
        session.add(
            ResumeImportSession(
                import_key=import_key,
                source_format="pdf",
                sanitized_filename="curriculo.pdf",
                content_hash=sha256(content).hexdigest(),
                status=ResumeImportStatus.REVIEWING,
                profile_name="Perfil importado - curriculo",
                page_count=1,
                extracted_character_count=220,
                extraction_mode="automatic",
                extraction_quality="DEGRADED",
                extraction_metrics_json="{}",
                warnings_json=json_dump(
                    [
                        "O PDF possui texto, mas sua estrutura e seus espacos nao puderam "
                        "ser reconstruidos com seguranca."
                    ]
                ),
                candidate_count=0,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    _patch_pdf_reader(
        monkeypatch,
        [
            _FakePdfPage(
                plain=_glued_resume_text(),
                layout=_glued_resume_text(),
                fragments=_resume_fragments(),
            )
        ],
    )

    with TestClient(create_app(settings)) as client:
        review_page = client.get(f"/profile/resume/imports/{import_key}/review")
        assert review_page.status_code == 200
        assert "Tentar outro modo de extracao" in review_page.text

        get_retry = client.get(f"/profile/resume/imports/{import_key}/retry")
        assert get_retry.status_code == 405

        retried = client.post(
            f"/profile/resume/imports/{import_key}/retry",
            data={"csrf_token": _csrf(review_page.text), "extraction_mode": "geometric"},
            files={"file": ("curriculo.pdf", content, "application/pdf")},
            follow_redirects=False,
        )
        assert retried.status_code == 303

    with session_scope(settings) as session:
        import_session = session.scalar(select(ResumeImportSession))
        assert import_session is not None
        assert import_session.extraction_mode == "geometric"
        assert import_session.extraction_quality in {"GOOD", "ACCEPTABLE"}
        assert any(
            candidate.candidate_type == ResumeImportCandidateType.EXPERIENCE
            for candidate in import_session.candidates
        )


def _resume_markdown() -> bytes:
    return b"""
# Perfil
Analista de dados junior
## Experiencia
Estagiario de Dados - Empresa X - 2024 - atual: Consultas SQL e Python.
## Habilidades
Python, SQL
"""


def _glued_resume_text() -> str:
    return (
        "PessoaFicticiaCidadeFicticiaBrasil "
        "EmbuscadeoportunidadedeEstagioemAnalisededadosBIouAnalyticsaplicando"
        "ExcelSQLPowerBIPythonePowerQuerynaorganizacaonotratamentonaanalise"
        "ValidacaodedadosregrasdenegociotestesdeAPIseinvestigacaodeinconsistencias"
        "Formacaoembancosdedadosestruturasdedadosmodelagemdesistemasedesenvolvimento"
    )


def _resume_fragments() -> tuple[tuple[str, float, float, float], ...]:
    return (
        ("Analista de Dados", 72, 740, 13),
        ("Resumo", 72, 710, 14),
        ("Em busca de oportunidade de estagio em Analise de Dados, BI ou Analytics.", 72, 690, 12),
        ("Experiencia", 72, 660, 14),
        ("Estagiaria de Dados - Empresa Sintetica", 72, 640, 12),
        ("2025 - atual", 72, 622, 12),
        ("- Levantamento e analise de requisitos com fluxos de dados.", 86, 604, 12),
        ("Habilidades", 72, 420, 14),
        ("Excel, SQL, Power BI, Python e Power Query", 72, 400, 12),
    )


class _FakePdfPage:
    def __init__(
        self,
        *,
        plain: str,
        layout: str,
        fragments: Sequence[tuple[str, float, float, float]],
    ) -> None:
        self.plain = plain
        self.layout = layout
        self.fragments = tuple(fragments)

    def extract_text(self, *args: Any, **kwargs: Any) -> str:
        _ = args
        visitor = kwargs.get("visitor_text")
        if callable(visitor):
            for text, x, y, font_size in self.fragments:
                visitor(text, (1, 0, 0, 1, 0, 0), (1, 0, 0, 1, x, y), {}, font_size)
            return ""
        if kwargs.get("extraction_mode") == "layout":
            return self.layout
        return self.plain


class _FakePdfReader:
    def __init__(self, pages: Sequence[_FakePdfPage]) -> None:
        self.is_encrypted = False
        self.pages = list(pages)


def _patch_pdf_reader(
    monkeypatch: pytest.MonkeyPatch,
    pages: Sequence[_FakePdfPage],
) -> None:
    import pypdf

    monkeypatch.setattr(pypdf, "PdfReader", lambda _content: _FakePdfReader(pages))


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
