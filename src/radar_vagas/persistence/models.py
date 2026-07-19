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
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from radar_vagas.domain.enums import (
    ApplicationStatus,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    PostingStatus,
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    key: Mapped[str | None] = mapped_column(String(120))
    collector_type: Mapped[str | None] = mapped_column(String(80))
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


class Posting(Base):
    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_postings_source_external_id"),
        UniqueConstraint("source_id", "normalized_url", name="uq_postings_source_normalized_url"),
        UniqueConstraint("content_hash", name="uq_postings_content_hash"),
        Index("ix_postings_status", "status"),
        Index("ix_postings_active_missing", "is_active", "missing_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("source_runs.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255))
    original_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    raw_title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_company: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_location: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_description: Mapped[str] = mapped_column(Text, nullable=False)
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
    resume_versions: Mapped[list["ResumeVersion"]] = relationship(back_populates="job")
    email_messages: Mapped[list["EmailMessage"]] = relationship(back_populates="job")


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

    job: Mapped[Job] = relationship(back_populates="decision")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (Index("ix_applications_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        enum_type(ApplicationStatus), default=ApplicationStatus.PREPARING, nullable=False
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform: Mapped[str | None] = mapped_column(String(120))
    external_reference: Mapped[str | None] = mapped_column(String(255))
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


class ApplicationEvent(Base):
    __tablename__ = "application_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    source: Mapped[str] = mapped_column(String(120), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="events")


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_base: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_path: Mapped[str | None] = mapped_column(String(1000))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    versions: Mapped[list["ResumeVersion"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resume_id: Mapped[int] = mapped_column(ForeignKey("resumes.id"), nullable=False, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    file_path: Mapped[str | None] = mapped_column(String(1000))
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    resume: Mapped[Resume] = relationship(back_populates="versions")
    job: Mapped[Job | None] = relationship(back_populates="resume_versions")


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
