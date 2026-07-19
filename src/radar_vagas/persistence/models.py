from datetime import datetime
from enum import Enum as PythonEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationMatchKind,
    ApplicationMatchStatus,
    ApplicationStage,
    ApplicationStatus,
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    PostingStatus,
    ProfileEvidenceType,
    RelevanceStatus,
    RequirementKind,
    RequirementMatchStatus,
    ReviewEventType,
    ReviewState,
    SourceRunStatus,
    WorkModel,
)
from radar_vagas.domain.time import utc_now


class Base(DeclarativeBase):
    pass


def enum_type(enum_class: type[PythonEnum]) -> SAEnum:
    return SAEnum(
        enum_class,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    runs: Mapped[list["SourceRun"]] = relationship(back_populates="source")
    postings: Mapped[list["Posting"]] = relationship(back_populates="source")
    boards: Mapped[list["CompanyBoard"]] = relationship(back_populates="source")


class SourceRun(Base):
    __tablename__ = "source_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SourceRunStatus] = mapped_column(
        enum_type(SourceRunStatus), default=SourceRunStatus.RUNNING, nullable=False
    )
    items_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="runs")
    postings: Mapped[list["Posting"]] = relationship(back_populates="source_run")
    search_queries: Mapped[list["SearchQuery"]] = relationship(
        back_populates="last_run", foreign_keys="SearchQuery.last_run_id"
    )
    discovery_hits: Mapped[list["DiscoveryHit"]] = relationship(back_populates="source_run")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    website: Mapped[str | None] = mapped_column(String(1000))
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    aliases: Mapped[list["CompanyAlias"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    boards: Mapped[list["CompanyBoard"]] = relationship(back_populates="company")
    jobs: Mapped[list["Job"]] = relationship(back_populates="company")
    email_messages: Mapped[list["EmailMessage"]] = relationship(back_populates="company")


class CompanyAlias(Base):
    __tablename__ = "company_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    company: Mapped[Company] = relationship(back_populates="aliases")


class CompanyBoard(Base):
    __tablename__ = "company_boards"
    __table_args__ = (
        Index("ix_company_boards_key", "key", unique=True),
        Index("ix_company_boards_collector_type", "collector_type"),
        Index("ix_company_boards_collection_scope_key", "collection_scope_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    key: Mapped[str | None] = mapped_column(String(120))
    collector_type: Mapped[str | None] = mapped_column(String(80))
    collection_scope_key: Mapped[str | None] = mapped_column(String(120))
    external_identifier: Mapped[str | None] = mapped_column(String(255))
    board_url: Mapped[str | None] = mapped_column(String(1000))
    configuration_json: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_etag: Mapped[str | None] = mapped_column(String(1000))
    last_modified: Mapped[str | None] = mapped_column(String(1000))
    last_complete_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("source_runs.id"), index=True)
    disabled_reason: Mapped[str | None] = mapped_column(Text)

    company: Mapped[Company] = relationship(back_populates="boards")
    source: Mapped[Source] = relationship(back_populates="boards")
    last_run: Mapped[SourceRun | None] = relationship(foreign_keys=[last_run_id])


class SearchQuery(Base):
    __tablename__ = "search_queries"
    __table_args__ = (
        Index("ix_search_queries_key", "key", unique=True),
        Index("ix_search_queries_collector_mode", "collector_type", "mode"),
        Index("ix_search_queries_collection_scope_key", "collection_scope_key"),
        Index("ix_search_queries_active_priority", "is_active", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    collector_type: Mapped[str] = mapped_column(String(80), nullable=False)
    mode: Mapped[str] = mapped_column(String(80), nullable=False)
    configuration_json: Mapped[str] = mapped_column(Text, nullable=False)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    collection_scope_key: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("source_runs.id"), index=True)
    last_complete_page_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    last_run: Mapped[SourceRun | None] = relationship(
        back_populates="search_queries", foreign_keys=[last_run_id]
    )
    hits: Mapped[list["DiscoveryHit"]] = relationship(
        back_populates="search_query", cascade="all, delete-orphan"
    )


class Posting(Base):
    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_postings_source_external_id"),
        UniqueConstraint("source_id", "normalized_url", name="uq_postings_source_normalized_url"),
        UniqueConstraint("provider_identity_key", name="uq_postings_provider_identity_key"),
        UniqueConstraint("content_hash", name="uq_postings_content_hash"),
        Index("ix_postings_status", "status"),
        Index("ix_postings_provider_identity_key", "provider_identity_key"),
        Index("ix_postings_active_missing", "is_active", "missing_count"),
        Index("ix_postings_collection_scope_active", "collection_scope_key", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("source_runs.id"), index=True)
    collection_scope_key: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(80))
    provider_scope: Mapped[str | None] = mapped_column(String(255))
    provider_external_id: Mapped[str | None] = mapped_column(String(500))
    provider_identity_key: Mapped[str | None] = mapped_column(String(1200))
    external_id: Mapped[str | None] = mapped_column(String(255))
    original_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    raw_title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_company: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_location: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
    raw_department: Mapped[str | None] = mapped_column(String(500))
    raw_area: Mapped[str | None] = mapped_column(String(500))
    raw_requirements: Mapped[str | None] = mapped_column(Text)
    raw_responsibilities: Mapped[str | None] = mapped_column(Text)
    raw_technologies_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[PostingStatus] = mapped_column(
        enum_type(PostingStatus), default=PostingStatus.NEW, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    closed_reason: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)

    source: Mapped[Source] = relationship(back_populates="postings")
    source_run: Mapped[SourceRun | None] = relationship(back_populates="postings")
    job: Mapped["Job | None"] = relationship(back_populates="postings")
    import_audits: Mapped[list["ImportItemAudit"]] = relationship(back_populates="posting")
    revisions: Mapped[list["PostingRevision"]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )
    discovery_hits: Mapped[list["DiscoveryHit"]] = relationship(back_populates="posting")


class PostingRevision(Base):
    __tablename__ = "posting_revisions"
    __table_args__ = (
        Index("ix_posting_revisions_posting_id", "posting_id"),
        Index("ix_posting_revisions_source_run_id", "source_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id"), nullable=False)
    previous_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    new_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("source_runs.id"))

    posting: Mapped[Posting] = relationship(back_populates="revisions")
    source_run: Mapped[SourceRun | None] = relationship()


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_employment_type", "employment_type"),
        Index("ix_jobs_work_model", "work_model"),
        Index("ix_jobs_city", "city"),
        Index("ix_jobs_company_title", "company_id", "normalized_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    canonical_title: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(String(500))
    area: Mapped[str | None] = mapped_column(String(500))
    requirements: Mapped[str | None] = mapped_column(Text)
    responsibilities: Mapped[str | None] = mapped_column(Text)
    technologies_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    employment_type: Mapped[EmploymentType] = mapped_column(
        enum_type(EmploymentType), default=EmploymentType.UNKNOWN, nullable=False
    )
    seniority: Mapped[str | None] = mapped_column(String(120))
    work_model: Mapped[WorkModel] = mapped_column(
        enum_type(WorkModel), default=WorkModel.UNKNOWN, nullable=False
    )
    country: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    remote_country_scope: Mapped[str | None] = mapped_column(String(255))
    hours_per_day: Mapped[float | None] = mapped_column(Float)
    hours_per_week: Mapped[float | None] = mapped_column(Float)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_period: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str | None] = mapped_column(String(20))
    application_url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[JobStatus] = mapped_column(
        enum_type(JobStatus), default=JobStatus.NEW, nullable=False
    )
    course_requirement: Mapped[str | None] = mapped_column(Text)
    has_uninterpreted_course_requirement: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    company: Mapped[Company] = relationship(back_populates="jobs")
    postings: Mapped[list[Posting]] = relationship(back_populates="job")
    decision: Mapped["Decision | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    applications: Mapped[list["Application"]] = relationship(back_populates="job")
    career_events: Mapped[list["CareerEvent"]] = relationship(back_populates="job")
    review_state: Mapped["JobReviewState | None"] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    review_events: Mapped[list["JobReviewEvent"]] = relationship(back_populates="job")
    resume_versions: Mapped[list["ResumeVersion"]] = relationship(back_populates="job")
    email_messages: Mapped[list["EmailMessage"]] = relationship(back_populates="job")
    application_matches: Mapped[list["ApplicationMatch"]] = relationship(back_populates="job")
    profile_comparisons: Mapped[list["JobProfileComparison"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_decisions_job_id"),
        Index("ix_decisions_eligibility_status", "eligibility_status"),
        Index("ix_decisions_ranking_score", "ranking_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    eligibility_status: Mapped[EligibilityStatus] = mapped_column(
        enum_type(EligibilityStatus), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    ranking_score: Mapped[int | None] = mapped_column(Integer)
    ranking_breakdown_json: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    rules_version: Mapped[str] = mapped_column(String(80), nullable=False)
    relevance_status: Mapped[RelevanceStatus | None] = mapped_column(enum_type(RelevanceStatus))
    relevance_score: Mapped[int | None] = mapped_column(Integer)
    relevance_reason_json: Mapped[str | None] = mapped_column(Text)
    relevance_rules_version: Mapped[str | None] = mapped_column(String(80))

    job: Mapped[Job] = relationship(back_populates="decision")


class DiscoveryHit(Base):
    __tablename__ = "discovery_hits"
    __table_args__ = (
        UniqueConstraint(
            "search_query_id",
            "source_run_id",
            "provider_identity_key",
            name="uq_discovery_hits_query_run_provider",
        ),
        Index("ix_discovery_hits_query_run", "search_query_id", "source_run_id"),
        Index("ix_discovery_hits_posting_id", "posting_id"),
        Index("ix_discovery_hits_job_id", "job_id"),
        Index("ix_discovery_hits_provider_identity_key", "provider_identity_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_query_id: Mapped[int] = mapped_column(
        ForeignKey("search_queries.id"), nullable=False, index=True
    )
    source_run_id: Mapped[int] = mapped_column(ForeignKey("source_runs.id"), nullable=False)
    posting_id: Mapped[int | None] = mapped_column(ForeignKey("postings.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    provider_identity_key: Mapped[str | None] = mapped_column(String(1200))
    position_in_results: Mapped[int | None] = mapped_column(Integer)
    page_number: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    match_status: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)

    search_query: Mapped[SearchQuery] = relationship(back_populates="hits")
    source_run: Mapped[SourceRun] = relationship(back_populates="discovery_hits")
    posting: Mapped[Posting | None] = relationship(back_populates="discovery_hits")
    job: Mapped[Job | None] = relationship()


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("application_key", name="uq_applications_application_key"),
        Index("ix_applications_status", "status"),
        Index("ix_applications_platform", "platform"),
        Index("ix_applications_applied_at", "applied_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    application_key: Mapped[str | None] = mapped_column(String(1200))
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus), default=ApplicationStatus.PREPARING, nullable=False
    )
    stage: Mapped[ApplicationStage | None] = mapped_column(enum_type(ApplicationStage))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform: Mapped[str | None] = mapped_column(String(120))
    external_reference: Mapped[str | None] = mapped_column(String(255))
    application_url: Mapped[str | None] = mapped_column(String(1000))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    job: Mapped[Job] = relationship(back_populates="applications")
    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )
    email_messages: Mapped[list["EmailMessage"]] = relationship(back_populates="application")
    matches: Mapped[list["ApplicationMatch"]] = relationship(back_populates="application")
    career_events: Mapped[list["CareerEvent"]] = relationship(back_populates="application")


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "event_key",
            name="uq_application_events_application_event_key",
        ),
        Index("ix_application_events_event_key", "event_key"),
        Index("ix_application_events_application_occurred", "application_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id"), nullable=False, index=True
    )
    event_key: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[ApplicationEventType] = mapped_column(
        enum_type(ApplicationEventType), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    application: Mapped[Application] = relationship(back_populates="events")


class JobReviewState(Base):
    __tablename__ = "job_review_states"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_review_states_job_id"),
        Index("ix_job_review_states_state", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    state: Mapped[ReviewState] = mapped_column(
        enum_type(ReviewState), default=ReviewState.UNREVIEWED, nullable=False
    )
    reason_code: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    job: Mapped[Job] = relationship(back_populates="review_state")


class JobReviewEvent(Base):
    __tablename__ = "job_review_events"
    __table_args__ = (
        Index("ix_job_review_events_job_id", "job_id"),
        Index("ix_job_review_events_event_type", "event_type"),
        Index("ix_job_review_events_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    event_type: Mapped[ReviewEventType] = mapped_column(enum_type(ReviewEventType), nullable=False)
    previous_job_status: Mapped[JobStatus | None] = mapped_column(enum_type(JobStatus))
    new_job_status: Mapped[JobStatus | None] = mapped_column(enum_type(JobStatus))
    previous_review_state: Mapped[ReviewState | None] = mapped_column(enum_type(ReviewState))
    new_review_state: Mapped[ReviewState | None] = mapped_column(enum_type(ReviewState))
    reason_code: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    job: Mapped[Job] = relationship(back_populates="review_events")


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        Index("ix_resumes_profile_id", "profile_id"),
        Index("ix_resumes_base_profile", "is_base", "profile_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("professional_profiles.id"))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    versions: Mapped[list["ResumeVersion"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )
    profile: Mapped["ProfessionalProfile | None"] = relationship(back_populates="resumes")


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id"), nullable=False, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    profile_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profile_versions.id"), index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(1000))
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    resume: Mapped[Resume] = relationship(back_populates="versions")
    job: Mapped[Job | None] = relationship(back_populates="resume_versions")
    profile_version: Mapped["ProfessionalProfileVersion | None"] = relationship(
        back_populates="resume_versions"
    )


class ProfessionalProfile(Base):
    __tablename__ = "professional_profiles"
    __table_args__ = (
        UniqueConstraint("normalized_name", name="uq_professional_profiles_normalized_name"),
        Index("ix_professional_profiles_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    versions: Mapped[list["ProfessionalProfileVersion"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    resumes: Mapped[list[Resume]] = relationship(back_populates="profile")
    activation_events: Mapped[list["ProfileActivationEvent"]] = relationship(
        back_populates="profile"
    )


class ProfessionalProfileVersion(Base):
    __tablename__ = "professional_profile_versions"
    __table_args__ = (
        UniqueConstraint("profile_id", "version_number", name="uq_profile_versions_number"),
        UniqueConstraint("profile_id", "content_hash", name="uq_profile_versions_content_hash"),
        Index("ix_profile_versions_active", "is_active"),
        Index(
            "uq_profile_versions_single_active",
            "is_active",
            unique=True,
            sqlite_where=text("is_active = 1"),
        ),
        Index("ix_profile_versions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profiles.id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(1000))
    source_format: Mapped[str | None] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    headline: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    raw_profile_json: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    profile: Mapped[ProfessionalProfile] = relationship(back_populates="versions")
    skills: Mapped[list["ProfileSkill"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    evidences: Mapped[list["ProfileEvidence"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    experiences: Mapped[list["ProfessionalExperience"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    projects: Mapped[list["ProfileProject"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    education: Mapped[list["EducationCredential"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    languages: Mapped[list["LanguageSkill"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    resume_versions: Mapped[list[ResumeVersion]] = relationship(back_populates="profile_version")
    comparisons: Mapped[list["JobProfileComparison"]] = relationship(
        back_populates="profile_version", cascade="all, delete-orphan"
    )
    activation_events: Mapped[list["ProfileActivationEvent"]] = relationship(
        back_populates="profile_version",
        foreign_keys="ProfileActivationEvent.profile_version_id",
    )


class ProfileActivationEvent(Base):
    __tablename__ = "profile_activation_events"
    __table_args__ = (
        Index("ix_profile_activation_events_profile_id", "profile_id"),
        Index("ix_profile_activation_events_profile_version_id", "profile_version_id"),
        Index("ix_profile_activation_events_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("professional_profiles.id"), nullable=False)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False
    )
    previous_profile_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("professional_profile_versions.id")
    )
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    profile: Mapped[ProfessionalProfile] = relationship(back_populates="activation_events")
    profile_version: Mapped[ProfessionalProfileVersion] = relationship(
        back_populates="activation_events",
        foreign_keys=[profile_version_id],
    )
    previous_profile_version: Mapped[ProfessionalProfileVersion | None] = relationship(
        foreign_keys=[previous_profile_version_id]
    )


class ProfileSkill(Base):
    __tablename__ = "profile_skills"
    __table_args__ = (
        UniqueConstraint(
            "profile_version_id",
            "normalized_name",
            name="uq_profile_skills_version_name",
        ),
        Index("ix_profile_skills_normalized_name", "normalized_name"),
        Index("ix_profile_skills_category", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    level: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="skills")
    evidences: Mapped[list["ProfileEvidence"]] = relationship(back_populates="skill")


class ProfileEvidence(Base):
    __tablename__ = "profile_evidences"
    __table_args__ = (
        Index("ix_profile_evidences_profile_version_id", "profile_version_id"),
        Index("ix_profile_evidences_skill_id", "skill_id"),
        Index("ix_profile_evidences_type", "evidence_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False
    )
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("profile_skills.id"))
    evidence_type: Mapped[ProfileEvidenceType] = mapped_column(
        enum_type(ProfileEvidenceType), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="evidences")
    skill: Mapped[ProfileSkill | None] = relationship(back_populates="evidences")


class ProfessionalExperience(Base):
    __tablename__ = "professional_experiences"
    __table_args__ = (
        Index("ix_professional_experiences_profile_version_id", "profile_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    organization: Mapped[str | None] = mapped_column(String(255))
    start_date: Mapped[str | None] = mapped_column(String(40))
    end_date: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    skills_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="experiences")


class ProfileProject(Base):
    __tablename__ = "profile_projects"
    __table_args__ = (Index("ix_profile_projects_profile_version_id", "profile_version_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    technologies_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(500))

    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="projects")


class EducationCredential(Base):
    __tablename__ = "education_credentials"
    __table_args__ = (Index("ix_education_credentials_profile_version_id", "profile_version_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False, index=True
    )
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    course: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str | None] = mapped_column(String(120))
    start_date: Mapped[str | None] = mapped_column(String(40))
    end_date: Mapped[str | None] = mapped_column(String(40))

    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="education")


class LanguageSkill(Base):
    __tablename__ = "language_skills"
    __table_args__ = (
        UniqueConstraint(
            "profile_version_id",
            "normalized_name",
            name="uq_language_skills_version_name",
        ),
        Index("ix_language_skills_profile_version_id", "profile_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="languages")


class JobProfileComparison(Base):
    __tablename__ = "job_profile_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "profile_version_id",
            "rules_version",
            "job_content_hash",
            name="uq_job_profile_comparisons_identity",
        ),
        Index("ix_job_profile_comparisons_score", "overall_score"),
        Index("ix_job_profile_comparisons_created_at", "created_at"),
        Index("ix_job_profile_comparisons_identity", "job_id", "profile_version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    profile_version_id: Mapped[int] = mapped_column(
        ForeignKey("professional_profile_versions.id"), nullable=False, index=True
    )
    overall_score: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    score_breakdown_json: Mapped[str] = mapped_column(Text, nullable=False)
    attention_points_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    rules_version: Mapped[str] = mapped_column(String(80), nullable=False)
    job_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    job: Mapped[Job] = relationship(back_populates="profile_comparisons")
    profile_version: Mapped[ProfessionalProfileVersion] = relationship(back_populates="comparisons")
    requirement_matches: Mapped[list["JobRequirementMatch"]] = relationship(
        back_populates="comparison", cascade="all, delete-orphan"
    )


class JobRequirementMatch(Base):
    __tablename__ = "job_requirement_matches"
    __table_args__ = (
        Index("ix_job_requirement_matches_comparison_id", "comparison_id"),
        Index("ix_job_requirement_matches_status", "match_status"),
        Index("ix_job_requirement_matches_kind", "requirement_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comparison_id: Mapped[int] = mapped_column(
        ForeignKey("job_profile_comparisons.id"), nullable=False
    )
    requirement_text: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_kind: Mapped[RequirementKind] = mapped_column(
        enum_type(RequirementKind), nullable=False
    )
    match_status: Mapped[RequirementMatchStatus] = mapped_column(
        enum_type(RequirementMatchStatus), nullable=False
    )
    evidence_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)

    comparison: Mapped[JobProfileComparison] = relationship(back_populates="requirement_matches")


class EmailMessage(Base):
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint("external_message_id", name="uq_email_messages_external_message_id"),
        Index("ix_email_messages_received_at", "received_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(255))
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classified_event_type: Mapped[str | None] = mapped_column(String(120))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), index=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float)

    company: Mapped[Company | None] = relationship(back_populates="email_messages")
    job: Mapped[Job | None] = relationship(back_populates="email_messages")
    application: Mapped[Application | None] = relationship(back_populates="email_messages")


class ApplicationMatch(Base):
    __tablename__ = "application_matches"
    __table_args__ = (
        Index("ix_application_matches_application_id", "application_id"),
        Index("ix_application_matches_job_id", "job_id"),
        Index("ix_application_matches_kind_status", "match_kind", "status"),
        UniqueConstraint("fingerprint", name="uq_application_matches_fingerprint"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    match_kind: Mapped[ApplicationMatchKind] = mapped_column(
        enum_type(ApplicationMatchKind), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ApplicationMatchStatus] = mapped_column(
        enum_type(ApplicationMatchStatus), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    application: Mapped[Application | None] = relationship(back_populates="matches")
    job: Mapped[Job | None] = relationship(back_populates="application_matches")


class CareerEvent(Base):
    __tablename__ = "career_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_career_events_event_key"),
        Index("ix_career_events_job_id", "job_id"),
        Index("ix_career_events_application_id", "application_id"),
        Index("ix_career_events_type", "event_type"),
        Index("ix_career_events_starts_at", "starts_at"),
        Index("ix_career_events_confirmation_status", "confirmation_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"))
    event_key: Mapped[str | None] = mapped_column(String(255))
    event_type: Mapped[CareerEventType] = mapped_column(enum_type(CareerEventType), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timezone: Mapped[str] = mapped_column(String(120), nullable=False)
    source: Mapped[CareerEventSource] = mapped_column(enum_type(CareerEventSource), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confirmation_status: Mapped[CareerEventConfirmationStatus] = mapped_column(
        enum_type(CareerEventConfirmationStatus), nullable=False
    )
    location: Mapped[str | None] = mapped_column(String(500))
    meeting_url: Mapped[str | None] = mapped_column(String(1000))
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    job: Mapped[Job | None] = relationship(back_populates="career_events")
    application: Mapped[Application | None] = relationship(back_populates="career_events")
    audits: Mapped[list["CareerEventAudit"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class CareerEventAudit(Base):
    __tablename__ = "career_event_audits"
    __table_args__ = (
        Index("ix_career_event_audits_event_id", "event_id"),
        Index("ix_career_event_audits_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("career_events.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    previous_values_json: Mapped[str | None] = mapped_column(Text)
    new_values_json: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    event: Mapped[CareerEvent] = relationship(back_populates="audits")


class FileImportBatch(Base):
    __tablename__ = "file_import_batches"
    __table_args__ = (Index("ix_file_import_batches_file_hash", "file_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_file: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_format: Mapped[str] = mapped_column(String(20), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    import_mode: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)

    item_audits: Mapped[list["ImportItemAudit"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportItemAudit(Base):
    __tablename__ = "import_item_audits"
    __table_args__ = (
        Index("ix_import_item_audits_batch_id", "batch_id"),
        Index("ix_import_item_audits_posting_id", "posting_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("file_import_batches.id"), nullable=False)
    posting_id: Mapped[int | None] = mapped_column(ForeignKey("postings.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    line_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(80), nullable=False)
    duplicate_kind: Mapped[str | None] = mapped_column(String(50))
    raw_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_payload_json: Mapped[str | None] = mapped_column(Text)
    errors_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    batch: Mapped[FileImportBatch] = relationship(back_populates="item_audits")
    posting: Mapped[Posting | None] = relationship(back_populates="import_audits")
