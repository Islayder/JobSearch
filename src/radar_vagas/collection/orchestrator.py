from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from radar_vagas.canonicalization.normalize import (
    normalize_city,
    normalize_state,
    normalize_title,
    normalize_url,
)
from radar_vagas.collection.contracts import CollectionContext, CollectionResult
from radar_vagas.collection.result import CollectionExecutionReport, CollectionSummary
from radar_vagas.collectors.common import short_metadata_change
from radar_vagas.config.schemas import BoardConfig, SearchQueryConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    CollectionAuthority,
    DuplicateKind,
    EligibilityStatus,
    JobStatus,
    PostingStatus,
    RelevanceStatus,
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
    DiscoveryHit,
    Job,
    Posting,
    PostingRevision,
    SearchQuery,
    Source,
    SourceRun,
)
from radar_vagas.relevance.service import (
    build_role_relevance_input_from_posting,
    normalize_technologies,
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
    authority: CollectionAuthority | None = None,
    query_key: str | None = None,
    query_mode: str | None = None,
    query_parameters: dict[str, Any] | None = None,
    max_pages: int | None = None,
) -> CollectionContext:
    source_name = _source_name(
        collector,
        board_key=board_key,
        board_token=board_token,
        url=url,
    )
    collection_scope_key = collection_scope_key_for(
        collector=collector,
        board_key=board_key,
        board_token=board_token,
        url=url,
    )
    resolved_authority = authority or _default_authority_for_collector(collector)
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
            collection_scope_key=collection_scope_key,
            authority=resolved_authority,
            query_key=query_key,
            query_mode=query_mode,
            query_parameters=query_parameters or {},
            dry_run=dry_run,
            max_items=max_items,
            max_pages=max_pages,
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
        collection_scope_key=collection_scope_key,
        authority=resolved_authority,
        query_key=query_key,
        query_mode=query_mode,
        query_parameters=query_parameters or {},
        dry_run=dry_run,
        max_items=max_items,
        max_pages=max_pages,
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
    search_query_config: SearchQueryConfig | None = None,
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

    board = _upsert_board(session, settings, context, source, board_config)
    search_query, query_warning = _upsert_search_query(session, context, search_query_config)
    summary = _persist_result_items(session, settings, context, result, source, run)
    if search_query is not None:
        _record_discovery_hits(session, search_query, run, context, result, source)
    run.items_created = summary.new
    run.items_skipped = summary.unchanged + summary.exact_duplicates + summary.invalid_items
    run.status = SourceRunStatus.SUCCESS
    run.finished_at = utc_now()

    if board is not None:
        _mark_board_success(board, run, result)
    if search_query is not None:
        _mark_search_query_success(search_query, run, result)

    report = _execution_report(context, result, started_at, run.finished_at, summary)
    if query_warning is not None:
        report = replace(report, warnings=[*report.warnings, query_warning])
    session.flush()
    return report


def record_failed_collection(
    session: Session,
    context: CollectionContext,
    error: Exception,
    *,
    board_config: BoardConfig | None = None,
    search_query_config: SearchQueryConfig | None = None,
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
    search_query, _query_warning = _upsert_search_query(session, context, search_query_config)
    if search_query is not None:
        _mark_search_query_failure(search_query, run)


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
    collection_scope_key = _collection_scope_key(context)

    if result.not_modified:
        return _summary_from_counters(counters)

    for analysis in analyses:
        existing = _find_existing_posting(session, source, analysis)
        if existing is not None:
            seen_posting_ids.add(existing.id)
            if _is_observation_only(context, existing, collection_scope_key):
                if existing.content_hash == analysis.content_hash:
                    counters["unchanged"] += 1
                    continue
                _record_revision(session, existing, analysis, run)
                _update_observed_posting_content(session, settings, existing, analysis)
                counters["changed"] += 1
                continue

            posting_scope_key = _scope_key_for_existing(context, existing, collection_scope_key)
            if existing.content_hash == analysis.content_hash:
                if not existing.is_active:
                    counters["reopened"] += 1
                _mark_posting_seen(
                    session,
                    settings,
                    existing,
                    run,
                    collection_scope_key=posting_scope_key,
                )
                counters["unchanged"] += 1
                continue
            if not existing.is_active:
                counters["reopened"] += 1
            _record_revision(session, existing, analysis, run)
            _update_existing_posting(
                session,
                settings,
                existing,
                analysis,
                run,
                collection_scope_key=posting_scope_key,
            )
            counters["changed"] += 1
            continue

        if analysis.duplicate_kind is DuplicateKind.EXACT:
            counters["exact_duplicates"] += 1
            continue

        company = _get_or_create_company(session, analysis.posting, settings)
        job, duplicate_kind = _get_or_create_job(session, company, analysis)
        posting = _create_posting(session, source, run, job, analysis)
        posting.collection_scope_key = collection_scope_key
        posting.is_active = True
        posting.missing_count = 0
        posting.closed_reason = None
        evaluate_job_record(session, job, settings)
        seen_posting_ids.add(posting.id)
        counters["new"] += 1
        if duplicate_kind is DuplicateKind.PROBABLE:
            counters["probable_duplicates"] += 1

    if _can_increment_absences(context, result):
        counters["closed"] += _increment_absences(
            session,
            collection_scope_key=collection_scope_key,
            seen_posting_ids=seen_posting_ids,
            close_after=settings_close_after(context),
        )

    return _summary_from_counters(counters)


def settings_close_after(context: CollectionContext) -> int:
    return context.collection_config.close_after_missing_successful_runs


def _increment_absences(
    session: Session,
    *,
    collection_scope_key: str,
    seen_posting_ids: set[int],
    close_after: int,
) -> int:
    statement = select(Posting).where(
        Posting.collection_scope_key == collection_scope_key,
        Posting.is_active.is_(True),
    )
    if seen_posting_ids:
        statement = statement.where(Posting.id.not_in(seen_posting_ids))
    closed = 0
    for posting in session.scalars(statement).all():
        posting.missing_count += 1
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


def _scope_key_for_existing(
    context: CollectionContext,
    posting: Posting,
    collection_scope_key: str,
) -> str:
    if context.authority is CollectionAuthority.DISCOVERY_QUERY and posting.collection_scope_key:
        return posting.collection_scope_key
    return collection_scope_key


def _is_observation_only(
    context: CollectionContext,
    posting: Posting,
    collection_scope_key: str,
) -> bool:
    return (
        context.authority is CollectionAuthority.DISCOVERY_QUERY
        and posting.collection_scope_key is not None
        and posting.collection_scope_key != collection_scope_key
    )


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
    if posting.provider_identity_key:
        existing = session.scalar(
            select(Posting).where(Posting.provider_identity_key == posting.provider_identity_key)
        )
        if existing is not None:
            return existing
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


def _mark_posting_seen(
    session: Session,
    settings: Settings,
    posting: Posting,
    run: SourceRun,
    *,
    collection_scope_key: str,
) -> None:
    was_inactive = not posting.is_active
    posting.source_run_id = run.id
    posting.collection_scope_key = collection_scope_key
    posting.last_seen_at = utc_now()
    posting.is_active = True
    posting.missing_count = 0
    posting.closed_reason = None
    if posting.status is PostingStatus.CLOSED:
        posting.status = PostingStatus.LINKED
    if posting.job is None or not _may_refresh_job(session, posting.job):
        return
    if posting.job.status is JobStatus.CLOSED:
        posting.job.status = JobStatus.NEW
        posting.job.updated_at = utc_now()
    if was_inactive:
        evaluate_job_record(session, posting.job, settings)


def _update_observed_posting_content(
    session: Session,
    settings: Settings,
    posting: Posting,
    analysis: ItemAnalysis,
) -> None:
    item = analysis.posting
    relevance_input = build_role_relevance_input_from_posting(item)
    posting.provider = posting.provider or item.provider
    posting.provider_scope = posting.provider_scope or item.provider_scope
    posting.provider_external_id = posting.provider_external_id or item.provider_external_id
    if posting.provider_identity_key is None:
        posting.provider_identity_key = item.provider_identity_key
    posting.external_id = item.external_id or posting.external_id
    posting.original_url = item.url or item.application_url or posting.original_url
    posting.normalized_url = analysis.normalized_url
    posting.raw_title = item.title
    posting.raw_company = item.company
    posting.raw_location = item.location or _location_from_item(item)
    posting.raw_description = item.description or ""
    posting.raw_department = relevance_input.department
    posting.raw_area = relevance_input.area
    posting.raw_requirements = relevance_input.requirements
    posting.raw_responsibilities = relevance_input.responsibilities
    posting.raw_technologies_json = _technologies_json(relevance_input.technologies)
    posting.published_at = item.published_at
    posting.content_hash = analysis.content_hash

    if posting.job is None or not _may_refresh_job(session, posting.job):
        return
    _refresh_job_content_from_item(posting.job, item)
    evaluate_job_record(session, posting.job, settings)


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
    *,
    collection_scope_key: str,
) -> None:
    item = analysis.posting
    relevance_input = build_role_relevance_input_from_posting(item)
    posting.source_run_id = run.id
    posting.collection_scope_key = collection_scope_key
    posting.provider = item.provider or posting.provider
    posting.provider_scope = item.provider_scope or posting.provider_scope
    posting.provider_external_id = item.provider_external_id or posting.provider_external_id
    if posting.provider_identity_key is None:
        posting.provider_identity_key = item.provider_identity_key
    posting.external_id = item.external_id
    posting.original_url = item.url or item.application_url or posting.original_url
    posting.normalized_url = analysis.normalized_url
    posting.raw_title = item.title
    posting.raw_company = item.company
    posting.raw_location = item.location or _location_from_item(item)
    posting.raw_description = item.description or ""
    posting.raw_department = relevance_input.department
    posting.raw_area = relevance_input.area
    posting.raw_requirements = relevance_input.requirements
    posting.raw_responsibilities = relevance_input.responsibilities
    posting.raw_technologies_json = _technologies_json(relevance_input.technologies)
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
    _refresh_job_content_from_item(job, item)
    if job.status is JobStatus.CLOSED:
        job.status = JobStatus.NEW
    job.updated_at = utc_now()
    evaluate_job_record(session, job, settings)


def _refresh_job_content_from_item(job: Job, item: ImportedPosting) -> None:
    relevance_input = build_role_relevance_input_from_posting(item)
    job.canonical_title = item.title
    job.normalized_title = normalize_title(item.title)
    job.description = item.description_with_benefits()
    job.department = relevance_input.department
    job.area = relevance_input.area
    job.requirements = relevance_input.requirements
    job.responsibilities = relevance_input.responsibilities
    job.technologies_json = _technologies_json(relevance_input.technologies)
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
    relevance_input = build_role_relevance_input_from_posting(item)
    comparisons: dict[str, tuple[Any, Any]] = {
        "title": (posting.raw_title, item.title),
        "company": (posting.raw_company, item.company),
        "location": (posting.raw_location, item.location or _location_from_item(item)),
        "description": (
            _safe_text_change(posting.raw_description),
            _safe_text_change(item.description or ""),
        ),
        "department": (posting.raw_department, relevance_input.department),
        "area": (posting.raw_area, relevance_input.area),
        "requirements": (
            _safe_text_change(posting.raw_requirements),
            _safe_text_change(relevance_input.requirements),
        ),
        "responsibilities": (
            _safe_text_change(posting.raw_responsibilities),
            _safe_text_change(relevance_input.responsibilities),
        ),
        "technologies": (
            _technologies_list(posting.raw_technologies_json),
            list(normalize_technologies(relevance_input.technologies)),
        ),
        "work_model": (
            posting.job.work_model.value if posting.job else None,
            item.work_model.value,
        ),
        "employment_type": (
            posting.job.employment_type.value if posting.job else None,
            item.employment_type.value,
        ),
        "expires_at": (
            posting.job.expires_at.isoformat() if posting.job and posting.job.expires_at else None,
            item.expires_at.isoformat() if item.expires_at else None,
        ),
        "provider_identity_key": (posting.provider_identity_key, item.provider_identity_key),
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


def _safe_text_change(value: str | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "length": len(value),
        "sha256": sha256(value.encode("utf-8")).hexdigest(),
    }


def _parsed_from_items(
    context: CollectionContext,
    items: list[ImportedPosting] | tuple[ImportedPosting, ...] | Any,
) -> ParsedImportFile:
    parsed_items: list[ParsedImportItem] = []
    for index, item in enumerate(list(items), start=1):
        item = item.model_copy(
            update={"source_name": context.source_name, "source_type": context.source_type}
        )
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
        "core": 0,
        "adjacent": 0,
        "unrelated": 0,
        "closed": 0,
        "reopened": 0,
        "invalid_items": len(result.invalid_items),
    }
    for analysis in analyses:
        if analysis.eligibility_status is EligibilityStatus.ELIGIBLE:
            counters["eligible"] += 1
        elif analysis.eligibility_status is EligibilityStatus.MANUAL_REVIEW:
            counters["manual_review"] += 1
        elif analysis.eligibility_status is EligibilityStatus.INELIGIBLE:
            counters["ineligible"] += 1
        if analysis.relevance_status is RelevanceStatus.CORE:
            counters["core"] += 1
        elif analysis.relevance_status is RelevanceStatus.ADJACENT:
            counters["adjacent"] += 1
        elif analysis.relevance_status is RelevanceStatus.UNRELATED:
            counters["unrelated"] += 1
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
        core=counters["core"],
        adjacent=counters["adjacent"],
        unrelated=counters["unrelated"],
        closed=counters["closed"],
        reopened=counters["reopened"],
        invalid_items=counters["invalid_items"],
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
            "authority": context.authority.value.lower(),
            "collection_scope_key": _collection_scope_key(context),
            "complete_snapshot": result.complete_snapshot,
            "partial": result.partial,
            "not_modified": result.not_modified,
            "status_code": result.status_code,
            "invalid_item_details": result.invalid_items,
        },
    )


def _get_or_create_source_for_context(session: Session, context: CollectionContext) -> Source:
    slug = _collection_scope_key(context)
    source = session.scalar(select(Source).where(Source.slug == slug))
    if source is not None:
        source.name = context.source_name
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
    collection_scope_key = _collection_scope_key(context)
    company = _get_or_create_company_by_name(session, board_config.company_name, settings)
    board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board_config.key))
    if board is None:
        board = CompanyBoard(
            key=board_config.key,
            company_id=company.id,
            source_id=source.id,
            collector_type=board_config.collector,
            collection_scope_key=collection_scope_key,
            external_identifier=board_config.board_token or board_config.url,
            board_url=board_url_for(context),
            configuration_json="{}",
            is_active=board_config.enabled,
            consecutive_failures=0,
        )
        session.add(board)
    else:
        _migrate_exclusive_legacy_board_postings(
            session,
            board=board,
            old_source_id=board.source_id,
            old_scope_key=board.collection_scope_key,
            new_source_id=source.id,
            new_scope_key=collection_scope_key,
        )
    board.company_id = company.id
    board.source_id = source.id
    board.collector_type = board_config.collector
    board.collection_scope_key = collection_scope_key
    board.external_identifier = board_config.board_token or board_config.url
    board.board_url = board_url_for(context)
    board.configuration_json = json.dumps(
        board_config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    board.is_active = board_config.enabled
    board.disabled_reason = None if board_config.enabled else "Desativado na configuracao YAML."
    return board


def _upsert_search_query(
    session: Session,
    context: CollectionContext,
    query_config: SearchQueryConfig | None,
) -> tuple[SearchQuery | None, str | None]:
    if query_config is None:
        return None, None
    query = session.scalar(select(SearchQuery).where(SearchQuery.key == query_config.key))
    configuration_json = json.dumps(
        query_config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    warning: str | None = None
    if query is None:
        query = SearchQuery(
            key=query_config.key,
            collector_type=query_config.collector,
            mode=query_config.mode,
            configuration_json=configuration_json,
            configuration_fingerprint=query_config.configuration_fingerprint,
            collection_scope_key=query_config.collection_scope_key,
            is_active=query_config.enabled,
            priority=query_config.priority,
            tags_json=json.dumps(query_config.tags, ensure_ascii=False, sort_keys=True),
            consecutive_failures=0,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(query)
    elif query.configuration_fingerprint != query_config.configuration_fingerprint:
        warning = (
            "Fingerprint da consulta mudou desde a ultima persistencia; "
            "ausencias antigas nao serao usadas para fechamento."
        )

    query.collector_type = query_config.collector
    query.mode = query_config.mode
    query.configuration_json = configuration_json
    query.configuration_fingerprint = query_config.configuration_fingerprint
    query.collection_scope_key = query_config.collection_scope_key
    query.is_active = query_config.enabled
    query.priority = query_config.priority
    query.tags_json = json.dumps(query_config.tags, ensure_ascii=False, sort_keys=True)
    query.disabled_reason = None if query_config.enabled else "Desativada na configuracao YAML."
    query.updated_at = utc_now()
    session.flush()
    return query, warning


def _record_discovery_hits(
    session: Session,
    search_query: SearchQuery,
    run: SourceRun,
    context: CollectionContext,
    result: CollectionResult,
    source: Source,
) -> None:
    seen_keys: set[str] = set()
    for index, item in enumerate(result.items, start=1):
        provider_key = item.provider_identity_key
        dedupe_key = provider_key or f"item-{index}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        posting = _find_posting_for_hit(session, source, item)
        metadata = _discovery_hit_metadata(context, item)
        session.add(
            DiscoveryHit(
                search_query_id=search_query.id,
                source_run_id=run.id,
                posting_id=posting.id if posting is not None else None,
                job_id=posting.job_id if posting is not None else None,
                provider_identity_key=provider_key,
                position_in_results=_metadata_int(item.metadata.get("position_in_results")),
                page_number=_metadata_int(item.metadata.get("page_number")),
                observed_at=utc_now(),
                match_status=_hit_match_status(posting, run),
                metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            )
        )


def _find_posting_for_hit(
    session: Session,
    source: Source,
    item: ImportedPosting,
) -> Posting | None:
    if item.provider_identity_key:
        posting = session.scalar(
            select(Posting).where(Posting.provider_identity_key == item.provider_identity_key)
        )
        if posting is not None:
            return posting
    if item.external_id:
        posting = session.scalar(
            select(Posting).where(
                Posting.source_id == source.id,
                Posting.external_id == item.external_id,
            )
        )
        if posting is not None:
            return posting
    normalized_url = normalize_url(item.url or item.application_url or "")
    if normalized_url:
        return session.scalar(
            select(Posting).where(
                Posting.source_id == source.id,
                Posting.normalized_url == normalized_url,
            )
        )
    return None


def _discovery_hit_metadata(
    context: CollectionContext,
    item: ImportedPosting,
) -> dict[str, object]:
    return {
        "query_key": context.query_key,
        "collector": context.collector,
        "mode": context.query_mode,
        "title": item.title,
        "company": item.company,
        "public_url": item.url or item.application_url,
        "page_number": _metadata_int(item.metadata.get("page_number")),
        "position_in_results": _metadata_int(item.metadata.get("position_in_results")),
    }


def _metadata_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _hit_match_status(posting: Posting | None, run: SourceRun) -> str:
    if posting is None:
        return "unmatched"
    if not posting.is_active or posting.status is PostingStatus.CLOSED:
        return "lifecycle_conflict"
    if _aware_datetime(posting.first_seen_at) >= _aware_datetime(run.started_at):
        return "new"
    if posting.revisions:
        latest_revision = max(posting.revisions, key=lambda revision: revision.observed_at)
        if latest_revision.source_run_id == run.id:
            return "changed"
    return "known"


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _mark_search_query_success(
    search_query: SearchQuery,
    run: SourceRun,
    result: CollectionResult,
) -> None:
    now = utc_now()
    search_query.last_checked_at = now
    search_query.last_success_at = now
    search_query.last_failed_at = None
    search_query.consecutive_failures = 0
    search_query.last_run_id = run.id
    if not result.partial and not bool(result.metadata.get("truncated")):
        search_query.last_complete_page_at = now
    search_query.updated_at = now


def _mark_search_query_failure(search_query: SearchQuery, run: SourceRun) -> None:
    now = utc_now()
    search_query.last_checked_at = now
    search_query.last_failed_at = now
    search_query.consecutive_failures += 1
    search_query.last_run_id = run.id
    search_query.updated_at = now


def _migrate_exclusive_legacy_board_postings(
    session: Session,
    *,
    board: CompanyBoard,
    old_source_id: int,
    old_scope_key: str | None,
    new_source_id: int,
    new_scope_key: str,
) -> None:
    if old_source_id == new_source_id and old_scope_key == new_scope_key:
        return
    shared_boards = session.scalar(
        select(func.count(CompanyBoard.id)).where(
            CompanyBoard.source_id == old_source_id,
            CompanyBoard.id != board.id,
        )
    )
    if shared_boards:
        return

    if old_scope_key:
        scope_filter = or_(
            Posting.collection_scope_key.is_(None),
            Posting.collection_scope_key == old_scope_key,
        )
    else:
        scope_filter = Posting.collection_scope_key.is_(None)
    postings = session.scalars(
        select(Posting).where(
            Posting.source_id == old_source_id,
            scope_filter,
        )
    ).all()
    for posting in postings:
        posting.source_id = new_source_id
        posting.collection_scope_key = new_scope_key


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


def collection_scope_key_for(
    *,
    collector: str,
    board_key: str | None,
    board_token: str | None,
    url: str | None,
) -> str:
    collector_slug = slugify_source_name(collector.strip().lower())
    if board_key:
        raw_key = f"{collector_slug}-board-{board_key}"
    elif board_token:
        raw_key = f"{collector_slug}-token-{board_token}"
    elif url:
        raw_key = f"{collector_slug}-url-{_url_digest(url)}"
    else:
        raw_key = f"{collector_slug}-direct"
    return _bounded_slug(raw_key)


def _default_authority_for_collector(collector: str) -> CollectionAuthority:
    normalized = collector.strip().lower()
    if normalized in {"greenhouse", "lever"}:
        return CollectionAuthority.AUTHORITATIVE_BOARD
    if normalized == "gupy":
        return CollectionAuthority.DISCOVERY_QUERY
    return CollectionAuthority.SINGLE_PAGE


def _collection_scope_key(context: CollectionContext) -> str:
    return context.collection_scope_key or collection_scope_key_for(
        collector=context.collector,
        board_key=context.board_key,
        board_token=context.board_token,
        url=context.url,
    )


def _can_increment_absences(context: CollectionContext, result: CollectionResult) -> bool:
    return (
        context.authority is CollectionAuthority.AUTHORITATIVE_BOARD
        and result.complete_snapshot
        and not result.partial
        and not result.not_modified
        and not result.invalid_items
        and not bool(result.metadata.get("truncated"))
    )


def _source_name(
    collector: str,
    *,
    board_key: str | None,
    board_token: str | None,
    url: str | None,
) -> str:
    label = {"greenhouse": "Greenhouse", "lever": "Lever", "jobposting": "JobPosting"}.get(
        collector,
        collector,
    )
    if board_key:
        return f"{label} board {board_key}"
    if board_token:
        return f"{label} token {board_token}"
    if url:
        return f"{label} url {_url_digest(url)}"
    return f"{label} direct"


def _bounded_slug(value: str, *, max_length: int = 120) -> str:
    slug = slugify_source_name(value)
    if len(slug) <= max_length:
        return slug
    digest = sha256(slug.encode("utf-8")).hexdigest()[:12]
    return f"{slug[: max_length - 13].rstrip('-')}-{digest}"


def _url_digest(url: str) -> str:
    normalized = normalize_url(url)
    return sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _location_from_item(item: ImportedPosting) -> str:
    parts = [part for part in [item.city, item.state, item.country] if part]
    return ", ".join(parts) if parts else ""


def _technologies_json(value: object) -> str:
    return json.dumps(
        list(normalize_technologies(value)),
        ensure_ascii=False,
        sort_keys=True,
    )


def _technologies_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return list(normalize_technologies(decoded))
