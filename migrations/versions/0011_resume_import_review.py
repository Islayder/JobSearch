"""resume import review

Revision ID: 0011_resume_import_review
Revises: 0010_requirement_detail_audit
Create Date: 2026-07-19 00:00:11.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_resume_import_review"
down_revision: str | None = "0010_requirement_detail_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, validate_strings=True)


def upgrade() -> None:
    op.create_table(
        "resume_import_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_key", sa.String(length=64), nullable=False),
        sa.Column("source_format", sa.String(length=20), nullable=False),
        sa.Column("sanitized_filename", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            _enum("EXTRACTING", "REVIEWING", "CONFIRMED", "DISCARDED", "FAILED"),
            nullable=False,
        ),
        sa.Column("profile_name", sa.String(length=255), nullable=True),
        sa.Column("headline", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("extracted_character_count", sa.Integer(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_profile_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["confirmed_profile_version_id"], ["professional_profile_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_key", name="uq_resume_import_sessions_import_key"),
    )
    op.create_index(
        "ix_resume_import_sessions_content_hash",
        "resume_import_sessions",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_resume_import_sessions_created_at",
        "resume_import_sessions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_resume_import_sessions_status",
        "resume_import_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_resume_import_sessions_confirmed_profile_version_id"),
        "resume_import_sessions",
        ["confirmed_profile_version_id"],
        unique=False,
    )

    op.create_table(
        "resume_import_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column(
            "candidate_type",
            _enum(
                "HEADLINE",
                "SUMMARY",
                "SKILL",
                "EXPERIENCE",
                "PROJECT",
                "EDUCATION",
                "LANGUAGE",
                "AMBIGUOUS",
            ),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("original_payload_json", sa.Text(), nullable=False),
        sa.Column("reviewed_payload_json", sa.Text(), nullable=True),
        sa.Column(
            "decision",
            _enum("PENDING", "ACCEPTED", "EDITED", "REMOVED"),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column(
            "confidence_label",
            _enum("HIGH", "MEDIUM", "LOW"),
            nullable=True,
        ),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.String(length=500), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["resume_import_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_resume_import_candidates_session_id"),
        "resume_import_candidates",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_resume_import_candidates_session_decision",
        "resume_import_candidates",
        ["session_id", "decision"],
        unique=False,
    )
    op.create_index(
        "ix_resume_import_candidates_session_type",
        "resume_import_candidates",
        ["session_id", "candidate_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_resume_import_candidates_session_type", table_name="resume_import_candidates")
    op.drop_index(
        "ix_resume_import_candidates_session_decision",
        table_name="resume_import_candidates",
    )
    op.drop_index(
        op.f("ix_resume_import_candidates_session_id"),
        table_name="resume_import_candidates",
    )
    op.drop_table("resume_import_candidates")
    op.drop_index(
        op.f("ix_resume_import_sessions_confirmed_profile_version_id"),
        table_name="resume_import_sessions",
    )
    op.drop_index("ix_resume_import_sessions_status", table_name="resume_import_sessions")
    op.drop_index("ix_resume_import_sessions_created_at", table_name="resume_import_sessions")
    op.drop_index("ix_resume_import_sessions_content_hash", table_name="resume_import_sessions")
    op.drop_table("resume_import_sessions")
