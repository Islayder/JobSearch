"""review and application history

Revision ID: 0007_review_and_application_history
Revises: 0006_relevance_consistency_and_observations
Create Date: 2026-07-19 00:00:07.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_review_and_application_history"
down_revision: str | None = "0006_relevance_consistency_and_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(sa.Column("application_key", sa.String(length=1200), nullable=True))
        batch_op.add_column(sa.Column("stage", sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column("application_url", sa.String(length=1000), nullable=True))
        batch_op.create_unique_constraint(
            "uq_applications_application_key",
            ["application_key"],
        )
        batch_op.create_index("ix_applications_platform", ["platform"])
        batch_op.create_index("ix_applications_applied_at", ["applied_at"])

    with op.batch_alter_table("application_events") as batch_op:
        batch_op.add_column(
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )

    op.create_table(
        "job_review_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("job_id", name="uq_job_review_states_job_id"),
    )
    op.create_index("ix_job_review_states_job_id", "job_review_states", ["job_id"])
    op.create_index("ix_job_review_states_state", "job_review_states", ["state"])

    op.create_table(
        "job_review_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("previous_job_status", sa.String(length=50), nullable=True),
        sa.Column("new_job_status", sa.String(length=50), nullable=True),
        sa.Column("previous_review_state", sa.String(length=50), nullable=True),
        sa.Column("new_review_state", sa.String(length=50), nullable=True),
        sa.Column("reason_code", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
    op.create_index("ix_job_review_events_job_id", "job_review_events", ["job_id"])
    op.create_index("ix_job_review_events_event_type", "job_review_events", ["event_type"])
    op.create_index("ix_job_review_events_occurred_at", "job_review_events", ["occurred_at"])

    op.create_table(
        "application_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("match_kind", sa.String(length=50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_application_matches_application_id",
        "application_matches",
        ["application_id"],
    )
    op.create_index("ix_application_matches_job_id", "application_matches", ["job_id"])
    op.create_index(
        "ix_application_matches_kind_status",
        "application_matches",
        ["match_kind", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_application_matches_kind_status", table_name="application_matches")
    op.drop_index("ix_application_matches_job_id", table_name="application_matches")
    op.drop_index("ix_application_matches_application_id", table_name="application_matches")
    op.drop_table("application_matches")

    op.drop_index("ix_job_review_events_occurred_at", table_name="job_review_events")
    op.drop_index("ix_job_review_events_event_type", table_name="job_review_events")
    op.drop_index("ix_job_review_events_job_id", table_name="job_review_events")
    op.drop_table("job_review_events")

    op.drop_index("ix_job_review_states_state", table_name="job_review_states")
    op.drop_index("ix_job_review_states_job_id", table_name="job_review_states")
    op.drop_table("job_review_states")

    with op.batch_alter_table("application_events") as batch_op:
        batch_op.drop_column("created_at")

    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_index("ix_applications_applied_at")
        batch_op.drop_index("ix_applications_platform")
        batch_op.drop_constraint("uq_applications_application_key", type_="unique")
        batch_op.drop_column("application_url")
        batch_op.drop_column("stage")
        batch_op.drop_column("application_key")
