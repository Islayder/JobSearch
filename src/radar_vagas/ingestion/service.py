import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar_vagas.canonicalization.normalize import (
    generate_content_hash,
    normalize_city,
    normalize_company_name,
    normalize_state,
    normalize_title,
    normalize_url,
)
from radar_vagas.config.loaders import blocked_company_reasons
from radar_vagas.config.settings import Settings
from radar_vagas.deduplication.service import (
    DuplicateCandidate,
    classify_canonical_duplicate,
)
from radar_vagas.domain.enums import DuplicateKind, JobStatus, PostingStatus, SourceRunStatus
from radar_vagas.domain.errors import RadarError
from radar_vagas.domain.time import utc_now
from radar_vagas.ingestion.schemas import FixtureFile, FixturePosting, FixtureSource
from radar_vagas.persistence.models import (
    Company,
    CompanyAlias,
    Job,
    Posting,
    Source,
    SourceRun,
)


@dataclass(frozen=True)
class ImportSummary:
    items_found: int
    postings_created: int
    postings_skipped: int
    jobs_created: int
    jobs_linked: int
    probable_duplicates: int
    sources_created: int
    companies_created: int


def import_fixture(session: Session, fixture_path: Path, settings: Settings) -> ImportSummary:
    fixture = load_fixture(fixture_path)
    blocked_reasons = blocked_company_reasons(settings.config_dir)
    runs_by_source_id: dict[int, SourceRun] = {}
    summary = _MutableImportSummary(items_found=len(fixture.items))

    for item in fixture.items:
        source = _get_or_create_source(session, item.source, summary)
        run = _get_or_create_run(session, source, runs_by_source_id)
        run.items_found += 1
        company = _get_or_create_company(session, item.raw_company, blocked_reasons, summary)

        normalized_url = normalize_url(item.original_url)
        content_hash = _posting_content_hash(item, normalized_url)
        duplicate = _find_duplicate_posting(
            session,
            source_id=source.id,
            external_id=item.external_id,
            normalized_url=normalized_url,
            content_hash=content_hash,
        )
        if duplicate is not None:
            duplicate.last_seen_at = utc_now()
            run.items_skipped += 1
            summary.postings_skipped += 1
            continue

        job, duplicate_kind = _get_or_create_job(session, company, item, content_hash)
        if duplicate_kind is DuplicateKind.EXACT:
            summary.jobs_linked += 1
            posting_status = PostingStatus.LINKED
        elif duplicate_kind is DuplicateKind.PROBABLE:
            summary.probable_duplicates += 1
            summary.jobs_created += 1
            posting_status = PostingStatus.PROBABLE_DUPLICATE
        else:
            summary.jobs_created += 1
            posting_status = PostingStatus.NEW

        posting = Posting(
            source_id=source.id,
            source_run_id=run.id,
            external_id=item.external_id,
            original_url=item.original_url,
            normalized_url=normalized_url,
            raw_title=item.raw_title,
            raw_company=item.raw_company,
            raw_location=item.raw_location,
            raw_description=item.raw_description,
            published_at=item.published_at,
            first_seen_at=utc_now(),
            last_seen_at=utc_now(),
            content_hash=content_hash,
            status=posting_status,
            job_id=job.id,
        )
        session.add(posting)
        run.items_created += 1
        summary.postings_created += 1

    for run in runs_by_source_id.values():
        run.finished_at = utc_now()
        run.status = SourceRunStatus.SUCCESS

    return summary.to_frozen()


def load_fixture(fixture_path: Path) -> FixtureFile:
    if not fixture_path.exists():
        raise RadarError(f"Arquivo não encontrado: {fixture_path}")
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RadarError(f"JSON inválido em {fixture_path}: {exc.msg}") from exc
    try:
        return FixtureFile.model_validate(payload)
    except ValidationError as exc:
        raise RadarError(f"Fixture inválida em {fixture_path}: {exc}") from exc


@dataclass
class _MutableImportSummary:
    items_found: int
    postings_created: int = 0
    postings_skipped: int = 0
    jobs_created: int = 0
    jobs_linked: int = 0
    probable_duplicates: int = 0
    sources_created: int = 0
    companies_created: int = 0

    def to_frozen(self) -> ImportSummary:
        return ImportSummary(
            items_found=self.items_found,
            postings_created=self.postings_created,
            postings_skipped=self.postings_skipped,
            jobs_created=self.jobs_created,
            jobs_linked=self.jobs_linked,
            probable_duplicates=self.probable_duplicates,
            sources_created=self.sources_created,
            companies_created=self.companies_created,
        )


def _get_or_create_source(
    session: Session, source_data: FixtureSource, summary: _MutableImportSummary
) -> Source:
    source = session.scalar(select(Source).where(Source.slug == source_data.slug))
    if source is not None:
        return source
    source = Source(
        name=source_data.name,
        slug=source_data.slug,
        source_type=source_data.source_type,
        base_url=source_data.base_url,
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(source)
    session.flush()
    summary.sources_created += 1
    return source


def _get_or_create_run(
    session: Session,
    source: Source,
    runs_by_source_id: dict[int, SourceRun],
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
    session: Session,
    raw_company: str,
    blocked_reasons: dict[str, str],
    summary: _MutableImportSummary,
) -> Company:
    normalized = normalize_company_name(raw_company)
    alias = session.scalar(select(CompanyAlias).where(CompanyAlias.normalized_alias == normalized))
    company: Company | None
    if alias is not None:
        company = alias.company
    else:
        company = session.scalar(select(Company).where(Company.normalized_name == normalized))

    if company is None:
        company = Company(
            canonical_name=raw_company,
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
                alias=raw_company,
                normalized_alias=normalized,
            )
        )
        summary.companies_created += 1
    elif alias is None:
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias=raw_company,
                normalized_alias=normalized,
            )
        )

    blocked_reason = blocked_reasons.get(normalized)
    if blocked_reason is not None:
        company.is_blocked = True
        company.blocked_reason = blocked_reason
    return company


def _find_duplicate_posting(
    session: Session,
    *,
    source_id: int,
    external_id: str | None,
    normalized_url: str,
    content_hash: str,
) -> Posting | None:
    if external_id:
        duplicate = session.scalar(
            select(Posting).where(
                Posting.source_id == source_id,
                Posting.external_id == external_id,
            )
        )
        if duplicate is not None:
            return duplicate

    duplicate = session.scalar(
        select(Posting).where(
            Posting.source_id == source_id,
            Posting.normalized_url == normalized_url,
        )
    )
    if duplicate is not None:
        return duplicate

    return session.scalar(select(Posting).where(Posting.content_hash == content_hash))


def _get_or_create_job(
    session: Session,
    company: Company,
    item: FixturePosting,
    content_hash: str,
) -> tuple[Job, DuplicateKind]:
    candidate = _candidate_from_item(company.normalized_name, item, content_hash)
    exact_job: Job | None = None
    probable_found = False

    existing_jobs = session.scalars(
        select(Job).where(
            Job.company_id == company.id,
            Job.normalized_title == candidate.normalized_title,
            Job.work_model == item.work_model,
        )
    ).all()
    for existing_job in existing_jobs:
        duplicate_kind = classify_canonical_duplicate(
            candidate,
            _candidate_from_job(company.normalized_name, existing_job),
        )
        if duplicate_kind is DuplicateKind.EXACT:
            exact_job = existing_job
            break
        if duplicate_kind is DuplicateKind.PROBABLE:
            probable_found = True

    if exact_job is not None:
        return exact_job, DuplicateKind.EXACT

    job = Job(
        company_id=company.id,
        canonical_title=item.raw_title,
        normalized_title=normalize_title(item.raw_title),
        description=item.raw_description,
        employment_type=item.employment_type,
        seniority=item.seniority,
        work_model=item.work_model,
        country=item.country,
        state=normalize_state(item.state),
        city=normalize_city(item.city),
        remote_country_scope=item.remote_country_scope,
        hours_per_day=item.hours_per_day,
        hours_per_week=item.hours_per_week,
        salary_min=item.salary_min,
        salary_max=item.salary_max,
        salary_period=item.salary_period,
        currency=item.currency,
        application_url=item.application_url or item.original_url,
        published_at=item.published_at,
        expires_at=item.expires_at,
        status=JobStatus.NEW,
        course_requirement=item.course_requirement,
        has_uninterpreted_course_requirement=item.has_uninterpreted_course_requirement,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(job)
    session.flush()
    return job, DuplicateKind.PROBABLE if probable_found else DuplicateKind.DISTINCT


def _candidate_from_item(
    normalized_company: str, item: FixturePosting, content_hash: str
) -> DuplicateCandidate:
    return DuplicateCandidate(
        normalized_company=normalized_company,
        normalized_title=normalize_title(item.raw_title),
        city=normalize_city(item.city),
        work_model=item.work_model,
        published_at=item.published_at,
        application_url=item.application_url or item.original_url,
        content_hash=content_hash,
    )


def _candidate_from_job(normalized_company: str, job: Job) -> DuplicateCandidate:
    first_posting_hash = job.postings[0].content_hash if job.postings else None
    return DuplicateCandidate(
        normalized_company=normalized_company,
        normalized_title=job.normalized_title,
        city=job.city,
        work_model=job.work_model,
        published_at=job.published_at,
        application_url=job.application_url,
        content_hash=first_posting_hash,
    )


def _posting_content_hash(item: FixturePosting, normalized_url: str) -> str:
    return generate_content_hash(
        [
            item.source.slug,
            item.external_id,
            normalized_url,
            item.raw_title,
            item.raw_company,
            item.raw_location,
            item.raw_description,
        ]
    )
