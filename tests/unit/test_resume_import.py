from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from pypdf import PdfWriter
from sqlalchemy import select

from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ResumeImportCandidateType,
    ResumeImportDecision,
    ResumeImportStatus,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import ProfessionalProfileVersion, ResumeImportSession
from radar_vagas.resume_import.extraction import extract_resume
from radar_vagas.resume_import.security import validate_resume_upload
from radar_vagas.resume_import.service import (
    accept_candidate,
    confirm_import,
    create_import_session,
    discard_import,
    get_import_session,
)


def test_security_rejects_old_doc_and_external_docx_relationship() -> None:
    with pytest.raises(RadarError, match="\\.doc antigo"):
        validate_resume_upload("curriculo.doc", b"legacy")

    content = BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        archive.writestr(
            "word/_rels/document.xml.rels",
            '<Relationships><Relationship TargetMode="External" Target="https://example.test"/></Relationships>',
        )

    with pytest.raises(RadarError, match="referencias externas"):
        validate_resume_upload("curriculo.docx", content.getvalue())


def test_pdf_without_text_gets_human_scanned_message() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    content = BytesIO()
    writer.write(content)

    with pytest.raises(
        RadarError, match="Não foi possível encontrar texto suficiente neste PDF\\."
    ):
        extract_resume("curriculo.pdf", content.getvalue())


def test_resume_session_requires_review_before_profile_and_confirms_with_provenance(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    content = _resume_markdown()

    with session_scope(settings) as session:
        result = create_import_session(session, filename="curriculo.md", content=content)
        import_session = get_import_session(session, result.import_key)
        assert import_session.status == ResumeImportStatus.REVIEWING
        assert session.scalar(select(ProfessionalProfileVersion.id)) is None
        assert not any(
            "islayder@example.com" in c.original_payload_json for c in import_session.candidates
        )

        for candidate in import_session.candidates:
            if candidate.candidate_type in {
                ResumeImportCandidateType.SKILL,
                ResumeImportCandidateType.EXPERIENCE,
                ResumeImportCandidateType.PROJECT,
            }:
                accept_candidate(session, import_session.import_key, candidate.id)

        confirmed = confirm_import(session, import_session.import_key, activate_now=True)
        profile_version = session.get(
            ProfessionalProfileVersion, confirmed.profile.profile_version_id
        )
        assert profile_version is not None
        assert profile_version.is_active is True
        assert profile_version.source_format == "markdown"
        assert "resume_import" in profile_version.raw_profile_json
        assert "islayder@example.com" not in profile_version.raw_profile_json
        assert import_session.status == ResumeImportStatus.CONFIRMED
        assert import_session.confirmed_profile_version_id == profile_version.id

        repeated = confirm_import(session, import_session.import_key, activate_now=True)
        assert repeated.profile.profile_version_id == confirmed.profile.profile_version_id
        assert repeated.profile.created_version is False


def test_docx_import_can_be_discarded_without_profile(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)

    with session_scope(settings) as session:
        created = create_import_session(session, filename="curriculo.docx", content=_resume_docx())
        import_session = discard_import(session, created.import_key)
        assert import_session.status == ResumeImportStatus.DISCARDED
        assert all(
            candidate.decision == ResumeImportDecision.PENDING
            for candidate in import_session.candidates
        )
        assert session.scalar(select(ProfessionalProfileVersion.id)) is None
        assert session.scalar(select(ResumeImportSession.id)) == import_session.id


def _resume_markdown() -> bytes:
    return b"""
# Perfil
Analista de dados junior

## Resumo
Estudante de dados com experiencia em dashboards e consultas SQL.

## Experiencia
Estagiario de Dados - Empresa X - 2024 - atual: Dashboards em Power BI, SQL e Python.

## Projetos
Dashboard de Vendas - Analise de indicadores com Python, SQL e Power BI.

## Habilidades
Python, SQL, Power BI

## Formacao
PUC Minas - Ciencia de Dados, cursando 2026

## Idiomas
Ingles intermediario

Contato: islayder@example.com
"""


def _resume_docx() -> bytes:
    document = Document()
    document.add_heading("Resumo", level=1)
    document.add_paragraph("Analista de dados com Python e SQL.")
    document.add_heading("Experiencia", level=1)
    document.add_paragraph(
        "Estagiario de Dados - Empresa X - 2024 - atual: Dashboards em Power BI."
    )
    document.add_heading("Habilidades", level=1)
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Python"
    table.rows[0].cells[1].text = "SQL"
    content = BytesIO()
    document.save(content)
    return content.getvalue()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'radar.sqlite3'}",
        config_dir=tmp_path / "config",
    )
