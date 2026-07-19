"""relevance consistency and observations

Revision ID: 0006_relevance_consistency_and_observations
Revises: 0005_search_queries_and_gupy
Create Date: 2026-07-19 00:00:06.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_relevance_consistency_and_observations"
down_revision: str | None = "0005_search_queries_and_gupy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("postings") as batch_op:
        batch_op.add_column(sa.Column("raw_department", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("raw_area", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("raw_requirements", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("raw_responsibilities", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("raw_technologies_json", sa.Text(), nullable=False, server_default="[]")
        )

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("department", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("area", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("requirements", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("responsibilities", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("technologies_json", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_column("technologies_json")
        batch_op.drop_column("responsibilities")
        batch_op.drop_column("requirements")
        batch_op.drop_column("area")
        batch_op.drop_column("department")

    with op.batch_alter_table("postings") as batch_op:
        batch_op.drop_column("raw_technologies_json")
        batch_op.drop_column("raw_responsibilities")
        batch_op.drop_column("raw_requirements")
        batch_op.drop_column("raw_area")
        batch_op.drop_column("raw_department")
