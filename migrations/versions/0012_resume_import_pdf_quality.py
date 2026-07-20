"""resume import PDF quality metadata

Revision ID: 0012_resume_import_pdf_quality
Revises: 0011_resume_import_review
Create Date: 2026-07-20 00:00:12.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_resume_import_pdf_quality"
down_revision: str | None = "0011_resume_import_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "resume_import_sessions",
        sa.Column(
            "extraction_mode",
            sa.String(length=20),
            nullable=False,
            server_default="automatic",
        ),
    )
    op.add_column(
        "resume_import_sessions",
        sa.Column(
            "extraction_quality",
            sa.String(length=20),
            nullable=False,
            server_default="GOOD",
        ),
    )
    op.add_column(
        "resume_import_sessions",
        sa.Column(
            "extraction_metrics_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("resume_import_sessions", "extraction_metrics_json")
    op.drop_column("resume_import_sessions", "extraction_quality")
    op.drop_column("resume_import_sessions", "extraction_mode")
