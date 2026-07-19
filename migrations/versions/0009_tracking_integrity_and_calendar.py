"""tracking integrity and local calendar

Revision ID: 0009_tracking_integrity_and_calendar
Revises: 0008_professional_profile_and_tracking
Create Date: 2026-07-19 00:00:09.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_tracking_integrity_and_calendar"
down_revision: str | None = "0008_professional_profile_and_tracking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_application_event_identity()
    _add_application_match_identity()
    _add_comparison_identity()
    _add_profile_activation_audit()
    _add_resume_profile_reference()
    _create_calendar_tables()


def downgrade() -> None:
    op.drop_index("ix_career_event_audits_created_at", table_name="career_event_audits")
    op.drop_index("ix_career_event_audits_event_id", table_name="career_event_audits")
    op.drop_table("career_event_audits")

    op.drop_index("ix_career_events_confirmation_status", table_name="career_events")
    op.drop_index("ix_career_events_starts_at", table_name="career_events")
    op.drop_index("ix_career_events_type", table_name="career_events")
    op.drop_index("ix_career_events_application_id", table_name="career_events")
    op.drop_index("ix_career_events_job_id", table_name="career_events")
    op.drop_table("career_events")

    if _has_index("professional_profile_versions", "uq_profile_versions_single_active"):
        op.drop_index(
            "uq_profile_versions_single_active",
            table_name="professional_profile_versions",
        )

    op.drop_index(
        "ix_profile_activation_events_occurred_at",
        table_name="profile_activation_events",
    )
    op.drop_index(
        "ix_profile_activation_events_profile_version_id",
        table_name="profile_activation_events",
    )
    op.drop_index("ix_profile_activation_events_profile_id", table_name="profile_activation_events")
    op.drop_table("profile_activation_events")

    if "profile_id" in _columns("resumes"):
        with op.batch_alter_table("resumes") as batch_op:
            if _has_index("resumes", "ix_resumes_base_profile"):
                batch_op.drop_index("ix_resumes_base_profile")
            if _has_index("resumes", "ix_resumes_profile_id"):
                batch_op.drop_index("ix_resumes_profile_id")
            batch_op.drop_constraint("fk_resumes_profile_id", type_="foreignkey")
            batch_op.drop_column("profile_id")

    with op.batch_alter_table("job_profile_comparisons") as batch_op:
        batch_op.drop_constraint("uq_job_profile_comparisons_identity", type_="unique")
    if _has_index("job_profile_comparisons", "ix_job_profile_comparisons_identity"):
        op.drop_index(
            "ix_job_profile_comparisons_identity",
            table_name="job_profile_comparisons",
        )
    _collapse_duplicate_comparisons_for_0008()
    with op.batch_alter_table("job_profile_comparisons") as batch_op:
        batch_op.drop_column("job_content_hash")
        batch_op.create_unique_constraint(
            "uq_job_profile_comparisons_job_profile_version",
            ["job_id", "profile_version_id"],
        )

    with op.batch_alter_table("application_matches") as batch_op:
        batch_op.drop_constraint("uq_application_matches_fingerprint", type_="unique")
        batch_op.drop_column("fingerprint")

    with op.batch_alter_table("application_events") as batch_op:
        if _has_index("application_events", "ix_application_events_application_occurred"):
            batch_op.drop_index("ix_application_events_application_occurred")
        if _has_index("application_events", "ix_application_events_event_key"):
            batch_op.drop_index("ix_application_events_event_key")
        batch_op.drop_constraint(
            "uq_application_events_application_event_key",
            type_="unique",
        )
        batch_op.drop_column("event_key")


def _add_application_event_identity() -> None:
    with op.batch_alter_table("application_events") as batch_op:
        batch_op.add_column(sa.Column("event_key", sa.String(length=255), nullable=True))
        batch_op.create_unique_constraint(
            "uq_application_events_application_event_key",
            ["application_id", "event_key"],
        )
        batch_op.create_index("ix_application_events_event_key", ["event_key"])
        batch_op.create_index(
            "ix_application_events_application_occurred",
            ["application_id", "occurred_at"],
        )


def _add_application_match_identity() -> None:
    with op.batch_alter_table("application_matches") as batch_op:
        batch_op.add_column(sa.Column("fingerprint", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint("uq_application_matches_fingerprint", ["fingerprint"])


def _add_comparison_identity() -> None:
    with op.batch_alter_table("job_profile_comparisons") as batch_op:
        batch_op.add_column(
            sa.Column(
                "job_content_hash",
                sa.String(length=64),
                nullable=False,
                server_default="legacy",
            )
        )
        batch_op.drop_constraint(
            "uq_job_profile_comparisons_job_profile_version",
            type_="unique",
        )
        batch_op.create_unique_constraint(
            "uq_job_profile_comparisons_identity",
            ["job_id", "profile_version_id", "rules_version", "job_content_hash"],
        )
    op.create_index(
        "ix_job_profile_comparisons_identity",
        "job_profile_comparisons",
        ["job_id", "profile_version_id"],
    )


def _add_profile_activation_audit() -> None:
    _normalize_single_active_profile_version()
    op.create_table(
        "profile_activation_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "previous_profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_profile_activation_events_profile_id",
        "profile_activation_events",
        ["profile_id"],
    )
    op.create_index(
        "ix_profile_activation_events_profile_version_id",
        "profile_activation_events",
        ["profile_version_id"],
    )
    op.create_index(
        "ix_profile_activation_events_occurred_at",
        "profile_activation_events",
        ["occurred_at"],
    )
    op.create_index(
        "uq_profile_versions_single_active",
        "professional_profile_versions",
        ["is_active"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
    )


def _add_resume_profile_reference() -> None:
    if "profile_id" in _columns("resumes"):
        return
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.add_column(sa.Column("profile_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_resumes_profile_id",
            "professional_profiles",
            ["profile_id"],
            ["id"],
        )
        batch_op.create_index("ix_resumes_profile_id", ["profile_id"])
        batch_op.create_index("ix_resumes_base_profile", ["is_base", "profile_id"])
    op.execute(
        """
        UPDATE resumes
        SET profile_id = (
            SELECT professional_profile_versions.profile_id
            FROM resume_versions
            JOIN professional_profile_versions
              ON professional_profile_versions.id = resume_versions.profile_version_id
            WHERE resume_versions.resume_id = resumes.id
            ORDER BY resume_versions.id
            LIMIT 1
        )
        WHERE profile_id IS NULL
        """
    )


def _create_calendar_tables() -> None:
    op.create_table(
        "career_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id"),
            nullable=True,
        ),
        sa.Column("event_key", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timezone", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confirmation_status", sa.String(length=80), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("meeting_url", sa.String(length=1000), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("event_key", name="uq_career_events_event_key"),
    )
    op.create_index("ix_career_events_job_id", "career_events", ["job_id"])
    op.create_index("ix_career_events_application_id", "career_events", ["application_id"])
    op.create_index("ix_career_events_type", "career_events", ["event_type"])
    op.create_index("ix_career_events_starts_at", "career_events", ["starts_at"])
    op.create_index(
        "ix_career_events_confirmation_status",
        "career_events",
        ["confirmation_status"],
    )

    op.create_table(
        "career_event_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("career_events.id"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("previous_values_json", sa.Text(), nullable=True),
        sa.Column("new_values_json", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_career_event_audits_event_id", "career_event_audits", ["event_id"])
    op.create_index("ix_career_event_audits_created_at", "career_event_audits", ["created_at"])


def _normalize_single_active_profile_version() -> None:
    op.execute(
        """
        UPDATE professional_profile_versions
        SET is_active = 0
        WHERE is_active = 1
          AND id NOT IN (
            SELECT id
            FROM professional_profile_versions
            WHERE is_active = 1
            ORDER BY created_at DESC, id DESC
            LIMIT 1
          )
        """
    )
    op.execute("UPDATE professional_profiles SET is_active = 0")
    op.execute(
        """
        UPDATE professional_profiles
        SET is_active = 1
        WHERE id IN (
            SELECT profile_id
            FROM professional_profile_versions
            WHERE is_active = 1
        )
        """
    )


def _collapse_duplicate_comparisons_for_0008() -> None:
    op.execute(
        """
        DELETE FROM job_requirement_matches
        WHERE comparison_id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY job_id, profile_version_id
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM job_profile_comparisons
            )
            WHERE rn > 1
        )
        """
    )
    op.execute(
        """
        DELETE FROM job_profile_comparisons
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY job_id, profile_version_id
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM job_profile_comparisons
            )
            WHERE rn > 1
        )
        """
    )


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))
