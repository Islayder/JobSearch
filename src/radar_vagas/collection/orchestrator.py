from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar_vagas.canonicalization.normalize import (
    normalize_city,
    normalize_state,
    normalize_title,
)
from radar_vagas.collection.contracts import CollectionContext, CollectionResult
from radar_vagas.collection.result import CollectionExecutionReport, CollectionSummary
from radar_vagas.collectors.common import short_metadata_change
from radar_vagas.config.schemas import BoardConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    DuplicateKind,
    EligibilityStatus,
    JobStatus,
    PostingStatus,
    SourceRunStatus,
)
from radar_vagas.domain.time import utc_now
from radar_vagas.eligibility.workflow import evaluate_job_record
from radar_vagas.ingestion.file_import_service import (
    ItemAnalysis,
    _analyze_items,
    _create_posting,
    _get_or_create_company,
    _get_or_create_job,
    slugify_source_name,
)
from radar_vagas.ingestion.file_parser import ParsedImportFile, ParsedImportItem
from radar_vagas.ingestion.import_schema import ImportedPosting
from radar_vagas.persistence.models import (
    Application,
    Company,
    CompanyBoard,
    Job,
    Posting,
    PostingRevision,
    Source,
    SourceRun,
)


def build_collection_context(
    *,
    collector: str,
    company_name: str | None,
    board_key: str | None = None,
    board_token: str | None = None,
    url: str | None = None,
    dry_run: bool = False,
    max_items: int | None = None,
    since: datetime | None = None,
    base_context: CollectionContext | None = None,
) -> CollectionContext:
    source_name = _source_name(collector, company_name=company_name, url=url)
    if base_context is not None:
        return replace(
            base_context,
            collector=collector,
            source_name=source_name,
            source_type=collector,
            company_name=company_name,
            board_key=board_key,
            board_token=board_token,
            url=url,
            dry_run=dry_run,
            max_items=max_items,
            since=since,
        )
    return CollectionContext(
        collector=collector,
        source_name=source_name,
        source_type=collector,
        company_name=company_name,
        board_key=board_key,
        board_token=board_token,
        url=url,
        dry_run=dry_run,
        max_items=max_items,
        since=since,
    )


def load_board_cache_headers(
    session: Session,
    board_key: str | None,
) -> tuple[str | None, str | None]:
    if not board_key:
        return None, None
    board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board_key))
    if board is None:
        return None, None
    return board.last_etag, board.last_modified


def run_collection_persistence(
    session: Session,
    settings: Settings,
    context: CollectionContext,
    result: CollectionResult,
    *,
    board_config: BoardConfig | None = None,
) -> CollectionExecutionReport:
    started_at = utc_now()
    if context.dry_run:
        report = _dry_run_report(session, settings, context, result, started_at)
        session.rollback()
        return report

    source = _get_or_create_source_for_context(session, context)
    run = SourceRun(
        source_id=source.id,
        started_at=started_at,
        status=SourceRunStatus.RUNNING,
        items_found=result.found,
        items_created=0,
        items_skipped=0,
    )
    session.add(run)
    session.flush()

    summary = _persist_result_items(session, settings, context, result, source, run)
    run.items_created = summary.new
    run.items_skipped = summary.unchanged + summary.exact_duplicates
    run.status = SourceRunStatus.SUCCESS
    run.finished_at = utc_now()

    board = _upsert_board(session, settings, context, source, board_config)
    if board is not None:
        _mark_board_success(board, run, result)

    report = _execution_report(context, result, started_at, run.finished_at, summary)
    session.flush()
    return report


def record_failed_collection(
    session: Session,
    context: CollectionContext,
    error: Exception,
    *,
    board_config: BoardConfig | None = None,
    settings: Settings | None = None,
) -> None:
    if context.dry_run:
        session.rollback()
        return
    source = _get_or_create_source_for_context(session, context)
    run = SourceRun(
        source_id=source.id,
        started_at=utc_now(),
        finished_at=utc_now(),
        status=SourceRunStatus.FAILED,
        items_found=0,
        items_created=0,
        items_skipped=0,
        error_message=str(error),
    )
    session.add(run)
    session.flush()
    if settings is not None:
        board = _upsert_board(session, settings, context, source, board_config)
        if board is not None:
            _mark_board_failure(board, run)


def board_url_for(context: CollectionContext) -> str | None:
    if context.collector == "greenhouse" and context.board_token:
        return f"https://boards-api.greenhouse.io/v1/boards/{context.board_token}/jobs?content=true"
    if context.collector == "lever" and context.board_token:
        return f"https://api.lever.co/v0/postings/{context.board_token}?mode=json"
    return context.url


def _dry_run_report(
    session: Session,
    settings: Settings,
    context: CollectionContext,
    result: CollectionResult,
    started_at: datetime,
) -> CollectionExecutionReport:
    analyses = _analyze_items(session, _parsed_from_items(context, result.items), settings)
    counters = _base_counters(result, analyses)
    counters["new"] = sum(
        1
        for analysis in analyses
        if analysis.duplicate_kind in {DuplicateKind.DISTINCT, DuplicateKind.PROBABLE}
    )
    counters["exact_duplicates"] = sum(
        1 for analysis in analyses if analysis.duplicate_kind is DuplicateKind.EXACT
    )
    counters["probable_duplicates"] = sum(
        1 for analysis in analyses if analysis.duplicate_kind is DuplicateKind.PROBABLE
    )
    summary = _summary_from_counters(counters)
    return _execution_report(context, result, started_at, utc_now(), summary)


def _persist_result_items(
    session: Session,
    settings: Settings,
    context: CollectionContext,
    result: CollectionResult,
    source: Source,
    run: SourceRun,
) -> CollectionSummary:
    analyses = _analyze_items(session, _parsed_from_items(context, result.items), settings)
    counters = _base_counters(result, analyses)
    seen_posting_ids: set[int] = set()

    if result.not_modified:
        return _summary_from_counters(counters)

    for analysis in analyses:
        existing = _find_existing_posting(session, source, analysis)
        if existing is not None:
            seen_posting_ids.add(existing.id)
            if existing.content_hash == analysis.content_hash:
                if not existing.is_active:
                    counters["reopened"] += 1
                _mark_posting_seen(existing, run)
                counters["unchanged"] += 1
                continue
            if not existing.is_active:
                counters["reopened"] += 1
            _record_revision(session, existing, analysis, run)
            _update_existing_posting(session, settings, existing, analysis, run)
            counters["changed"] += 1
            continue

        if analysis.duplicate_kind is DuplicateKind.EXACT:
            counters["exact_duplicates"] += 1
            continue

        company = _get_or_create_company(session, analysis.posting, settings)
        job, duplicate_kind = _get_or_create_job(session, company, analysis)
        posting = _create_posting(session, source, run, job, analysis)
        posting.is_active = True
        posting.missing_count = 0
        posting.closed_reason = None
        evaluate_job_record(session, job, settings)
        seen_posting_ids.add(posting.id)
        counters["new"] += 1
        if duplicate_kind is DuplicateKind.PROBABLE:
            counters["probable_duplicates"] += 1

    if result.complete_snapshot and not result.partial:
        counters["closed"] += _increment_absences(
            session,
            source=source,
            run=run,
            seen_posting_ids=seen_posting_ids,
            close_after=settings_close_after(context),
        )

    return _summary_from_counters(counters)


def settings_close_after(context: CollectionContext) -> int:
    return context.collection_config.close_after_missing_successful_runs


def _increment_absences(
    session: Session,
    *,
    source: Source,
    run: SourceRun,
    seen_posting_ids: set[int],
    close_after: int,
) -> int:
    statement = select(Posting).where(
        Posting.source_id == source.id,
        Posting.is_active.is_(True),
    )
    if seen_posting_ids:
        statement = statement.where(Posting.id.not_in(seen_posting_ids))
    closed = 0
    for posting in session.scalars(statement).all():
        posting.missing_count += 1
        posting.source_run_id = run.id
        posting.last_seen_at = utc_now()
        if posting.missing_count < close_after:
            continue
        posting.is_active = False
        posting.status = PostingStatus.CLOSED
        posting.closed_reason = (
            f"Ausente em {posting.missing_count} snapshots completos consecutivos."
        )
        _close_job_if_no_active_posting(session, posting.job)
        closed += 1
    return closed


def _close_job_if_no_active_posting(session: Session, job: Job | None) -> None:
    if job is None:
        return
    active_count = session.scalar(
        select(func.count(Posting.id)).where(
            Posting.job_id == job.id,
            Posting.is_active.is_(True),
        )
    )
    if active_count:
        return
    if _has_application(session, job.id):
        return
    if job.status is not JobStatus.DISMISSED:
        job.status = JobStatus.CLOSED
        job.updated_at = utc_now()


def _find_existing_posting(
    session: Session,
    source: Source,
    analysis: ItemAnalysis,
) -> Posting | None:
    posting = analysis.posting
    if posting.external_id:
        existing = session.scalar(
            select(Posting).where(
                Posting.source_id == source.id,
                Posting.external_id == posting.external_id,
            )
        )
        if existing is not None:
            return existing
    return session.scalar(
        select(Posting).where(
            Posting.source_id == source.id,
            Posting.normalized_url == analysis.normalized_url,
        )
    )


def _mark_posting_seen(posting: Posting, run: SourceRun) -> None:
    posting.source_run_id = run.id
    posting.last_seen_at = utc_now()
    posting.is_active = True
    posting.missing_count = 0
    posting.closed_reason = None
    if posting.status is PostingStatus.CLOSED:
        posting.status = PostingStatus.LINKED
    if posting.job is not None and posting.job.status is JobStatus.CLOSED:
        posting.job.status = JobStatus.NEW
        posting.job.updated_at = utc_now()


def _record_revision(
    session: Session,
    posting: Posting,
    analysis: ItemAnalysis,
    run: SourceRun,
) -> None:
    changed_fields = _changed_fields(posting, analysis.posting, analysis.content_hash)
    session.add(
        PostingRevision(
            posting_id=posting.id,
            previous_content_hash=posting.content_hash,
            new_content_hash=analysis.content_hash,
            changed_fields_json=json.dumps(changed_fields, ensure_ascii=False, sort_keys=True),
            observed_at=utc_now(),
            source_run_id=run.id,
        )
    )


def _update_existing_posting(
    session: Session,
    settings: Settings,
    posting: Posting,
    analysis: ItemAnalysis,
    run: SourceRun,
) -> None:
    item = analysis.posting
    posting.source_run_id = run.id
    posting.external_id = item.external_id
    posting.original_url = item.url or item.application_url or posting.original_url
    posting.normalized_url = analysis.normalized_url
    posting.raw_title = item.title
    posting.raw_company = item.company
    posting.raw_location = item.location or _location_from_item(item)
    posting.raw_description = item.description or ""
    posting.published_at = item.published_at
    posting.last_seen_at = utc_now()
    posting.content_hash = analysis.content_hash
    posting.is_active = True
    posting.missing_count = 0
    posting.closed_reason = None
    if posting.status is PostingStatus.CLOSED:
        posting.status = PostingStatus.LINKED

    if posting.job is None or not _may_refresh_job(session, posting.job):
        return
    job = posting.job
    job.canonical_title = item.title
    job.normalized_title = normalize_title(item.title)
    job.description = item.description_with_benefits()
    job.employment_type = item.employment_type
    job.work_model = item.work_model
    job.country = item.country
    job.state = normalize_state(item.state)
    job.city = normalize_city(item.city)
    job.remote_country_scope = item.remote_country_scope
    job.hours_per_day = item.hours_per_day
    job.hours_per_week = item.hours_per_week
    job.salary_min = item.salary_min
    job.salary_max = item.salary_max
    job.salary_period = item.salary_period
    job.currency = item.currency
    job.application_url = item.application_url or item.url
    job.published_at = item.published_at
    job.expires_at = item.expires_at
    if job.status is JobStatus.CLOSED:
        job.status = JobStatus.NEW
    job.updated_at = utc_now()
    evaluate_job_record(session, job, settings)


def _may_refresh_job(session: Session, job: Job) -> bool:
    if job.status in {JobStatus.APPLIED, JobStatus.DISMISSED}:
        return False
    return not _has_application(session, job.id)


def _has_application(session: Session, job_id: int) -> bool:
    value = session.scalar(select(func.count(Application.id)).where(Application.job_id == job_id))
    return bool(value)


def _changed_fields(
    posting: Posting,
    item: ImportedPosting,
    new_content_hash: str,
) -> dict[str, Any]:
    comparisons: dict[str, tuple[Any, Any]] = {
        "title": (posting.raw_title, item.title),
        "company": (posting.raw_company, item.company),
        "location": (posting.raw_location, item.location or _location_from_item(item)),
        "description": (posting.raw_description, item.description or ""),
        "published_at": (
            posting.published_at.isoformat() if posting.published_at else None,
            item.published_at.isoformat() if item.published_at else None,
        ),
        "content_hash": (posting.content_hash, new_content_hash),
    }
    return {
        field: short_metadata_change(old, new)
        for field, (old, new) in comparisons.items()
        if old != new
    }


def _parsed_from_items(
    context: CollectionContext,
    items: list[ImportedPosting] | tuple[ImportedPosting, ...] | Any,
) -> ParsedImportFile:
    parsed_items: list[ParsedImportItem] = []
    for index, item in enumerate(list(items), start=1):
        raw_fields = item.model_dump(mode="json")
        parsed_items.append(
            ParsedImportItem(
                line_number=None,
                item_index=index,
                raw_fields=raw_fields,
                posting=item,
                errors=[],
            )
        )
    input_file = Path(f"collection-{context.collector}-{context.board_key or 'direct'}.json")
    return ParsedImportFile(
        input_file=input_file,
        file_format="collection",
        schema_version="1.0",
        items=parsed_items,
    )


def _base_counters(result: CollectionResult, analyses: list[ItemAnalysis]) -> dict[str, int]:
    counters = {
        "found": result.found,
        "new": 0,
        "unchanged": 0,
        "changed": 0,
        "exact_duplicates": 0,
        "probable_duplicates": 0,
        "eligible": 0,
        "manual_review": 0,
        "ineligible": 0,
        "closed": 0,
        "reopened": 0,
    }
    for analysis in analyses:
        if analysis.eligibility_status is EligibilityStatus.ELIGIBLE:
            counters["eligible"] += 1
        elif analysis.eligibility_status is EligibilityStatus.MANUAL_REVIEW:
            counters["manual_review"] += 1
        elif analysis.eligibility_status is EligibilityStatus.INELIGIBLE:
            counters["ineligible"] += 1
    return counters


def _summary_from_counters(counters: dict[str, int]) -> CollectionSummary:
    return CollectionSummary(
        found=counters["found"],
        new=counters["new"],
        unchanged=counters["unchanged"],
        changed=counters["changed"],
        exact_duplicates=counters["exact_duplicates"],
        probable_duplicates=counters["probable_duplicates"],
        eligible=counters["eligible"],
        manual_review=counters["manual_review"],
        ineligible=counters["ineligible"],
        closed=counters["closed"],
        reopened=counters["reopened"],
    )


def _execution_report(
    context: CollectionContext,
    result: CollectionResult,
    started_at: datetime,
    finished_at: datetime,
    summary: CollectionSummary,
) -> CollectionExecutionReport:
    retries = result.metadata.get("retries", 0)
    return CollectionExecutionReport(
        collector=result.collector,
        board=context.board_key or context.board_token or context.url,
        started_at=started_at,
        finished_at=finished_at,
        dry_run=context.dry_run,
        network={
            "requests": result.requests,
            "bytes_received": result.bytes_received,
            "retries": retries if isinstance(retries, int) else 0,
        },
        summary=summary,
        warnings=result.warnings,
        errors=result.recoverable_errors,
        metadata={
            **result.metadata,
            "complete_snapshot": result.complete_snapshot,
            "partial": result.partial,
            "not_modified": result.not_modified,
            "status_code": result.status_code,
        },
    )


def _get_or_create_source_for_context(session: Session, context: CollectionContext) -> Source:
    slug = slugify_source_name(context.source_name)
    source = session.scalar(select(Source).where(Source.slug == slug))
    if source is not None:
        source.source_type = context.source_type
        source.is_active = True
        source.updated_at = utc_now()
        if board_url := board_url_for(context):
            source.base_url = board_url
        return source
    source = Source(
        name=context.source_name,
        slug=slug,
        source_type=context.source_type,
        base_url=board_url_for(context),
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(source)
    session.flush()
    return source


def _upsert_board(
    session: Session,
    settings: Settings,
    context: CollectionContext,
    source: Source,
    board_config: BoardConfig | None,
) -> CompanyBoard | None:
    if board_config is None or not board_config.key:
        return None
    company = _get_or_create_company_by_name(session, board_config.company_name, settings)
    board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board_config.key))
    if board is None:
        board = CompanyBoard(
            key=board_config.key,
            company_id=company.id,
            source_id=source.id,
            collector_type=board_config.collector,
            external_identifier=board_config.board_token or board_config.url,
            board_url=board_url_for(context),
            configuration_json="{}",
            is_active=board_config.enabled,
            consecutive_failures=0,
        )
        session.add(board)
    board.company_id = company.id
    board.source_id = source.id
    board.collector_type = board_config.collector
    board.external_identifier = board_config.board_token or board_config.url
    board.board_url = board_url_for(context)
    board.configuration_json = json.dumps(
        board_config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    board.is_active = board_config.enabled
    board.disabled_reason = None if board_config.enabled else "Desativado na configuracao YAML."
    return board


def _get_or_create_company_by_name(
    session: Session,
    company_name: str,
    settings: Settings,
) -> Company:
    posting = ImportedPosting(
        source_name="Board configuration",
        source_type="configuration",
        title="Board configuration",
        company=company_name,
    )
    return _get_or_create_company(session, posting, settings)


def _mark_board_success(board: CompanyBoard, run: SourceRun, result: CollectionResult) -> None:
    now = utc_now()
    board.last_checked_at = now
    board.last_success_at = now
    board.last_failed_at = None
    board.consecutive_failures = 0
    board.last_run_id = run.id
    if result.cache_etag:
        board.last_etag = result.cache_etag
    if result.cache_last_modified:
        board.last_modified = result.cache_last_modified
    if result.complete_snapshot and not result.partial and not result.not_modified:
        board.last_complete_snapshot_at = now


def _mark_board_failure(board: CompanyBoard, run: SourceRun) -> None:
    now = utc_now()
    board.last_checked_at = now
    board.last_failed_at = now
    board.consecutive_failures += 1
    board.last_run_id = run.id


def _source_name(collector: str, *, company_name: str | None, url: str | None) -> str:
    if collector == "greenhouse":
        return f"Greenhouse: {company_name or 'empresa'}"
    if collector == "lever":
        return f"Lever: {company_name or 'empresa'}"
    if collector == "jobposting":
        return f"JobPosting: {url or company_name or 'pagina'}"
    return collector


def _location_from_item(item: ImportedPosting) -> str:
    parts = [part for part in [item.city, item.state, item.country] if part]
    return ", ".join(parts) if parts else ""
