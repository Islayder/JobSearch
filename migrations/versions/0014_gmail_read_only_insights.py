"""gmail read only insights

Revision ID: 0014_gmail_read_only_insights
Revises: 0013_company_intelligence_interviews
Create Date: 2026-07-20 00:00:14.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_gmail_read_only_insights"
down_revision: str | None = "0013_company_intelligence_interviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_messages") as batch_op:
        batch_op.add_column(
            sa.Column("provider", sa.String(length=80), nullable=False, server_default="gmail")
        )
        batch_op.add_column(sa.Column("body_excerpt", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("suggestion_json", sa.Text(), nullable=False, server_default="{}")
        )
        batch_op.add_column(sa.Column("source_query", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column(
                "fetched_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.create_index("ix_email_messages_provider", ["provider"])


def downgrade() -> None:
    with op.batch_alter_table("email_messages") as batch_op:
        batch_op.drop_index("ix_email_messages_provider")
        batch_op.drop_column("fetched_at")
        batch_op.drop_column("source_query")
        batch_op.drop_column("suggestion_json")
        batch_op.drop_column("body_excerpt")
        batch_op.drop_column("provider")
