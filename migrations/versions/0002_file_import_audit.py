"""file import audit

Revision ID: 0002_file_import_audit
Revises: 0001_initial
Create Date: 2026-07-18 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_file_import_audit"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "file_import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("input_file", sa.String(length=1000), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_format", sa.String(length=20), nullable=False),
        sa.Column("schema_version", sa.String(length=50), nullable=False),
        sa.Column("import_mode", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_file_import_batches_file_hash",
        "file_import_batches",
        ["file_hash"],
    )

    op.create_table(
        "import_item_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("file_import_batches.id"),
            nullable=False,
        ),
        sa.Column("posting_id", sa.Integer(), sa.ForeignKey("postings.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("duplicate_kind", sa.String(length=50), nullable=True),
        sa.Column("raw_payload_json", sa.Text(), nullable=False),
        sa.Column("normalized_payload_json", sa.Text(), nullable=True),
        sa.Column("errors_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_import_item_audits_batch_id", "import_item_audits", ["batch_id"])
    op.create_index(
        "ix_import_item_audits_posting_id",
        "import_item_audits",
        ["posting_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_item_audits_posting_id", table_name="import_item_audits")
    op.drop_index("ix_import_item_audits_batch_id", table_name="import_item_audits")
    op.drop_table("import_item_audits")
    op.drop_index("ix_file_import_batches_file_hash", table_name="file_import_batches")
    op.drop_table("file_import_batches")
