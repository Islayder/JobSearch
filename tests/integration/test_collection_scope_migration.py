from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect, text

from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.persistence.migrations import alembic_config, run_migrations


def test_collection_scope_migration_upgrades_empty_database(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    run_migrations(settings)

    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        posting_columns = {column["name"] for column in inspector.get_columns("postings")}
        board_columns = {column["name"] for column in inspector.get_columns("company_boards")}
    finally:
        engine.dispose()
    assert "collection_scope_key" in posting_columns
    assert "collection_scope_key" in board_columns


def test_collection_scope_migration_downgrades_to_0003_and_upgrades_again(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    config = alembic_config(settings)

    command.downgrade(config, "0003_collection_infrastructure")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        posting_columns = {column["name"] for column in inspector.get_columns("postings")}
        board_columns = {column["name"] for column in inspector.get_columns("company_boards")}
    finally:
        engine.dispose()
    assert "collection_scope_key" not in posting_columns
    assert "collection_scope_key" not in board_columns

    command.upgrade(config, "head")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        posting_columns = {column["name"] for column in inspector.get_columns("postings")}
        board_columns = {column["name"] for column in inspector.get_columns("company_boards")}
    finally:
        engine.dispose()
    assert "collection_scope_key" in posting_columns
    assert "collection_scope_key" in board_columns


def test_relevance_consistency_migration_adds_structured_fields_and_downgrades(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    config = alembic_config(settings)

    command.upgrade(config, "0005_search_queries_and_gupy")
    command.upgrade(config, "head")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        posting_columns = {column["name"] for column in inspector.get_columns("postings")}
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    finally:
        engine.dispose()
    assert "raw_department" in posting_columns
    assert "raw_technologies_json" in posting_columns
    assert "department" in job_columns
    assert "technologies_json" in job_columns

    command.downgrade(config, "0005_search_queries_and_gupy")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        posting_columns = {column["name"] for column in inspector.get_columns("postings")}
        job_columns = {column["name"] for column in inspector.get_columns("jobs")}
    finally:
        engine.dispose()
    assert "raw_department" not in posting_columns
    assert "technologies_json" not in job_columns


def test_professional_profile_and_tracking_migration_adds_tables_and_downgrades(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    config = alembic_config(settings)

    command.upgrade(config, "0006_relevance_consistency_and_observations")
    command.upgrade(config, "head")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        application_columns = {column["name"] for column in inspector.get_columns("applications")}
        resume_version_columns = {
            column["name"] for column in inspector.get_columns("resume_versions")
        }
    finally:
        engine.dispose()
    assert "job_review_states" in tables
    assert "job_review_events" in tables
    assert "application_matches" in tables
    assert "professional_profiles" in tables
    assert "professional_profile_versions" in tables
    assert "profile_skills" in tables
    assert "profile_evidences" in tables
    assert "job_profile_comparisons" in tables
    assert "job_requirement_matches" in tables
    assert "application_key" in application_columns
    assert "stage" in application_columns
    assert "profile_version_id" in resume_version_columns

    command.downgrade(config, "0006_relevance_consistency_and_observations")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        application_columns = {column["name"] for column in inspector.get_columns("applications")}
        resume_version_columns = {
            column["name"] for column in inspector.get_columns("resume_versions")
        }
    finally:
        engine.dispose()
    assert "job_review_states" not in tables
    assert "job_review_events" not in tables
    assert "application_matches" not in tables
    assert "professional_profiles" not in tables
    assert "professional_profile_versions" not in tables
    assert "job_profile_comparisons" not in tables
    assert "job_requirement_matches" not in tables
    assert "application_key" not in application_columns
    assert "stage" not in application_columns
    assert "profile_version_id" not in resume_version_columns


def test_collection_scope_migration_backfills_existing_0003_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    config = alembic_config(settings)
    command.upgrade(config, "0003_collection_infrastructure")

    engine = create_engine(settings.database_url, future=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO sources
                        (id, name, slug, source_type, base_url, is_active, created_at, updated_at)
                    VALUES
                        (1, 'Greenhouse: Empresa', 'greenhouse-empresa', 'greenhouse',
                         'https://example.com', 1, '2026-07-01 10:00:00',
                         '2026-07-01 10:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO companies
                        (id, canonical_name, normalized_name, website, is_blocked,
                         blocked_reason, created_at, updated_at)
                    VALUES
                        (1, 'Empresa', 'empresa', NULL, 0, NULL,
                         '2026-07-01 10:00:00', '2026-07-01 10:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO source_runs
                        (id, source_id, started_at, finished_at, status, items_found,
                         items_created, items_skipped, error_message)
                    VALUES
                        (1, 1, '2026-07-01 10:00:00', '2026-07-01 10:00:01',
                         'SUCCESS', 1, 1, 0, NULL)
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO jobs
                        (id, company_id, canonical_title, normalized_title, description,
                         employment_type, seniority, work_model, country, state, city,
                         remote_country_scope, hours_per_day, hours_per_week, salary_min,
                         salary_max, salary_period, currency, application_url, published_at,
                         expires_at, status, course_requirement,
                         has_uninterpreted_course_requirement, created_at, updated_at)
                    VALUES
                        (1, 1, 'Estagio em Dados', 'estagio em dados', 'desc',
                         'INTERNSHIP', NULL, 'REMOTE', 'Brasil', NULL, NULL, 'Brasil',
                         NULL, NULL, NULL, NULL, NULL, NULL, 'https://example.com/job',
                         NULL, NULL, 'NEW', NULL, 0, '2026-07-01 10:00:00',
                         '2026-07-01 10:00:00')
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO postings
                        (id, source_id, source_run_id, external_id, original_url,
                         normalized_url, raw_title, raw_company, raw_location,
                         raw_description, published_at, first_seen_at, last_seen_at,
                         content_hash, status, job_id, is_active, missing_count,
                         closed_reason)
                    VALUES
                        (1, 1, 1, '1001', 'https://example.com/job',
                         'https://example.com/job', 'Estagio em Dados', 'Empresa',
                         'Remote - Brazil', 'desc', NULL, '2026-07-01 10:00:00',
                         '2026-07-01 10:00:00',
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         'NEW', 1, 1, 0, NULL)
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO company_boards
                        (id, company_id, source_id, external_identifier, board_url, is_active,
                         last_checked_at, key, collector_type, configuration_json,
                         last_success_at, last_failed_at, consecutive_failures, last_etag,
                         last_modified, last_complete_snapshot_at, last_run_id,
                         disabled_reason)
                    VALUES
                        (1, 1, 1, 'empresa', 'https://example.com', 1,
                         '2026-07-01 10:00:01', 'empresa-greenhouse', 'greenhouse',
                         '{}', '2026-07-01 10:00:01', NULL, 0, NULL, NULL,
                         '2026-07-01 10:00:01', 1, NULL)
                    """
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(settings.database_url, future=True)
    try:
        with engine.connect() as connection:
            posting_scope = connection.execute(
                text("SELECT collection_scope_key FROM postings WHERE id = 1")
            ).scalar_one()
            board_scope = connection.execute(
                text("SELECT collection_scope_key FROM company_boards WHERE id = 1")
            ).scalar_one()
    finally:
        engine.dispose()

    assert posting_scope == "legacy-source-1"
    assert board_scope == "legacy-source-1"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )
