"""requirement detail audit

Revision ID: 0010_requirement_detail_audit
Revises: 0009_tracking_integrity_and_calendar
Create Date: 2026-07-19 00:00:10.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_requirement_detail_audit"
down_revision: str | None = "0009_tracking_integrity_and_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job_requirement_matches") as batch_op:
        batch_op.add_column(sa.Column("requirement_source", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("original_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("terms_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("term_results_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_requirement_matches") as batch_op:
        batch_op.drop_column("term_results_json")
        batch_op.drop_column("terms_json")
        batch_op.drop_column("original_text")
        batch_op.drop_column("requirement_source")
