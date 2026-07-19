"""collection scope keys

Revision ID: 0004_collection_scope_keys
Revises: 0003_collection_infrastructure
Create Date: 2026-07-18 00:00:03.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_collection_scope_keys"
down_revision: str | None = "0003_collection_infrastructure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("company_boards") as batch_op:
        batch_op.add_column(sa.Column("collection_scope_key", sa.String(length=120), nullable=True))
        batch_op.create_index("ix_company_boards_collection_scope_key", ["collection_scope_key"])

    with op.batch_alter_table("postings") as batch_op:
        batch_op.add_column(sa.Column("collection_scope_key", sa.String(length=120), nullable=True))
        batch_op.create_index(
            "ix_postings_collection_scope_active",
            ["collection_scope_key", "is_active"],
        )

    op.execute(
        """
        UPDATE company_boards
        SET collection_scope_key = 'legacy-source-' || source_id
        WHERE collection_scope_key IS NULL
        """
    )
    op.execute(
        """
        UPDATE postings
        SET collection_scope_key = 'legacy-source-' || source_id
        WHERE collection_scope_key IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("postings") as batch_op:
        batch_op.drop_index("ix_postings_collection_scope_active")
        batch_op.drop_column("collection_scope_key")

    with op.batch_alter_table("company_boards") as batch_op:
        batch_op.drop_index("ix_company_boards_collection_scope_key")
        batch_op.drop_column("collection_scope_key")
