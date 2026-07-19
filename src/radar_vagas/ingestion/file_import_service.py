import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar_vagas.canonicalization.normalize import (
    generate_content_hash,
    normalize_city,
    normalize_company_name,
    normalize_state,
    normalize_text,
    normalize_title,
    normalize_url,
)
from radar_vagas.config.loaders import blocked_company_reasons, load_eligibility_rules
from radar_vagas.config.settings import Settings
from radar_vagas.deduplication.service import DuplicateCandidate, classify_canonical_duplicate
from radar_vagas.domain.enums import (
    DuplicateKind,
    EligibilityStatus,
    JobStatus,
    PostingStatus,
    SourceRunStatus,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.eligibility.service import EligibilityInput, evaluate_eligibility
from radar_vagas.eligibility.workflow import evaluate_job_record
from radar_vagas.ingestion.file_parser import ParsedImportFile, ParsedImportItem, parse_import_file
from radar_vagas.ingestion.import_schema import ImportedPosting
from radar_vagas.persistence.models import (
    Company,
    CompanyAlias,
    Decision,
    FileImportBatch,
    ImportItemAudit,
    Job,
    Posting,
    Source,
    SourceRun,
)


@dataclass(frozen=True)
class ImportFileReport:
    input_file: str
    started_at: datetime
    finished_at: datetime
    dry_run: bool
    summary: dict[str, int]
    valid_items: list[dict[str, Any]]
    invalid_items: list[dict[str, Any]]
    exact_duplicates: list[dict[str, Any]]
    probable_duplicates: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_file": self.input_file,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "dry_run": self.dry_run,
            "summary": self.summary,
            "valid_items": self.valid_items,
            "invalid_items": self.invalid_items,
            "exact_duplicates": self.exact_duplicates,
            "probable_duplicates": self.probable_duplicates,
        }


@dataclass(frozen=True)
class ImportExecutionResult:
    report: ImportFileReport
    postings_created: int
    postings_skipped: int
    jobs_created: int
    probable_duplicates: int


@dataclass(frozen=True)
class ItemAnalysis:
    parsed_item: ParsedImportItem
    posting: ImportedPosting
    source_slug: str
    normalized_url: str
    content_hash: str
    duplicate_kind: DuplicateKind
    duplicate_job_id: int | None
    duplicate_posting_id: int | None
    eligibility_status: EligibilityStatus
    reason_code: str


@dataclass
class _ReportBuilder:
    input_file: str
    started_at: datetime
    dry_run: bool
    valid_items: list[dict[str, Any]] = field(default_factory=list)
    invalid_items: list[dict[str, Any]] = field(default_factory=list)
    exact_duplicates: list[dict[str, Any]] = field(default_factory=list)
    probable_duplicates: list[dict[str, Any]] = field(default_factory=list)
    eligible: int = 0
    manual_review: int = 0
    ineligible: int = 0

    def build(self, rows_read: int) -> ImportFileReport:
        summary = {
            "linhas_lidas": rows_read,
            "validas": len(self.valid_items),
            "invalidas": len(self.invalid_items),
            "duplicatas_exatas": len(self.exact_duplicates),
            "duplicatas_provaveis": len(self.probable_duplicates),
            "elegiveis": self.eligible,
            "revisao_manual": self.manual_review,
            "incompativeis": self.ineligible,
        }
        return ImportFileReport(
            input_file=self.input_file,
            started_at=self.started_at,
            finished_at=utc_now(),
            dry_run=self.dry_run,
            summary=summary,
            valid_items=self.valid_items,
            invalid_items=self.invalid_items,
            exact_duplicates=self.exact_duplicates,
            probable_duplicates=self.probable_duplicates,
        )


def validate_import_file(
    session: Session,
    path: Path,
    settings: Settings,
    *,
    delimiter: str | None = None,
    dry_run: bool = True,
) -> ImportFileReport:
    parsed = _parse_existing_file(path, delimiter)
    analyses = _analyze_items(session, parsed, settings)
    return _build_report(parsed, analyses, dry_run=dry_run)


def import_file(
    session: Session,
    path: Path,
    settings: Settings,
    *,
    delimiter: str | None = None,
    dry_run: bool = False,
) -> ImportExecutionResult:
    parsed = _parse_existing_file(path, delimiter)
    analyses = _analyze_items(session, parsed, settings)
    if dry_run:
        return ImportExecutionResult(
            report=_build_report(parsed, analyses, dry_run=True),
            postings_created=0,
            postings_skipped=sum(
                1 for analysis in analyses if analysis.duplicate_kind is DuplicateKind.EXACT
            ),
            jobs_created=0,
            probable_duplicates=sum(
                1 for analysis in analyses if analysis.duplicate_kind is DuplicateKind.PROBABLE
            ),
        )

    file_hash = _file_hash(path)
    report = _build_report(parsed, analyses, dry_run=False)
    batch = FileImportBatch(
        input_file=str(path),
        file_hash=file_hash,
        file_format=parsed.file_format,
        schema_version=parsed.schema_version,
        import_mode="file_import",
        started_at=report.started_at,
        finished_at=report.finished_at,
        summary_json=json.dumps(report.summary, ensure_ascii=False, sort_keys=True),
    )
    session.add(batch)
    session.flush()

    counters = {"created": 0, "skipped": 0, "jobs_created": 0, "probable": 0}
    runs_by_source_id: dict[int, SourceRun] = {}
    analyses_by_index = {analysis.parsed_item.item_index: analysis for analysis in analyses}

    for parsed_item in parsed.items:
        analysis = analyses_by_index.get(parsed_item.item_index)
        if analysis is None:
            _add_invalid_audit(session, batch, parsed_item)
            continue

        if analysis.duplicate_kind is DuplicateKind.EXACT:
            counters["skipped"] += 1
            _add_valid_audit(session, batch, analysis, status="skipped_duplicate")
            continue

        source = _get_or_create_source(session, analysis.posting)
        run = _get_or_create_run(session, source, runs_by_source_id)
        run.items_found += 1
        company = _get_or_create_company(session, analysis.posting, settings)
        job, duplicate_kind = _get_or_create_job(session, company, analysis)
        posting = _create_posting(session, source, run, job, analysis)
        decision: Decision = evaluate_job_record(session, job, settings)

        run.items_created += 1
        counters["created"] += 1
        if duplicate_kind is DuplicateKind.PROBABLE:
            counters["probable"] += 1
        else:
            counters["jobs_created"] += 1

        _add_valid_audit(
            session,
            batch,
            analysis,
            status="created",
            posting=posting,
            job=job,
            source=source,
            decision=decision,
        )

    for run in runs_by_source_id.values():
        run.finished_at = utc_now()
        run.status = SourceRunStatus.SUCCESS
        run.items_skipped = counters["skipped"]

    return ImportExecutionResult(
        report=report,
        postings_created=counters["created"],
        postings_skipped=counters["skipped"],
        jobs_created=counters["jobs_created"],
        probable_duplicates=counters["probable"],
    )


def write_import_report(report: ImportFileReport, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_existing_file(path: Path, delimiter: str | None) -> ParsedImportFile:
    if not path.exists():
        raise RadarError(f"Arquivo não encontrado: {path}")
    try:
        return parse_import_file(path, delimiter=delimiter)
    except ValueError as exc:
        raise RadarError(str(exc)) from exc


def _analyze_items(
    session: Session, parsed: ParsedImportFile, settings: Settings
) -> list[ItemAnalysis]:
    blocked_reasons = blocked_company_reasons(settings.config_dir)
    rules = load_eligibility_rules(settings.config_dir)
    analyses: list[ItemAnalysis] = []
    seen_exact_keys: set[tuple[str, str]] = set()

    for parsed_item in parsed.valid_items:
        posting = parsed_item.posting
        if posting is None:
            continue
        source_slug = slugify_source_name(posting.source_name)
        normalized_url = _effective_normalized_url(posting, parsed.input_file, parsed_item)
        content_hash = _content_hash(posting, normalized_url)
        duplicate_kind, duplicate_posting_id, duplicate_job_id = _detect_duplicate(
            session,
            posting,
            source_slug=source_slug,
            normalized_url=normalized_url,
            content_hash=content_hash,
        )
        exact_keys = _exact_keys(source_slug, posting, normalized_url, content_hash)
        if duplicate_kind is DuplicateKind.DISTINCT and seen_exact_keys.intersection(exact_keys):
            duplicate_kind = DuplicateKind.EXACT
        seen_exact_keys.update(exact_keys)
        eligibility = evaluate_eligibility(
            EligibilityInput(
                company_name=posting.company,
                company_aliases=(),
                company_is_blocked=normalize_company_name(posting.company) in blocked_reasons,
                job_status=JobStatus.NEW,
                employment_type=posting.employment_type,
                work_model=posting.work_model,
                city=normalize_city(posting.city),
                state=normalize_state(posting.state),
                remote_country_scope=posting.remote_country_scope,
                hours_per_day=posting.hours_per_day,
            ),
            rules,
            blocked_reasons,
        )
        analyses.append(
            ItemAnalysis(
                parsed_item=parsed_item,
                posting=posting,
                source_slug=source_slug,
                normalized_url=normalized_url,
                content_hash=content_hash,
                duplicate_kind=duplicate_kind,
                duplicate_job_id=duplicate_job_id,
                duplicate_posting_id=duplicate_posting_id,
                eligibility_status=eligibility.status,
                reason_code=eligibility.reason_code,
            )
        )
    return analyses


def _exact_keys(
    source_slug: str,
    posting: ImportedPosting,
    normalized_url: str,
    content_hash: str,
) -> set[tuple[str, str]]:
    keys = {("hash", content_hash)}
    if posting.external_id:
        keys.add((f"external:{source_slug}", posting.external_id))
    if normalized_url:
        keys.add((f"url:{source_slug}", normalized_url))
    return keys


def _build_report(
    parsed: ParsedImportFile, analyses: list[ItemAnalysis], *, dry_run: bool
) -> ImportFileReport:
    builder = _ReportBuilder(
        input_file=str(parsed.input_file),
        started_at=utc_now(),
        dry_run=dry_run,
    )
    analyses_by_index = {analysis.parsed_item.item_index: analysis for analysis in analyses}

    for parsed_item in parsed.items:
        analysis = analyses_by_index.get(parsed_item.item_index)
        if analysis is None:
            builder.invalid_items.append(_invalid_item_payload(parsed_item))
            continue
        builder.valid_items.append(_valid_item_payload(analysis))
        if analysis.duplicate_kind is DuplicateKind.EXACT:
            builder.exact_duplicates.append(_duplicate_payload(analysis))
        elif analysis.duplicate_kind is DuplicateKind.PROBABLE:
            builder.probable_duplicates.append(_duplicate_payload(analysis))

        if analysis.eligibility_status is EligibilityStatus.ELIGIBLE:
            builder.eligible += 1
        elif analysis.eligibility_status is EligibilityStatus.MANUAL_REVIEW:
            builder.manual_review += 1
        elif analysis.eligibility_status is EligibilityStatus.INELIGIBLE:
            builder.ineligible += 1

    return builder.build(rows_read=len(parsed.items))


def _valid_item_payload(analysis: ItemAnalysis) -> dict[str, Any]:
    return {
        "item_index": analysis.parsed_item.item_index,
        "line_number": analysis.parsed_item.line_number,
        "source_name": analysis.posting.source_name,
        "title": analysis.posting.title,
        "company": analysis.posting.company,
        "employment_type": analysis.posting.employment_type.value,
        "work_model": analysis.posting.work_model.value,
        "eligibility_status": analysis.eligibility_status.value,
        "reason_code": analysis.reason_code,
        "duplicate_kind": analysis.duplicate_kind.value,
    }


def _invalid_item_payload(parsed_item: ParsedImportItem) -> dict[str, Any]:
    return {
        "item_index": parsed_item.item_index,
        "line_number": parsed_item.line_number,
        "fields": parsed_item.raw_fields,
        "errors": parsed_item.errors,
    }


def _duplicate_payload(analysis: ItemAnalysis) -> dict[str, Any]:
    return {
        "item_index": analysis.parsed_item.item_index,
        "line_number": analysis.parsed_item.line_number,
        "duplicate_kind": analysis.duplicate_kind.value,
        "posting_id": analysis.duplicate_posting_id,
        "job_id": analysis.duplicate_job_id,
        "title": analysis.posting.title,
        "company": analysis.posting.company,
    }


def _detect_duplicate(
    session: Session,
    posting: ImportedPosting,
    *,
    source_slug: str,
    normalized_url: str,
    content_hash: str,
) -> tuple[DuplicateKind, int | None, int | None]:
    source = session.scalar(select(Source).where(Source.slug == source_slug))
    if source is not None:
        if posting.external_id:
            duplicate = session.scalar(
                select(Posting).where(
                    Posting.source_id == source.id,
                    Posting.external_id == posting.external_id,
                )
            )
            if duplicate is not None:
                return DuplicateKind.EXACT, duplicate.id, duplicate.job_id

        duplicate = session.scalar(
            select(Posting).where(
                Posting.source_id == source.id,
                Posting.normalized_url == normalized_url,
            )
        )
        if duplicate is not None:
            return DuplicateKind.EXACT, duplicate.id, duplicate.job_id

    duplicate_by_hash = session.scalar(select(Posting).where(Posting.content_hash == content_hash))
    if duplicate_by_hash is not None:
        return DuplicateKind.EXACT, duplicate_by_hash.id, duplicate_by_hash.job_id

    probable_job_id = _probable_duplicate_job_id(session, posting, content_hash)
    if probable_job_id is not None:
        return DuplicateKind.PROBABLE, None, probable_job_id
    return DuplicateKind.DISTINCT, None, None


def _probable_duplicate_job_id(
    session: Session, posting: ImportedPosting, content_hash: str
) -> int | None:
    normalized_company = normalize_company_name(posting.company)
    existing_jobs = session.scalars(
        select(Job)
        .join(Company)
        .where(
            Company.normalized_name == normalized_company,
            Job.normalized_title == normalize_title(posting.title),
            Job.work_model == posting.work_model,
        )
    ).all()
    candidate = _candidate_from_import(normalized_company, posting, content_hash)
    for job in existing_jobs:
        duplicate_kind = classify_canonical_duplicate(
            candidate,
            DuplicateCandidate(
                normalized_company=normalized_company,
                normalized_title=job.normalized_title,
                city=job.city,
                work_model=job.work_model,
                published_at=job.published_at,
                application_url=job.application_url,
                content_hash=job.postings[0].content_hash if job.postings else None,
            ),
        )
        if duplicate_kind is DuplicateKind.PROBABLE:
            return job.id
    return None


def _get_or_create_source(session: Session, posting: ImportedPosting) -> Source:
    slug = slugify_source_name(posting.source_name)
    source = session.scalar(select(Source).where(Source.slug == slug))
    if source is not None:
        return source
    source = Source(
        name=posting.source_name,
        slug=slug,
        source_type=posting.source_type or "file_import",
        base_url=None,
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(source)
    session.flush()
    return source


def _get_or_create_run(
    session: Session, source: Source, runs_by_source_id: dict[int, SourceRun]
) -> SourceRun:
    if source.id in runs_by_source_id:
        return runs_by_source_id[source.id]
    run = SourceRun(
        source_id=source.id,
        started_at=utc_now(),
        status=SourceRunStatus.RUNNING,
        items_found=0,
        items_created=0,
        items_skipped=0,
    )
    session.add(run)
    session.flush()
    runs_by_source_id[source.id] = run
    return run


def _get_or_create_company(
    session: Session, posting: ImportedPosting, settings: Settings
) -> Company:
    normalized = normalize_company_name(posting.company)
    alias = session.scalar(select(CompanyAlias).where(CompanyAlias.normalized_alias == normalized))
    company: Company | None = alias.company if alias is not None else None
    if company is None:
        company = session.scalar(select(Company).where(Company.normalized_name == normalized))

    if company is None:
        company = Company(
            canonical_name=posting.company,
            normalized_name=normalized,
            website=None,
            is_blocked=False,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(company)
        session.flush()
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias=posting.company,
                normalized_alias=normalized,
            )
        )
    elif alias is None:
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias=posting.company,
                normalized_alias=normalized,
            )
        )

    blocked_reason = blocked_company_reasons(settings.config_dir).get(normalized)
    if blocked_reason is not None:
        company.is_blocked = True
        company.blocked_reason = blocked_reason
    return company


def _get_or_create_job(
    session: Session, company: Company, analysis: ItemAnalysis
) -> tuple[Job, DuplicateKind]:
    duplicate_kind = DuplicateKind.DISTINCT
    if analysis.duplicate_kind is DuplicateKind.PROBABLE:
        duplicate_kind = DuplicateKind.PROBABLE
    job = Job(
        company_id=company.id,
        canonical_title=analysis.posting.title,
        normalized_title=normalize_title(analysis.posting.title),
        description=analysis.posting.description_with_benefits(),
        employment_type=analysis.posting.employment_type,
        seniority=None,
        work_model=analysis.posting.work_model,
        country=analysis.posting.country,
        state=normalize_state(analysis.posting.state),
        city=normalize_city(analysis.posting.city),
        remote_country_scope=analysis.posting.remote_country_scope,
        hours_per_day=analysis.posting.hours_per_day,
        hours_per_week=analysis.posting.hours_per_week,
        salary_min=analysis.posting.salary_min,
        salary_max=analysis.posting.salary_max,
        salary_period=analysis.posting.salary_period,
        currency=analysis.posting.currency,
        application_url=analysis.posting.application_url or analysis.posting.url,
        published_at=analysis.posting.published_at,
        expires_at=analysis.posting.expires_at,
        status=JobStatus.NEW,
        course_requirement=None,
        has_uninterpreted_course_requirement=False,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(job)
    session.flush()
    return job, duplicate_kind


def _create_posting(
    session: Session,
    source: Source,
    run: SourceRun,
    job: Job,
    analysis: ItemAnalysis,
) -> Posting:
    original_url = _effective_original_url(analysis)
    posting = Posting(
        source_id=source.id,
        source_run_id=run.id,
        external_id=analysis.posting.external_id,
        original_url=original_url,
        normalized_url=analysis.normalized_url,
        raw_title=analysis.posting.title,
        raw_company=analysis.posting.company,
        raw_location=analysis.posting.location or _location_from_fields(analysis.posting),
        raw_description=analysis.posting.description or "",
        published_at=analysis.posting.published_at,
        first_seen_at=utc_now(),
        last_seen_at=utc_now(),
        content_hash=analysis.content_hash,
        status=(
            PostingStatus.PROBABLE_DUPLICATE
            if analysis.duplicate_kind is DuplicateKind.PROBABLE
            else PostingStatus.NEW
        ),
        job_id=job.id,
    )
    session.add(posting)
    session.flush()
    return posting


def _add_invalid_audit(
    session: Session, batch: FileImportBatch, parsed_item: ParsedImportItem
) -> None:
    session.add(
        ImportItemAudit(
            batch_id=batch.id,
            posting_id=None,
            job_id=None,
            source_id=None,
            item_index=parsed_item.item_index,
            line_number=parsed_item.line_number,
            status="invalid",
            duplicate_kind=None,
            raw_payload_json=json.dumps(parsed_item.raw_fields, ensure_ascii=False, sort_keys=True),
            normalized_payload_json=None,
            errors_json=json.dumps(parsed_item.errors, ensure_ascii=False),
            created_at=utc_now(),
        )
    )


def _add_valid_audit(
    session: Session,
    batch: FileImportBatch,
    analysis: ItemAnalysis,
    *,
    status: str,
    posting: Posting | None = None,
    job: Job | None = None,
    source: Source | None = None,
    decision: Decision | None = None,
) -> None:
    normalized_payload = analysis.posting.model_dump(mode="json")
    if decision is not None:
        normalized_payload["decision"] = {
            "eligibility_status": decision.eligibility_status.value,
            "reason_code": decision.reason_code,
            "ranking_score": decision.ranking_score,
        }
    session.add(
        ImportItemAudit(
            batch_id=batch.id,
            posting_id=posting.id if posting is not None else analysis.duplicate_posting_id,
            job_id=job.id if job is not None else analysis.duplicate_job_id,
            source_id=source.id if source is not None else None,
            item_index=analysis.parsed_item.item_index,
            line_number=analysis.parsed_item.line_number,
            status=status,
            duplicate_kind=analysis.duplicate_kind.value,
            raw_payload_json=json.dumps(
                analysis.parsed_item.raw_fields, ensure_ascii=False, sort_keys=True
            ),
            normalized_payload_json=json.dumps(
                normalized_payload, ensure_ascii=False, sort_keys=True
            ),
            errors_json=None,
            created_at=utc_now(),
        )
    )


def slugify_source_name(source_name: str) -> str:
    normalized = normalize_text(source_name)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "file-import"


def _effective_original_url(analysis: ItemAnalysis) -> str:
    return (
        analysis.posting.url
        or analysis.posting.application_url
        or f"file-import://{analysis.content_hash}/{analysis.parsed_item.item_index}"
    )


def _effective_normalized_url(
    posting: ImportedPosting, input_file: Path, parsed_item: ParsedImportItem
) -> str:
    explicit_url = posting.url or posting.application_url
    if explicit_url:
        return normalize_url(explicit_url)
    seed = generate_content_hash(
        [
            str(input_file.resolve()),
            str(parsed_item.item_index),
            posting.source_name,
            posting.external_id,
            posting.title,
            posting.company,
        ]
    )
    return normalize_url(f"file-import://{seed}/{parsed_item.item_index}")


def _content_hash(posting: ImportedPosting, normalized_url: str) -> str:
    url_component = normalized_url if posting.url or posting.application_url else ""
    return generate_content_hash(
        [
            slugify_source_name(posting.source_name),
            posting.external_id,
            url_component,
            posting.title,
            posting.company,
            posting.location,
            posting.description_with_benefits(),
        ]
    )


def _candidate_from_import(
    normalized_company: str, posting: ImportedPosting, content_hash: str
) -> DuplicateCandidate:
    return DuplicateCandidate(
        normalized_company=normalized_company,
        normalized_title=normalize_title(posting.title),
        city=normalize_city(posting.city),
        work_model=posting.work_model,
        published_at=posting.published_at,
        application_url=posting.application_url or posting.url,
        content_hash=content_hash,
    )


def _location_from_fields(posting: ImportedPosting) -> str:
    parts = [part for part in [posting.city, posting.state, posting.country] if part]
    return ", ".join(parts) if parts else ""


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
