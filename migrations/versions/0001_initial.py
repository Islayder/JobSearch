"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-18 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_sources_slug"),
    )
    op.create_index("ix_sources_slug", "sources", ["slug"])

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=1000), nullable=True),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_name", name="uq_companies_normalized_name"),
    )
    op.create_index("ix_companies_normalized_name", "companies", ["normalized_name"])

    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_base", sa.Boolean(), nullable=False),
        sa.Column("source_path", sa.String(length=1000), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "source_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("items_found", sa.Integer(), nullable=False),
        sa.Column("items_created", sa.Integer(), nullable=False),
        sa.Column("items_skipped", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_source_runs_source_id", "source_runs", ["source_id"])

    op.create_table(
        "company_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("normalized_alias", sa.String(length=255), nullable=False),
        sa.UniqueConstraint("normalized_alias", name="uq_company_aliases_normalized_alias"),
    )
    op.create_index("ix_company_aliases_company_id", "company_aliases", ["company_id"])

    op.create_table(
        "company_boards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("external_identifier", sa.String(length=255), nullable=True),
        sa.Column("board_url", sa.String(length=1000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_company_boards_company_id", "company_boards", ["company_id"])
    op.create_index("ix_company_boards_source_id", "company_boards", ["source_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("canonical_title", sa.String(length=500), nullable=False),
        sa.Column("normalized_title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("employment_type", sa.String(length=50), nullable=False),
        sa.Column("seniority", sa.String(length=120), nullable=True),
        sa.Column("work_model", sa.String(length=50), nullable=False),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("remote_country_scope", sa.String(length=255), nullable=True),
        sa.Column("hours_per_day", sa.Float(), nullable=True),
        sa.Column("hours_per_week", sa.Float(), nullable=True),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column("salary_period", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=20), nullable=True),
        sa.Column("application_url", sa.String(length=1000), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("course_requirement", sa.Text(), nullable=True),
        sa.Column("has_uninterpreted_course_requirement", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_company_title", "jobs", ["company_id", "normalized_title"])
    op.create_index("ix_jobs_employment_type", "jobs", ["employment_type"])
    op.create_index("ix_jobs_work_model", "jobs", ["work_model"])
    op.create_index("ix_jobs_city", "jobs", ["city"])
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "postings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("source_runs.id"), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("original_url", sa.String(length=1000), nullable=False),
        sa.Column("normalized_url", sa.String(length=1000), nullable=False),
        sa.Column("raw_title", sa.String(length=500), nullable=False),
        sa.Column("raw_company", sa.String(length=255), nullable=False),
        sa.Column("raw_location", sa.String(length=255), nullable=False),
        sa.Column("raw_description", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.UniqueConstraint("source_id", "external_id", name="uq_postings_source_external_id"),
        sa.UniqueConstraint(
            "source_id", "normalized_url", name="uq_postings_source_normalized_url"
        ),
        sa.UniqueConstraint("content_hash", name="uq_postings_content_hash"),
    )
    op.create_index("ix_postings_content_hash", "postings", ["content_hash"])
    op.create_index("ix_postings_job_id", "postings", ["job_id"])
    op.create_index("ix_postings_source_id", "postings", ["source_id"])
    op.create_index("ix_postings_source_run_id", "postings", ["source_run_id"])
    op.create_index("ix_postings_status", "postings", ["status"])

    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("eligibility_status", sa.String(length=50), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("ranking_score", sa.Integer(), nullable=True),
        sa.Column("ranking_breakdown_json", sa.Text(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rules_version", sa.String(length=80), nullable=False),
        sa.UniqueConstraint("job_id", name="uq_decisions_job_id"),
    )
    op.create_index("ix_decisions_eligibility_status", "decisions", ["eligibility_status"])
    op.create_index("ix_decisions_ranking_score", "decisions", ["ranking_score"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("platform", sa.String(length=120), nullable=True),
        sa.Column("external_reference", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_index("ix_applications_status", "applications", ["status"])

    op.create_table(
        "resume_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resume_id", sa.Integer(), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("file_path", sa.String(length=1000), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_resume_versions_resume_id", "resume_versions", ["resume_id"])
    op.create_index("ix_resume_versions_job_id", "resume_versions", ["job_id"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_application_events_application_id", "application_events", ["application_id"]
    )

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_message_id", sa.String(length=255), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("sender", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classified_event_type", sa.String(length=120), nullable=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id"), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.UniqueConstraint("external_message_id", name="uq_email_messages_external_message_id"),
    )
    op.create_index("ix_email_messages_application_id", "email_messages", ["application_id"])
    op.create_index("ix_email_messages_company_id", "email_messages", ["company_id"])
    op.create_index("ix_email_messages_job_id", "email_messages", ["job_id"])
    op.create_index("ix_email_messages_received_at", "email_messages", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_email_messages_received_at", table_name="email_messages")
    op.drop_index("ix_email_messages_job_id", table_name="email_messages")
    op.drop_index("ix_email_messages_company_id", table_name="email_messages")
    op.drop_index("ix_email_messages_application_id", table_name="email_messages")
    op.drop_table("email_messages")
    op.drop_index("ix_application_events_application_id", table_name="application_events")
    op.drop_table("application_events")
    op.drop_index("ix_resume_versions_job_id", table_name="resume_versions")
    op.drop_index("ix_resume_versions_resume_id", table_name="resume_versions")
    op.drop_table("resume_versions")
    op.drop_index("ix_applications_status", table_name="applications")
    op.drop_index("ix_applications_job_id", table_name="applications")
    op.drop_table("applications")
    op.drop_index("ix_decisions_ranking_score", table_name="decisions")
    op.drop_index("ix_decisions_eligibility_status", table_name="decisions")
    op.drop_table("decisions")
    op.drop_index("ix_postings_status", table_name="postings")
    op.drop_index("ix_postings_source_run_id", table_name="postings")
    op.drop_index("ix_postings_source_id", table_name="postings")
    op.drop_index("ix_postings_job_id", table_name="postings")
    op.drop_index("ix_postings_content_hash", table_name="postings")
    op.drop_table("postings")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_city", table_name="jobs")
    op.drop_index("ix_jobs_work_model", table_name="jobs")
    op.drop_index("ix_jobs_employment_type", table_name="jobs")
    op.drop_index("ix_jobs_company_title", table_name="jobs")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_company_boards_source_id", table_name="company_boards")
    op.drop_index("ix_company_boards_company_id", table_name="company_boards")
    op.drop_table("company_boards")
    op.drop_index("ix_company_aliases_company_id", table_name="company_aliases")
    op.drop_table("company_aliases")
    op.drop_index("ix_source_runs_source_id", table_name="source_runs")
    op.drop_table("source_runs")
    op.drop_table("resumes")
    op.drop_index("ix_companies_normalized_name", table_name="companies")
    op.drop_table("companies")
    op.drop_index("ix_sources_slug", table_name="sources")
    op.drop_table("sources")
