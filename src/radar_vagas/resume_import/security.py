from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath

from radar_vagas.domain.errors import RadarError

MAX_RAW_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 30
MAX_DOCX_ZIP_ENTRIES = 500
MAX_DOCX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_EXTRACTED_TEXT_CHARS = 250_000
MAX_CANDIDATE_EXCERPT_CHARS = 500
MIN_TEXT_CHARS = 40
SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


@dataclass(frozen=True)
class ResumeUpload:
    filename: str
    content: bytes
    source_format: str
    content_hash: str


def validate_resume_upload(filename: str, content: bytes) -> ResumeUpload:
    sanitized = sanitize_filename(filename)
    if not content:
        raise RadarError("Arquivo vazio. Envie um curriculo PDF, DOCX, TXT ou Markdown.")
    if len(content) > MAX_RAW_UPLOAD_BYTES:
        raise RadarError("Arquivo maior que 8 MB. Reduza o arquivo e tente novamente.")

    suffix = Path(sanitized).suffix.lower()
    if suffix == ".doc":
        raise RadarError("O formato .doc antigo ainda nao e suportado. Salve o arquivo como .docx.")
    if suffix == ".docm":
        raise RadarError("Curriculos com macros nao sao aceitos. Salve uma copia .docx sem macros.")
    if suffix not in SUPPORTED_SUFFIXES:
        raise RadarError("Use um curriculo PDF, DOCX, TXT ou Markdown.")

    if suffix == ".pdf":
        _validate_pdf_signature(content)
        source_format = "pdf"
    elif suffix == ".docx":
        _validate_docx_container(content)
        source_format = "docx"
    else:
        _validate_text_bytes(content)
        source_format = "markdown" if suffix == ".md" else "txt"

    return ResumeUpload(
        filename=sanitized,
        content=content,
        source_format=source_format,
        content_hash=sha256(content).hexdigest(),
    )


def sanitize_filename(filename: str) -> str:
    raw_name = Path(filename or "curriculo").name
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" .")
    if not safe:
        safe = "curriculo"
    return safe[:255]


def _validate_pdf_signature(content: bytes) -> None:
    if not content.startswith(b"%PDF-"):
        raise RadarError("O conteudo do arquivo nao parece ser um PDF valido.")


def _validate_docx_container(content: bytes) -> None:
    if not content.startswith(b"PK"):
        raise RadarError("O conteudo do arquivo nao parece ser um DOCX valido.")
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_ZIP_ENTRIES:
                raise RadarError("DOCX com estrutura grande demais para importacao local segura.")
            names = {info.filename for info in infos}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise RadarError("O conteudo do arquivo nao parece ser um DOCX valido.")
            total_uncompressed = 0
            for info in infos:
                name = info.filename
                _validate_zip_path(name)
                lower_name = name.lower()
                if lower_name.endswith("vbaproject.bin") or "macros" in lower_name:
                    raise RadarError(
                        "Curriculos com macros nao sao aceitos. Salve uma copia .docx sem macros."
                    )
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise RadarError("DOCX grande demais depois de descompactar.")
                if info.compress_size and info.file_size / max(info.compress_size, 1) > 100:
                    raise RadarError("DOCX com compressao abusiva nao pode ser importado.")
            _reject_external_relationships(archive)
    except zipfile.BadZipFile as exc:
        raise RadarError("O conteudo do arquivo nao parece ser um DOCX valido.") from exc


def _validate_zip_path(name: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise RadarError("DOCX com caminhos internos invalidos nao pode ser importado.")


def _reject_external_relationships(archive: zipfile.ZipFile) -> None:
    relationship_names = [
        name
        for name in archive.namelist()
        if name.lower().endswith(".rels") and name.startswith(("word/", "_rels/"))
    ]
    for name in relationship_names:
        xml = archive.read(name).decode("utf-8", errors="ignore").lower()
        if 'targetmode="external"' in xml:
            raise RadarError("DOCX com referencias externas nao pode ser importado.")


def _validate_text_bytes(content: bytes) -> None:
    if b"\x00" in content:
        raise RadarError("Arquivo de texto invalido para importacao de curriculo.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RadarError("Arquivo de texto deve estar em UTF-8.") from exc
    if len(text.strip()) < MIN_TEXT_CHARS:
        raise RadarError("O arquivo tem pouca informacao para montar um perfil revisavel.")
