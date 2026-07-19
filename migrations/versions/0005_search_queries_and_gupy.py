"""search queries and gupy discovery

Revision ID: 0005_search_queries_and_gupy
Revises: 0004_collection_scope_keys
Create Date: 2026-07-19 00:00:04.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision: str = "0005_search_queries_and_gupy"
down_revision: str | None = "0004_collection_scope_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("postings") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("provider_scope", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("provider_external_id", sa.String(length=500), nullable=True))
        batch_op.add_column(
            sa.Column("provider_identity_key", sa.String(length=1200), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_postings_provider_identity_key", ["provider_identity_key"]
        )
        batch_op.create_index("ix_postings_provider_identity_key", ["provider_identity_key"])

    with op.batch_alter_table("decisions") as batch_op:
        batch_op.add_column(sa.Column("relevance_status", sa.String(length=13), nullable=True))
        batch_op.add_column(sa.Column("relevance_score", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("relevance_reason_json", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("relevance_rules_version", sa.String(length=80), nullable=True)
        )

    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("collector_type", sa.String(length=80), nullable=False),
        sa.Column("mode", sa.String(length=80), nullable=False),
        sa.Column("configuration_json", sa.Text(), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("collection_scope_key", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_id", sa.Integer(), sa.ForeignKey("source_runs.id"), nullable=True),
        sa.Column("last_complete_page_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_search_queries_key", "search_queries", ["key"], unique=True)
    op.create_index(
        "ix_search_queries_collector_mode",
        "search_queries",
        ["collector_type", "mode"],
    )
    op.create_index(
        "ix_search_queries_collection_scope_key",
        "search_queries",
        ["collection_scope_key"],
    )
    op.create_index(
        "ix_search_queries_active_priority",
        "search_queries",
        ["is_active", "priority"],
    )
    op.create_index("ix_search_queries_last_run_id", "search_queries", ["last_run_id"])

    op.create_table(
        "discovery_hits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "search_query_id",
            sa.Integer(),
            sa.ForeignKey("search_queries.id"),
            nullable=False,
        ),
        sa.Column(
            "source_run_id",
            sa.Integer(),
            sa.ForeignKey("source_runs.id"),
            nullable=False,
        ),
        sa.Column("posting_id", sa.Integer(), sa.ForeignKey("postings.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("provider_identity_key", sa.String(length=1200), nullable=True),
        sa.Column("position_in_results", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("match_status", sa.String(length=80), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "search_query_id",
            "source_run_id",
            "provider_identity_key",
            name="uq_discovery_hits_query_run_provider",
        ),
    )
    op.create_index("ix_discovery_hits_search_query_id", "discovery_hits", ["search_query_id"])
    op.create_index(
        "ix_discovery_hits_query_run", "discovery_hits", ["search_query_id", "source_run_id"]
    )
    op.create_index("ix_discovery_hits_posting_id", "discovery_hits", ["posting_id"])
    op.create_index("ix_discovery_hits_job_id", "discovery_hits", ["job_id"])
    op.create_index(
        "ix_discovery_hits_provider_identity_key",
        "discovery_hits",
        ["provider_identity_key"],
    )

    _backfill_provider_identity()


def downgrade() -> None:
    op.drop_index("ix_discovery_hits_provider_identity_key", table_name="discovery_hits")
    op.drop_index("ix_discovery_hits_job_id", table_name="discovery_hits")
    op.drop_index("ix_discovery_hits_posting_id", table_name="discovery_hits")
    op.drop_index("ix_discovery_hits_query_run", table_name="discovery_hits")
    op.drop_index("ix_discovery_hits_search_query_id", table_name="discovery_hits")
    op.drop_table("discovery_hits")

    op.drop_index("ix_search_queries_last_run_id", table_name="search_queries")
    op.drop_index("ix_search_queries_active_priority", table_name="search_queries")
    op.drop_index("ix_search_queries_collection_scope_key", table_name="search_queries")
    op.drop_index("ix_search_queries_collector_mode", table_name="search_queries")
    op.drop_index("ix_search_queries_key", table_name="search_queries")
    op.drop_table("search_queries")

    with op.batch_alter_table("decisions") as batch_op:
        batch_op.drop_column("relevance_rules_version")
        batch_op.drop_column("relevance_reason_json")
        batch_op.drop_column("relevance_score")
        batch_op.drop_column("relevance_status")

    with op.batch_alter_table("postings") as batch_op:
        batch_op.drop_index("ix_postings_provider_identity_key")
        batch_op.drop_constraint("uq_postings_provider_identity_key", type_="unique")
        batch_op.drop_column("provider_identity_key")
        batch_op.drop_column("provider_external_id")
        batch_op.drop_column("provider_scope")
        batch_op.drop_column("provider")


def _backfill_provider_identity() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT
                postings.id,
                postings.external_id,
                postings.normalized_url,
                sources.source_type,
                sources.base_url
            FROM postings
            JOIN sources ON sources.id = postings.source_id
            WHERE postings.provider_identity_key IS NULL
            """
        )
    ).mappings()
    used: set[str] = set()
    for row in rows:
        provider, scope, external_id, identity_key = _identity_from_row(row)
        if identity_key is None or identity_key in used:
            continue
        used.add(identity_key)
        bind.execute(
            sa.text(
                """
                UPDATE postings
                SET
                    provider = :provider,
                    provider_scope = :scope,
                    provider_external_id = :external_id,
                    provider_identity_key = :identity_key
                WHERE id = :posting_id
                """
            ),
            {
                "provider": provider,
                "scope": scope,
                "external_id": external_id,
                "identity_key": identity_key,
                "posting_id": row["id"],
            },
        )


def _identity_from_row(row: sa.RowMapping) -> tuple[str | None, str | None, str | None, str | None]:
    source_type = (row["source_type"] or "").strip().lower()
    external_id = str(row["external_id"] or "").strip()
    normalized_url = str(row["normalized_url"] or "").strip()
    base_url = str(row["base_url"] or "").strip()

    if source_type == "jobposting" and normalized_url:
        return "jobposting", None, normalized_url, f"jobposting:{normalized_url}"

    if source_type == "greenhouse" and external_id:
        token = _greenhouse_token(base_url)
        if token:
            return "greenhouse", token, external_id, f"greenhouse:{token}:{external_id}"

    if source_type == "lever" and external_id:
        token = _lever_token(base_url)
        if token:
            return "lever", token, external_id, f"lever:{token}:{external_id}"

    return None, None, None, None


def _greenhouse_token(url: str) -> str | None:
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    try:
        boards_index = segments.index("boards")
    except ValueError:
        return None
    if len(segments) <= boards_index + 1:
        return None
    return segments[boards_index + 1] or None


def _lever_token(url: str) -> str | None:
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    try:
        postings_index = segments.index("postings")
    except ValueError:
        return None
    if len(segments) <= postings_index + 1:
        return None
    return segments[postings_index + 1] or None
