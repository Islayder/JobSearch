"""professional profile and compatibility tracking

Revision ID: 0008_professional_profile_and_tracking
Revises: 0007_review_and_application_history
Create Date: 2026-07-19 00:00:08.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_professional_profile_and_tracking"
down_revision: str | None = "0007_review_and_application_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _repair_previous_local_0007()
    _create_profile_tables()
    _add_resume_profile_reference()
    _create_comparison_tables()


def downgrade() -> None:
    op.drop_index("ix_job_requirement_matches_kind", table_name="job_requirement_matches")
    op.drop_index("ix_job_requirement_matches_status", table_name="job_requirement_matches")
    op.drop_index(
        "ix_job_requirement_matches_comparison_id",
        table_name="job_requirement_matches",
    )
    op.drop_table("job_requirement_matches")

    op.drop_index(
        "ix_job_profile_comparisons_created_at",
        table_name="job_profile_comparisons",
    )
    op.drop_index("ix_job_profile_comparisons_score", table_name="job_profile_comparisons")
    op.drop_index(
        "ix_job_profile_comparisons_profile_version_id",
        table_name="job_profile_comparisons",
    )
    op.drop_index("ix_job_profile_comparisons_job_id", table_name="job_profile_comparisons")
    op.drop_table("job_profile_comparisons")

    if "profile_version_id" in _columns("resume_versions"):
        with op.batch_alter_table("resume_versions") as batch_op:
            batch_op.drop_index("ix_resume_versions_profile_version_id")
            batch_op.drop_constraint("fk_resume_versions_profile_version_id", type_="foreignkey")
            batch_op.drop_column("profile_version_id")

    op.drop_index("ix_language_skills_profile_version_id", table_name="language_skills")
    op.drop_table("language_skills")

    op.drop_index(
        "ix_education_credentials_profile_version_id",
        table_name="education_credentials",
    )
    op.drop_table("education_credentials")

    op.drop_index("ix_profile_projects_profile_version_id", table_name="profile_projects")
    op.drop_table("profile_projects")

    op.drop_index(
        "ix_professional_experiences_profile_version_id",
        table_name="professional_experiences",
    )
    op.drop_table("professional_experiences")

    op.drop_index("ix_profile_evidences_type", table_name="profile_evidences")
    op.drop_index("ix_profile_evidences_skill_id", table_name="profile_evidences")
    op.drop_index(
        "ix_profile_evidences_profile_version_id",
        table_name="profile_evidences",
    )
    op.drop_table("profile_evidences")

    op.drop_index("ix_profile_skills_category", table_name="profile_skills")
    op.drop_index("ix_profile_skills_normalized_name", table_name="profile_skills")
    op.drop_index("ix_profile_skills_profile_version_id", table_name="profile_skills")
    op.drop_table("profile_skills")

    op.drop_index(
        "ix_profile_versions_created_at",
        table_name="professional_profile_versions",
    )
    op.drop_index("ix_profile_versions_active", table_name="professional_profile_versions")
    op.drop_index(
        "ix_professional_profile_versions_profile_id",
        table_name="professional_profile_versions",
    )
    op.drop_table("professional_profile_versions")

    op.drop_index("ix_professional_profiles_active", table_name="professional_profiles")
    op.drop_table("professional_profiles")


def _repair_previous_local_0007() -> None:
    if "stage" not in _columns("applications"):
        with op.batch_alter_table("applications") as batch_op:
            batch_op.add_column(sa.Column("stage", sa.String(length=50), nullable=True))

    if "message_fingerprint" in _columns("email_messages"):
        with op.batch_alter_table("email_messages") as batch_op:
            if _has_index("email_messages", "ix_email_messages_fingerprint"):
                batch_op.drop_index("ix_email_messages_fingerprint")
            batch_op.drop_column("message_fingerprint")

    if "classification_reason_json" in _columns("email_messages"):
        with op.batch_alter_table("email_messages") as batch_op:
            batch_op.drop_column("classification_reason_json")

    if "email_message_id" in _columns("application_matches"):
        with op.batch_alter_table("application_matches") as batch_op:
            if _has_index(
                "application_matches",
                "ix_application_matches_email_message_id",
            ):
                batch_op.drop_index("ix_application_matches_email_message_id")
            batch_op.drop_column("email_message_id")


def _create_profile_tables() -> None:
    op.create_table(
        "professional_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.UniqueConstraint(
            "normalized_name",
            name="uq_professional_profiles_normalized_name",
        ),
    )
    op.create_index("ix_professional_profiles_active", "professional_profiles", ["is_active"])

    op.create_table(
        "professional_profile_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("professional_profiles.id"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.String(length=1000), nullable=True),
        sa.Column("source_format", sa.String(length=40), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        sa.Column("headline", sa.String(length=500), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("raw_profile_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint("profile_id", "version_number", name="uq_profile_versions_number"),
        sa.UniqueConstraint("profile_id", "content_hash", name="uq_profile_versions_content_hash"),
    )
    op.create_index(
        "ix_professional_profile_versions_profile_id",
        "professional_profile_versions",
        ["profile_id"],
    )
    op.create_index("ix_profile_versions_active", "professional_profile_versions", ["is_active"])
    op.create_index(
        "ix_profile_versions_created_at",
        "professional_profile_versions",
        ["created_at"],
    )

    op.create_table(
        "profile_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("level", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "profile_version_id",
            "normalized_name",
            name="uq_profile_skills_version_name",
        ),
    )
    op.create_index(
        "ix_profile_skills_profile_version_id", "profile_skills", ["profile_version_id"]
    )
    op.create_index("ix_profile_skills_normalized_name", "profile_skills", ["normalized_name"])
    op.create_index("ix_profile_skills_category", "profile_skills", ["category"])

    op.create_table(
        "profile_evidences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("skill_id", sa.Integer(), sa.ForeignKey("profile_skills.id"), nullable=True),
        sa.Column("evidence_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_profile_evidences_profile_version_id",
        "profile_evidences",
        ["profile_version_id"],
    )
    op.create_index("ix_profile_evidences_skill_id", "profile_evidences", ["skill_id"])
    op.create_index("ix_profile_evidences_type", "profile_evidences", ["evidence_type"])

    op.create_table(
        "professional_experiences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("organization", sa.String(length=255), nullable=True),
        sa.Column("start_date", sa.String(length=40), nullable=True),
        sa.Column("end_date", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("skills_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.create_index(
        "ix_professional_experiences_profile_version_id",
        "professional_experiences",
        ["profile_version_id"],
    )

    op.create_table(
        "profile_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("technologies_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("source_ref", sa.String(length=500), nullable=True),
    )
    op.create_index(
        "ix_profile_projects_profile_version_id",
        "profile_projects",
        ["profile_version_id"],
    )

    op.create_table(
        "education_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("institution", sa.String(length=255), nullable=False),
        sa.Column("course", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=120), nullable=True),
        sa.Column("start_date", sa.String(length=40), nullable=True),
        sa.Column("end_date", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_education_credentials_profile_version_id",
        "education_credentials",
        ["profile_version_id"],
    )

    op.create_table(
        "language_skills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("normalized_name", sa.String(length=120), nullable=False),
        sa.Column("level", sa.String(length=120), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.UniqueConstraint(
            "profile_version_id",
            "normalized_name",
            name="uq_language_skills_version_name",
        ),
    )
    op.create_index(
        "ix_language_skills_profile_version_id", "language_skills", ["profile_version_id"]
    )


def _add_resume_profile_reference() -> None:
    if "profile_version_id" in _columns("resume_versions"):
        return
    with op.batch_alter_table("resume_versions") as batch_op:
        batch_op.add_column(sa.Column("profile_version_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_resume_versions_profile_version_id",
            "professional_profile_versions",
            ["profile_version_id"],
            ["id"],
        )
        batch_op.create_index("ix_resume_versions_profile_version_id", ["profile_version_id"])


def _create_comparison_tables() -> None:
    op.create_table(
        "job_profile_comparisons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column(
            "profile_version_id",
            sa.Integer(),
            sa.ForeignKey("professional_profile_versions.id"),
            nullable=False,
        ),
        sa.Column("overall_score", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("score_breakdown_json", sa.Text(), nullable=False),
        sa.Column("attention_points_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("rules_version", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "job_id",
            "profile_version_id",
            name="uq_job_profile_comparisons_job_profile_version",
        ),
    )
    op.create_index("ix_job_profile_comparisons_job_id", "job_profile_comparisons", ["job_id"])
    op.create_index(
        "ix_job_profile_comparisons_profile_version_id",
        "job_profile_comparisons",
        ["profile_version_id"],
    )
    op.create_index(
        "ix_job_profile_comparisons_score",
        "job_profile_comparisons",
        ["overall_score"],
    )
    op.create_index(
        "ix_job_profile_comparisons_created_at",
        "job_profile_comparisons",
        ["created_at"],
    )

    op.create_table(
        "job_requirement_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "comparison_id",
            sa.Integer(),
            sa.ForeignKey("job_profile_comparisons.id"),
            nullable=False,
        ),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("requirement_kind", sa.String(length=50), nullable=False),
        sa.Column("match_status", sa.String(length=50), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_job_requirement_matches_comparison_id",
        "job_requirement_matches",
        ["comparison_id"],
    )
    op.create_index(
        "ix_job_requirement_matches_status", "job_requirement_matches", ["match_status"]
    )
    op.create_index(
        "ix_job_requirement_matches_kind", "job_requirement_matches", ["requirement_kind"]
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
