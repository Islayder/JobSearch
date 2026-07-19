from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from radar_vagas.canonicalization.normalize import normalize_text
from radar_vagas.domain.enums import (
    ProfileEvidenceType,
    ResumeImportCandidateType,
    ResumeImportDecision,
    ResumeImportStatus,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.persistence.models import (
    ProfessionalProfileVersion,
    ResumeImportCandidate,
    ResumeImportSession,
)
from radar_vagas.profile.service import (
    EducationInput,
    EvidenceInput,
    ExperienceInput,
    LanguageInput,
    ProfessionalProfileInput,
    ProfileImportResult,
    ProjectInput,
    SkillInput,
    SourceProvenanceInput,
    compare_active_jobs_to_profile,
    create_professional_profile,
)
from radar_vagas.resume_import.extraction import extract_resume
from radar_vagas.resume_import.parser import parse_resume_document
from radar_vagas.resume_import.repository import (
    candidate_for_session,
    json_dump,
    json_load,
)
from radar_vagas.resume_import.repository import (
    get_import_session as repository_get_import_session,
)
from radar_vagas.resume_import.repository import (
    list_import_sessions as repository_list_import_sessions,
)
from radar_vagas.resume_import.security import ResumeUpload


@dataclass(frozen=True)
class ResumeImportCreateResult:
    import_key: str
    candidate_count: int
    warning_count: int


@dataclass(frozen=True)
class ResumeImportConfirmResult:
    profile: ProfileImportResult
    comparisons_created: int = 0
    comparisons_reused: int = 0
    comparisons_failed: int = 0


def create_import_session(
    session: Session,
    *,
    filename: str,
    content: bytes,
) -> ResumeImportCreateResult:
    upload, document = extract_resume(filename, content)
    candidates = parse_resume_document(document)
    if not candidates:
        raise RadarError("Nao encontrei informacoes profissionais suficientes para revisar.")

    import_session = ResumeImportSession(
        import_key=_new_import_key(),
        source_format=upload.source_format,
        sanitized_filename=upload.filename,
        content_hash=upload.content_hash,
        status=ResumeImportStatus.REVIEWING,
        profile_name=_profile_name_for_upload(upload),
        headline=_first_payload_value(candidates, ResumeImportCandidateType.HEADLINE, "headline"),
        summary=_first_payload_value(candidates, ResumeImportCandidateType.SUMMARY, "summary"),
        page_count=document.page_count,
        extracted_character_count=document.extracted_character_count,
        warnings_json=json_dump(document.warnings),
        candidate_count=len(candidates),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(import_session)
    session.flush()
    for sequence, candidate in enumerate(candidates, start=1):
        session.add(
            ResumeImportCandidate(
                session_id=import_session.id,
                candidate_type=candidate.candidate_type,
                sequence=sequence,
                original_payload_json=json_dump(candidate.payload),
                reviewed_payload_json=None,
                decision=ResumeImportDecision.PENDING,
                confidence_score=candidate.confidence_score,
                confidence_label=candidate.confidence_label,
                explanation=candidate.explanation,
                source_reference=candidate.source_reference,
                source_excerpt=candidate.source_excerpt,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
    session.flush()
    return ResumeImportCreateResult(
        import_key=import_session.import_key,
        candidate_count=len(candidates),
        warning_count=len(document.warnings),
    )


def get_import_session(session: Session, import_key: str) -> ResumeImportSession:
    return repository_get_import_session(session, import_key)


def list_import_sessions(session: Session, *, limit: int = 20) -> list[ResumeImportSession]:
    return repository_list_import_sessions(session, limit=limit)


def update_candidate(
    session: Session,
    import_key: str,
    candidate_id: int,
    payload: dict[str, Any],
) -> ResumeImportCandidate:
    import_session = _reviewable_session(session, import_key)
    candidate = candidate_for_session(import_session, candidate_id)
    cleaned = _validate_candidate_payload(candidate.candidate_type, payload)
    candidate.reviewed_payload_json = json_dump(cleaned)
    candidate.decision = ResumeImportDecision.EDITED
    candidate.updated_at = utc_now()
    import_session.updated_at = utc_now()
    session.flush()
    return candidate


def accept_candidate(
    session: Session,
    import_key: str,
    candidate_id: int,
) -> ResumeImportCandidate:
    import_session = _reviewable_session(session, import_key)
    candidate = candidate_for_session(import_session, candidate_id)
    _validate_candidate_payload(candidate.candidate_type, _candidate_payload(candidate))
    candidate.decision = ResumeImportDecision.ACCEPTED
    candidate.updated_at = utc_now()
    import_session.updated_at = utc_now()
    session.flush()
    return candidate


def remove_candidate(
    session: Session,
    import_key: str,
    candidate_id: int,
) -> ResumeImportCandidate:
    import_session = _reviewable_session(session, import_key)
    candidate = candidate_for_session(import_session, candidate_id)
    candidate.decision = ResumeImportDecision.REMOVED
    candidate.updated_at = utc_now()
    import_session.updated_at = utc_now()
    session.flush()
    return candidate


def restore_candidate(
    session: Session,
    import_key: str,
    candidate_id: int,
) -> ResumeImportCandidate:
    import_session = _reviewable_session(session, import_key)
    candidate = candidate_for_session(import_session, candidate_id)
    candidate.decision = ResumeImportDecision.PENDING
    candidate.reviewed_payload_json = None
    candidate.updated_at = utc_now()
    import_session.updated_at = utc_now()
    session.flush()
    return candidate


def update_import_header(
    session: Session,
    import_key: str,
    *,
    profile_name: str,
    headline: str | None,
    summary: str | None,
) -> ResumeImportSession:
    import_session = _reviewable_session(session, import_key)
    import_session.profile_name = _clean_text(profile_name) or _profile_name_for_upload(
        ResumeUpload(
            filename=import_session.sanitized_filename,
            content=b"",
            source_format=import_session.source_format,
            content_hash=import_session.content_hash,
        )
    )
    import_session.headline = _clean_text(headline)
    import_session.summary = _clean_text(summary)
    import_session.updated_at = utc_now()
    session.flush()
    return import_session


def confirm_import(
    session: Session,
    import_key: str,
    *,
    activate_now: bool,
    analyze_jobs_after_confirm: bool = False,
) -> ResumeImportConfirmResult:
    import_session = repository_get_import_session(session, import_key)
    if import_session.status == ResumeImportStatus.CONFIRMED:
        if import_session.confirmed_profile_version_id is None:
            raise RadarError("Importacao confirmada sem versao de perfil vinculada.")
        profile = _result_for_existing_version(session, import_session.confirmed_profile_version_id)
        return ResumeImportConfirmResult(profile=profile)
    if import_session.status != ResumeImportStatus.REVIEWING:
        raise RadarError("Somente importacoes em revisao podem ser confirmadas.")

    document = build_profile_input_from_review(import_session)
    source_label = _source_label(import_session)
    profile = create_professional_profile(
        session,
        document,
        activate=activate_now,
        source_label=source_label,
        source_format=import_session.source_format,
        activation_source="resume_import",
    )
    import_session.status = ResumeImportStatus.CONFIRMED
    import_session.confirmed_profile_version_id = profile.profile_version_id
    import_session.completed_at = utc_now()
    import_session.updated_at = utc_now()
    comparisons_created = 0
    comparisons_reused = 0
    comparisons_failed = 0
    if analyze_jobs_after_confirm and activate_now:
        comparisons = compare_active_jobs_to_profile(session, limit=50)
        comparisons_created = comparisons.created
        comparisons_reused = comparisons.reused
        comparisons_failed = comparisons.failed
    session.flush()
    return ResumeImportConfirmResult(
        profile=profile,
        comparisons_created=comparisons_created,
        comparisons_reused=comparisons_reused,
        comparisons_failed=comparisons_failed,
    )


def discard_import(session: Session, import_key: str) -> ResumeImportSession:
    import_session = repository_get_import_session(session, import_key)
    if import_session.status == ResumeImportStatus.CONFIRMED:
        raise RadarError("Importacao confirmada nao pode ser descartada.")
    import_session.status = ResumeImportStatus.DISCARDED
    import_session.completed_at = utc_now()
    import_session.updated_at = utc_now()
    session.flush()
    return import_session


def purge_import(session: Session, import_key: str) -> None:
    import_session = repository_get_import_session(session, import_key)
    session.delete(import_session)
    session.flush()


def build_profile_input_from_review(
    import_session: ResumeImportSession,
) -> ProfessionalProfileInput:
    headline = import_session.headline
    summary = import_session.summary
    skills_by_name: dict[str, SkillInput] = {}
    experiences: list[ExperienceInput] = []
    projects: list[ProjectInput] = []
    education: list[EducationInput] = []
    languages: list[LanguageInput] = []
    accepted_count = 0

    for candidate in import_session.candidates:
        if candidate.decision not in {ResumeImportDecision.ACCEPTED, ResumeImportDecision.EDITED}:
            continue
        payload = _candidate_payload(candidate)
        provenance = _provenance(import_session, candidate)
        if candidate.candidate_type == ResumeImportCandidateType.HEADLINE:
            headline = _clean_text(payload.get("headline")) or headline
        elif candidate.candidate_type == ResumeImportCandidateType.SUMMARY:
            summary = _clean_text(payload.get("summary")) or summary
        elif candidate.candidate_type == ResumeImportCandidateType.SKILL:
            _merge_skill(skills_by_name, _skill_from_payload(payload, provenance))
            accepted_count += 1
        elif candidate.candidate_type == ResumeImportCandidateType.EXPERIENCE:
            experiences.append(_experience_from_payload(payload, provenance))
            accepted_count += 1
        elif candidate.candidate_type == ResumeImportCandidateType.PROJECT:
            projects.append(_project_from_payload(payload, provenance))
            accepted_count += 1
        elif candidate.candidate_type == ResumeImportCandidateType.EDUCATION:
            education.append(_education_from_payload(payload, provenance))
            accepted_count += 1
        elif candidate.candidate_type == ResumeImportCandidateType.LANGUAGE:
            languages.append(_language_from_payload(payload, provenance))
            accepted_count += 1

    if accepted_count == 0:
        raise RadarError("Confirme ao menos uma habilidade, experiencia, projeto, curso ou idioma.")
    try:
        return ProfessionalProfileInput(
            profile_name=_clean_text(import_session.profile_name) or "Perfil importado",
            headline=headline,
            summary=summary,
            skills=list(skills_by_name.values()),
            experiences=experiences,
            projects=projects,
            education=education,
            languages=languages,
        )
    except ValueError as exc:
        raise RadarError(f"Perfil revisado invalido: {exc}") from exc


def _reviewable_session(session: Session, import_key: str) -> ResumeImportSession:
    import_session = repository_get_import_session(session, import_key)
    if import_session.status != ResumeImportStatus.REVIEWING:
        raise RadarError("Esta importacao nao esta mais em revisao.")
    return import_session


def _validate_candidate_payload(
    candidate_type: ResumeImportCandidateType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if candidate_type == ResumeImportCandidateType.HEADLINE:
        return {"headline": _required(payload.get("headline"), "titulo")}
    if candidate_type == ResumeImportCandidateType.SUMMARY:
        return {"summary": _required(payload.get("summary"), "resumo")}
    if candidate_type == ResumeImportCandidateType.SKILL:
        return {
            "name": _required(payload.get("name"), "habilidade"),
            "category": _clean_text(payload.get("category")),
            "level": _clean_text(payload.get("level")),
            "evidence": _clean_evidence(payload.get("evidence")),
        }
    if candidate_type == ResumeImportCandidateType.EXPERIENCE:
        return {
            "title": _required(payload.get("title"), "experiencia"),
            "organization": _clean_text(payload.get("organization")),
            "start_date": _clean_text(payload.get("start_date")),
            "end_date": _clean_text(payload.get("end_date")),
            "description": _clean_text(payload.get("description")),
            "skills": _clean_list(payload.get("skills")),
        }
    if candidate_type == ResumeImportCandidateType.PROJECT:
        return {
            "name": _required(payload.get("name"), "projeto"),
            "description": _clean_text(payload.get("description")),
            "technologies": _clean_list(payload.get("technologies")),
            "source_ref": _clean_text(payload.get("source_ref")),
        }
    if candidate_type == ResumeImportCandidateType.EDUCATION:
        return {
            "institution": _required(payload.get("institution"), "instituicao"),
            "course": _required(payload.get("course"), "curso"),
            "status": _clean_text(payload.get("status")),
            "start_date": _clean_text(payload.get("start_date")),
            "end_date": _clean_text(payload.get("end_date")),
        }
    if candidate_type == ResumeImportCandidateType.LANGUAGE:
        return {
            "name": _required(payload.get("name"), "idioma"),
            "level": _required(payload.get("level"), "nivel"),
            "evidence": _clean_list(payload.get("evidence")),
        }
    return {"text": _clean_text(payload.get("text")) or ""}


def _candidate_payload(candidate: ResumeImportCandidate) -> dict[str, Any]:
    payload = json_load(candidate.reviewed_payload_json) or json_load(
        candidate.original_payload_json
    )
    if not isinstance(payload, dict):
        raise RadarError("Item de revisao invalido.")
    return payload


def _skill_from_payload(
    payload: dict[str, Any],
    provenance: SourceProvenanceInput,
) -> SkillInput:
    evidence = []
    for item in _clean_evidence(payload.get("evidence")):
        evidence.append(
            EvidenceInput(
                title=_required(item["title"], "evidencia"),
                description=item.get("description"),
                source_ref=item.get("source_ref"),
                evidence_type=_evidence_type(item.get("evidence_type")),
                provenance=provenance,
            )
        )
    return SkillInput(
        name=_required(payload.get("name"), "habilidade"),
        category=_clean_text(payload.get("category")),
        level=_clean_text(payload.get("level")),
        evidence=evidence,
        provenance=provenance,
    )


def _experience_from_payload(
    payload: dict[str, Any],
    provenance: SourceProvenanceInput,
) -> ExperienceInput:
    return ExperienceInput(
        title=_required(payload.get("title"), "experiencia"),
        organization=_clean_text(payload.get("organization")),
        start_date=_clean_text(payload.get("start_date")),
        end_date=_clean_text(payload.get("end_date")),
        description=_clean_text(payload.get("description")),
        skills=_clean_list(payload.get("skills")),
        provenance=provenance,
    )


def _project_from_payload(
    payload: dict[str, Any],
    provenance: SourceProvenanceInput,
) -> ProjectInput:
    return ProjectInput(
        name=_required(payload.get("name"), "projeto"),
        description=_clean_text(payload.get("description")),
        technologies=_clean_list(payload.get("technologies")),
        source_ref=_clean_text(payload.get("source_ref")),
        provenance=provenance,
    )


def _education_from_payload(
    payload: dict[str, Any],
    provenance: SourceProvenanceInput,
) -> EducationInput:
    return EducationInput(
        institution=_required(payload.get("institution"), "instituicao"),
        course=_required(payload.get("course"), "curso"),
        status=_clean_text(payload.get("status")),
        start_date=_clean_text(payload.get("start_date")),
        end_date=_clean_text(payload.get("end_date")),
        provenance=provenance,
    )


def _language_from_payload(
    payload: dict[str, Any],
    provenance: SourceProvenanceInput,
) -> LanguageInput:
    return LanguageInput(
        name=_required(payload.get("name"), "idioma"),
        level=_required(payload.get("level"), "nivel"),
        evidence=_clean_list(payload.get("evidence")),
        provenance=provenance,
    )


def _merge_skill(skills_by_name: dict[str, SkillInput], skill: SkillInput) -> None:
    normalized = normalize_text(skill.name)
    existing = skills_by_name.get(normalized)
    if existing is None:
        skills_by_name[normalized] = skill
        return
    evidence_keys = {
        (evidence.title, evidence.description, evidence.source_ref)
        for evidence in existing.evidence
    }
    merged_evidence = list(existing.evidence)
    for evidence in skill.evidence:
        key = (evidence.title, evidence.description, evidence.source_ref)
        if key not in evidence_keys:
            merged_evidence.append(evidence)
    skills_by_name[normalized] = SkillInput(
        name=existing.name,
        category=existing.category or skill.category,
        level=existing.level or skill.level,
        evidence=merged_evidence,
        provenance=existing.provenance or skill.provenance,
    )


def _provenance(
    import_session: ResumeImportSession,
    candidate: ResumeImportCandidate,
) -> SourceProvenanceInput:
    block_ids = _block_ids(candidate.source_reference)
    return SourceProvenanceInput(
        source_type="resume_import",
        source_format=import_session.source_format,
        page_number=_page_number(candidate.source_reference),
        section=candidate.candidate_type.value.lower(),
        block_ids=block_ids,
        excerpt=candidate.source_excerpt,
        confidence_score=candidate.confidence_score,
        confidence_label=candidate.confidence_label.value if candidate.confidence_label else None,
        extraction_explanation=candidate.explanation,
    )


def _page_number(source_reference: str | None) -> int | None:
    if not source_reference or not source_reference.startswith("pagina "):
        return None
    try:
        return int(source_reference.split(",", 1)[0].split(" ", 1)[1])
    except (IndexError, ValueError):
        return None


def _block_ids(source_reference: str | None) -> list[str]:
    if not source_reference:
        return []
    marker = "bloco "
    if marker not in source_reference:
        return []
    return [source_reference.split(marker, 1)[1].strip()]


def _clean_evidence(value: Any) -> list[dict[str, str | None]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, str | None]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = _clean_text(item.get("title"))
        if not title:
            continue
        evidence.append(
            {
                "title": title,
                "description": _clean_text(item.get("description")),
                "source_ref": _clean_text(item.get("source_ref")),
                "evidence_type": _clean_text(item.get("evidence_type")),
            }
        )
    return evidence


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        pieces = value.replace(",", "\n").splitlines()
    elif isinstance(value, list):
        pieces = [str(item) for item in value]
    else:
        pieces = [str(value)]
    return [item for item in (_clean_text(piece) for piece in pieces) if item]


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _required(value: Any, label: str) -> str:
    text = _clean_text(value)
    if not text:
        raise RadarError(f"Preencha {label} antes de confirmar o item.")
    return text


def _evidence_type(value: str | None) -> ProfileEvidenceType:
    try:
        return ProfileEvidenceType(value or ProfileEvidenceType.RESUME.value)
    except ValueError:
        return ProfileEvidenceType.RESUME


def _first_payload_value(
    candidates: list[Any],
    candidate_type: ResumeImportCandidateType,
    field: str,
) -> str | None:
    for candidate in candidates:
        if candidate.candidate_type == candidate_type:
            value = candidate.payload.get(field)
            return str(value).strip() if value else None
    return None


def _profile_name_for_upload(upload: ResumeUpload) -> str:
    stem = Path(upload.filename).stem.strip() or "curriculo"
    return f"Perfil importado - {stem}"


def _source_label(import_session: ResumeImportSession) -> str:
    label = {
        "pdf": "Curriculo PDF",
        "docx": "Curriculo DOCX",
        "txt": "Curriculo TXT",
        "markdown": "Curriculo Markdown",
    }.get(import_session.source_format, "Curriculo")
    return f"{label}: {import_session.sanitized_filename}"


def _result_for_existing_version(session: Session, version_id: int) -> ProfileImportResult:
    version = session.get(ProfessionalProfileVersion, version_id)
    if version is None:
        raise RadarError("Versao de perfil confirmada nao encontrada.")
    return ProfileImportResult(
        profile_id=version.profile_id,
        profile_version_id=version.id,
        profile_name=version.profile.name if version.profile else "Perfil",
        version_number=version.version_number,
        content_hash=version.content_hash,
        created_version=False,
        source_path=version.source_path,
    )


def _new_import_key() -> str:
    return uuid4().hex
