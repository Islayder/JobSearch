"""company intelligence and interview preparation

Revision ID: 0013_company_intelligence_interviews
Revises: 0012_resume_import_pdf_quality
Create Date: 2026-07-20 00:00:13.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_company_intelligence_interviews"
down_revision: str | None = "0012_resume_import_pdf_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, validate_strings=True)


def upgrade() -> None:
    op.create_table(
        "company_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("official_website", sa.String(length=1000), nullable=True),
        sa.Column("industry", sa.String(length=255), nullable=True),
        sa.Column("company_size", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sources_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_company_profiles_company_id"),
    )
    op.create_index(
        op.f("ix_company_profiles_company_id"),
        "company_profiles",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_company_profiles_retrieved_at",
        "company_profiles",
        ["retrieved_at"],
        unique=False,
    )

    op.create_table(
        "company_facts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "origin_type",
            _enum("OFFICIAL_INFO", "EMPLOYEE_REPORT", "RADAR_INFERENCE", "USER_NOTE"),
            nullable=False,
        ),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("source_date", sa.String(length=80), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_company_facts_category", "company_facts", ["category"], unique=False)
    op.create_index(
        "ix_company_facts_company_origin",
        "company_facts",
        ["company_id", "origin_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_facts_company_id"),
        "company_facts",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "company_review_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=120), nullable=False),
        sa.Column("overall_rating", sa.Float(), nullable=True),
        sa.Column("review_count", sa.Integer(), nullable=True),
        sa.Column("positives_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("negatives_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("period", sa.String(length=120), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=True),
        sa.Column(
            "employee_reports_notice",
            sa.String(length=120),
            nullable=False,
            server_default="relatos de funcionarios",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_company_review_snapshots_company_platform",
        "company_review_snapshots",
        ["company_id", "platform"],
        unique=False,
    )
    op.create_index(
        "ix_company_review_snapshots_created_at",
        "company_review_snapshots",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_company_review_snapshots_company_id"),
        "company_review_snapshots",
        ["company_id"],
        unique=False,
    )

    op.create_table(
        "interview_preparations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("profile_version_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("likely_questions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("relevant_experiences_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("gaps_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("interviewer_questions_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("checklist_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("sources_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["profile_version_id"], ["professional_profile_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interview_preparations_job_generated",
        "interview_preparations",
        ["job_id", "generated_at"],
        unique=False,
    )
    op.create_index(
        "ix_interview_preparations_profile_version_id",
        "interview_preparations",
        ["profile_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_interview_preparations_company_id",
        "interview_preparations",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_interview_preparations_job_id"),
        "interview_preparations",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_interview_preparations_job_id"), table_name="interview_preparations")
    op.drop_index("ix_interview_preparations_company_id", table_name="interview_preparations")
    op.drop_index(
        "ix_interview_preparations_profile_version_id",
        table_name="interview_preparations",
    )
    op.drop_index("ix_interview_preparations_job_generated", table_name="interview_preparations")
    op.drop_table("interview_preparations")
    op.drop_index(
        op.f("ix_company_review_snapshots_company_id"),
        table_name="company_review_snapshots",
    )
    op.drop_index(
        "ix_company_review_snapshots_created_at",
        table_name="company_review_snapshots",
    )
    op.drop_index(
        "ix_company_review_snapshots_company_platform",
        table_name="company_review_snapshots",
    )
    op.drop_table("company_review_snapshots")
    op.drop_index(op.f("ix_company_facts_company_id"), table_name="company_facts")
    op.drop_index("ix_company_facts_company_origin", table_name="company_facts")
    op.drop_index("ix_company_facts_category", table_name="company_facts")
    op.drop_table("company_facts")
    op.drop_index("ix_company_profiles_retrieved_at", table_name="company_profiles")
    op.drop_index(op.f("ix_company_profiles_company_id"), table_name="company_profiles")
    op.drop_table("company_profiles")
