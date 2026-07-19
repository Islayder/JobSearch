from __future__ import annotations

from radar_vagas.domain.errors import RadarError
from radar_vagas.resume_import.docx import extract_docx_document
from radar_vagas.resume_import.models import ExtractedDocument
from radar_vagas.resume_import.pdf import extract_pdf_document
from radar_vagas.resume_import.sections import assign_sections
from radar_vagas.resume_import.security import ResumeUpload, validate_resume_upload
from radar_vagas.resume_import.text import extract_text_document


def extract_resume(filename: str, content: bytes) -> tuple[ResumeUpload, ExtractedDocument]:
    upload = validate_resume_upload(filename, content)
    if upload.source_format == "pdf":
        document = extract_pdf_document(upload.content)
    elif upload.source_format == "docx":
        document = extract_docx_document(upload.content)
    elif upload.source_format in {"txt", "markdown"}:
        document = extract_text_document(upload.content, source_format=upload.source_format)
    else:
        raise RadarError("Formato de curriculo nao suportado.")
    return upload, assign_sections(document)
