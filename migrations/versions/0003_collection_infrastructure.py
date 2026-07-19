"""collection infrastructure

Revision ID: 0003_collection_infrastructure
Revises: 0002_file_import_audit
Create Date: 2026-07-18 00:00:02.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_collection_infrastructure"
down_revision: str | None = "0002_file_import_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("company_boards") as batch_op:
        batch_op.add_column(sa.Column("key", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("collector_type", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("configuration_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("last_etag", sa.String(length=1000), nullable=True))
        batch_op.add_column(sa.Column("last_modified", sa.String(length=1000), nullable=True))
        batch_op.add_column(
            sa.Column("last_complete_snapshot_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("last_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("disabled_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_company_boards_last_run_id_source_runs",
            "source_runs",
            ["last_run_id"],
            ["id"],
        )
        batch_op.create_index("ix_company_boards_key", ["key"], unique=True)
        batch_op.create_index("ix_company_boards_collector_type", ["collector_type"])
        batch_op.create_index("ix_company_boards_last_run_id", ["last_run_id"])

    with op.batch_alter_table("postings") as batch_op:
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("closed_reason", sa.Text(), nullable=True))
        batch_op.create_index("ix_postings_active_missing", ["is_active", "missing_count"])

    op.create_table(
        "posting_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("posting_id", sa.Integer(), sa.ForeignKey("postings.id"), nullable=False),
        sa.Column("previous_content_hash", sa.String(length=64), nullable=False),
        sa.Column("new_content_hash", sa.String(length=64), nullable=False),
        sa.Column("changed_fields_json", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_run_id", sa.Integer(), sa.ForeignKey("source_runs.id"), nullable=True),
    )
    op.create_index(
        "ix_posting_revisions_posting_id",
        "posting_revisions",
        ["posting_id"],
    )
    op.create_index(
        "ix_posting_revisions_source_run_id",
        "posting_revisions",
        ["source_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_posting_revisions_source_run_id", table_name="posting_revisions")
    op.drop_index("ix_posting_revisions_posting_id", table_name="posting_revisions")
    op.drop_table("posting_revisions")

    with op.batch_alter_table("postings") as batch_op:
        batch_op.drop_index("ix_postings_active_missing")
        batch_op.drop_column("closed_reason")
        batch_op.drop_column("missing_count")
        batch_op.drop_column("is_active")

    with op.batch_alter_table("company_boards") as batch_op:
        batch_op.drop_index("ix_company_boards_last_run_id")
        batch_op.drop_index("ix_company_boards_collector_type")
        batch_op.drop_index("ix_company_boards_key")
        batch_op.drop_constraint(
            "fk_company_boards_last_run_id_source_runs",
            type_="foreignkey",
        )
        batch_op.drop_column("disabled_reason")
        batch_op.drop_column("last_run_id")
        batch_op.drop_column("last_complete_snapshot_at")
        batch_op.drop_column("last_modified")
        batch_op.drop_column("last_etag")
        batch_op.drop_column("consecutive_failures")
        batch_op.drop_column("last_failed_at")
        batch_op.drop_column("last_success_at")
        batch_op.drop_column("configuration_json")
        batch_op.drop_column("collector_type")
        batch_op.drop_column("key")
