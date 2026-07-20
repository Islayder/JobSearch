from __future__ import annotations

from radar_vagas.domain.errors import RadarError
from radar_vagas.resume_import.docx import extract_docx_document
from radar_vagas.resume_import.models import ExtractedDocument
from radar_vagas.resume_import.pdf import extract_pdf_document
from radar_vagas.resume_import.sections import assign_sections
from radar_vagas.resume_import.security import ResumeUpload, validate_resume_upload
from radar_vagas.resume_import.text import extract_text_document


def extract_resume(
    filename: str,
    content: bytes,
    *,
    extraction_mode: str = "automatic",
) -> tuple[ResumeUpload, ExtractedDocument]:
    upload = validate_resume_upload(filename, content)
    if upload.source_format == "pdf":
        document = extract_pdf_document(upload.content, extraction_mode=extraction_mode)
    elif upload.source_format == "docx":
        _reject_manual_extraction_mode(extraction_mode, upload.source_format)
        document = extract_docx_document(upload.content)
    elif upload.source_format in {"txt", "markdown"}:
        _reject_manual_extraction_mode(extraction_mode, upload.source_format)
        document = extract_text_document(upload.content, source_format=upload.source_format)
    else:
        raise RadarError("Formato de curriculo nao suportado.")
    return upload, assign_sections(document)


def _reject_manual_extraction_mode(extraction_mode: str, source_format: str) -> None:
    if extraction_mode.strip().lower() not in {"", "automatic", "auto"}:
        raise RadarError(
            f"Modo de extracao manual esta disponivel apenas para PDF, nao {source_format}."
        )
