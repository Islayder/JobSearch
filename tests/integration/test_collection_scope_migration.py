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


def test_tracking_integrity_and_calendar_migration_round_trips_from_0008(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    config = alembic_config(settings)

    command.upgrade(config, "0008_professional_profile_and_tracking")
    command.upgrade(config, "head")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        application_event_columns = {
            column["name"] for column in inspector.get_columns("application_events")
        }
        application_match_columns = {
            column["name"] for column in inspector.get_columns("application_matches")
        }
        comparison_columns = {
            column["name"] for column in inspector.get_columns("job_profile_comparisons")
        }
        resume_columns = {column["name"] for column in inspector.get_columns("resumes")}
    finally:
        engine.dispose()

    assert "career_events" in tables
    assert "career_event_audits" in tables
    assert "profile_activation_events" in tables
    assert "event_key" in application_event_columns
    assert "fingerprint" in application_match_columns
    assert "job_content_hash" in comparison_columns
    assert "profile_id" in resume_columns

    command.downgrade(config, "0008_professional_profile_and_tracking")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        application_event_columns = {
            column["name"] for column in inspector.get_columns("application_events")
        }
        application_match_columns = {
            column["name"] for column in inspector.get_columns("application_matches")
        }
        comparison_columns = {
            column["name"] for column in inspector.get_columns("job_profile_comparisons")
        }
        resume_columns = {column["name"] for column in inspector.get_columns("resumes")}
    finally:
        engine.dispose()

    assert "career_events" not in tables
    assert "career_event_audits" not in tables
    assert "profile_activation_events" not in tables
    assert "event_key" not in application_event_columns
    assert "fingerprint" not in application_match_columns
    assert "job_content_hash" not in comparison_columns
    assert "profile_id" not in resume_columns

    command.upgrade(config, "head")
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()
    assert "career_events" in tables


def test_requirement_detail_audit_migration_preserves_existing_counts(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    config = alembic_config(settings)

    command.upgrade(config, "0009_tracking_integrity_and_calendar")
    _seed_requirement_audit_migration_data(settings)

    before = _table_counts(settings, _REQUIREMENT_AUDIT_COUNT_TABLES)
    assert before["jobs"] == 50
    assert before["postings"] == 50
    assert before["decisions"] == 50
    assert before["discovery_hits"] == 61
    assert before["job_profile_comparisons"] == 50
    assert before["job_requirement_matches"] == 50
    assert "requirement_source" not in _columns_for(settings, "job_requirement_matches")

    command.upgrade(config, "0010_requirement_detail_audit")
    after_upgrade = _table_counts(settings, _REQUIREMENT_AUDIT_COUNT_TABLES)
    assert after_upgrade == before
    columns_after_upgrade = _columns_for(settings, "job_requirement_matches")
    assert {
        "requirement_source",
        "original_text",
        "terms_json",
        "term_results_json",
    } <= columns_after_upgrade

    command.downgrade(config, "0009_tracking_integrity_and_calendar")
    after_downgrade = _table_counts(settings, _REQUIREMENT_AUDIT_COUNT_TABLES)
    assert after_downgrade == before
    assert "term_results_json" not in _columns_for(settings, "job_requirement_matches")

    command.upgrade(config, "0010_requirement_detail_audit")
    after_second_upgrade = _table_counts(settings, _REQUIREMENT_AUDIT_COUNT_TABLES)
    assert after_second_upgrade == before
    assert "term_results_json" in _columns_for(settings, "job_requirement_matches")


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


_REQUIREMENT_AUDIT_COUNT_TABLES = [
    "companies",
    "sources",
    "source_runs",
    "search_queries",
    "jobs",
    "postings",
    "decisions",
    "discovery_hits",
    "job_review_states",
    "job_review_events",
    "applications",
    "application_events",
    "professional_profiles",
    "professional_profile_versions",
    "resumes",
    "resume_versions",
    "profile_skills",
    "job_profile_comparisons",
    "job_requirement_matches",
    "career_events",
    "career_event_audits",
]


def _seed_requirement_audit_migration_data(settings: Settings) -> None:
    engine = create_engine(settings.database_url, future=True)
    try:
        with engine.begin() as connection:
            now = "2026-07-19 12:00:00"
            connection.execute(
                text(
                    """
                    INSERT INTO companies
                        (id, canonical_name, normalized_name, website, is_blocked,
                         blocked_reason, created_at, updated_at)
                    VALUES
                        (1, 'Empresa Sintetica', 'empresa sintetica', NULL, 0, NULL,
                         :now, :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO sources
                        (id, name, slug, source_type, base_url, is_active,
                         created_at, updated_at)
                    VALUES
                        (1, 'Fonte Sintetica', 'fonte-sintetica', 'fixture',
                         'https://example.com', 1, :now, :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO source_runs
                        (id, source_id, started_at, finished_at, status, items_found,
                         items_created, items_skipped, error_message)
                    VALUES
                        (1, 1, :now, :now, 'SUCCESS', 50, 50, 0, NULL)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO search_queries
                        (id, key, collector_type, mode, configuration_json,
                         configuration_fingerprint, collection_scope_key, is_active,
                         priority, tags_json, last_checked_at, last_success_at,
                         last_failed_at, consecutive_failures, last_run_id,
                         last_complete_page_at, disabled_reason)
                    VALUES
                        (1, 'query-sintetica', 'gupy', 'public_portal', '{}',
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         'query:gupy:query-sintetica', 1, 10, '[]', :now, :now,
                         NULL, 0, 1, :now, NULL)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO professional_profiles
                        (id, name, normalized_name, is_active, created_at, updated_at)
                    VALUES
                        (1, 'Perfil Sintetico', 'perfil sintetico', 1, :now, :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO professional_profile_versions
                        (id, profile_id, version_number, content_hash, profile_hash,
                         source_path, raw_profile_json, headline, summary, is_active,
                         created_at)
                    VALUES
                        (1, 1, 1,
                         'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                         'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                         'synthetic.json', '{}', 'Dados', 'Perfil sintetico', 1, :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resumes
                        (id, profile_id, name, source_path, content_hash,
                         is_base, created_at)
                    VALUES
                        (1, 1, 'Curriculo Sintetico', 'synthetic.json',
                         'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
                         1, :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO resume_versions
                        (id, resume_id, profile_version_id, job_id, file_path,
                         change_summary, created_at)
                    VALUES
                        (1, 1, 1, NULL, 'synthetic.json',
                         'Versao sintetica', :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO profile_skills
                        (id, profile_version_id, name, normalized_name, category,
                         level, created_at)
                    VALUES
                        (1, 1, 'SQL', 'sql', 'data', 'intermediario', :now)
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO jobs
                        (id, company_id, canonical_title, normalized_title,
                         description, department, area, requirements,
                         responsibilities, technologies_json, employment_type,
                         seniority, work_model, country, state, city,
                         remote_country_scope, hours_per_day, hours_per_week,
                         salary_min, salary_max, salary_period, currency,
                         application_url, published_at, expires_at, status,
                         course_requirement, has_uninterpreted_course_requirement,
                         created_at, updated_at)
                    VALUES
                        (:id, 1, :title, :normalized_title, 'Descricao sintetica',
                         'Dados', 'Dados', 'SQL', 'Analise', '["SQL"]',
                         'INTERNSHIP', NULL, 'REMOTE', 'Brasil', NULL, NULL,
                         'Brasil', NULL, NULL, NULL, NULL, NULL, NULL, :url,
                         NULL, NULL, 'ELIGIBLE', NULL, 0, :now, :now)
                    """
                ),
                [
                    {
                        "id": index,
                        "title": f"Estagio em Dados {index}",
                        "normalized_title": f"estagio em dados {index}",
                        "url": f"https://example.com/jobs/{index}",
                        "now": now,
                    }
                    for index in range(1, 51)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO postings
                        (id, source_id, source_run_id, collection_scope_key, provider,
                         provider_scope, provider_external_id, provider_identity_key,
                         external_id, original_url, normalized_url, raw_title,
                         raw_company, raw_location, raw_description, raw_department,
                         raw_area, raw_requirements, raw_responsibilities,
                         raw_technologies_json, published_at, first_seen_at,
                         last_seen_at, content_hash, status, is_active, missing_count,
                         closed_reason, job_id)
                    VALUES
                        (:id, 1, 1, 'fixture:synthetic', 'fixture', 'synthetic',
                         :external_id, :provider_identity_key, :external_id, :url,
                         :url, :title, 'Empresa Sintetica', 'Remoto Brasil',
                         'Descricao sintetica', 'Dados', 'Dados', 'SQL', 'Analise',
                         '["SQL"]', NULL, :now, :now, :content_hash, 'LINKED',
                         1, 0, NULL, :id)
                    """
                ),
                [
                    {
                        "id": index,
                        "external_id": f"job-{index}",
                        "provider_identity_key": f"fixture:synthetic:{index}",
                        "url": f"https://example.com/jobs/{index}",
                        "title": f"Estagio em Dados {index}",
                        "content_hash": f"{index:064x}",
                        "now": now,
                    }
                    for index in range(1, 51)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO decisions
                        (id, job_id, eligibility_status, reason_code, reason_text,
                         ranking_score, ranking_breakdown_json, evaluated_at,
                         rules_version, relevance_status, relevance_score,
                         relevance_reason_json, relevance_rules_version)
                    VALUES
                        (:id, :id, 'ELIGIBLE', 'synthetic', 'Elegivel',
                         80, '{}', :now, 'test', 'CORE', 90, '{}', 'test')
                    """
                ),
                [{"id": index, "now": now} for index in range(1, 51)],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO discovery_hits
                        (id, search_query_id, source_run_id, posting_id, job_id,
                         provider_identity_key, page_number, position_in_results,
                         match_status, metadata_json, observed_at)
                    VALUES
                        (:id, 1, 1, :posting_id, :posting_id,
                         :provider_identity_key, 1, :id, 'known', '{}', :now)
                    """
                ),
                [
                    {
                        "id": index,
                        "posting_id": ((index - 1) % 50) + 1,
                        "provider_identity_key": f"fixture:hit:{index}",
                        "now": now,
                    }
                    for index in range(1, 62)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO job_review_states
                        (id, job_id, state, reason_code, notes, created_at,
                         updated_at)
                    VALUES
                        (:id, :id, 'UNREVIEWED', NULL, NULL, :now, :now)
                    """
                ),
                [{"id": index, "now": now} for index in range(1, 51)],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO job_review_events
                        (id, job_id, event_type, previous_job_status,
                         new_job_status, previous_review_state, new_review_state,
                         reason_code, notes, source, occurred_at, created_at)
                    VALUES
                        (:id, :id, 'SEEN', 'ELIGIBLE', 'SEEN', 'UNREVIEWED',
                         'SEEN', NULL, NULL, 'synthetic', :now, :now)
                    """
                ),
                [{"id": index, "now": now} for index in range(1, 51)],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO applications
                        (id, job_id, application_key, status, applied_at, platform,
                         external_reference, application_url, stage, notes,
                         created_at, updated_at)
                    VALUES
                        (:id, :id, :application_key, 'SUBMITTED', :now, 'manual',
                         :external_reference, :url, 'APPLIED', NULL, :now, :now)
                    """
                ),
                [
                    {
                        "id": index,
                        "application_key": f"synthetic-app-{index}",
                        "external_reference": f"APP-{index}",
                        "url": f"https://example.com/applications/{index}",
                        "now": now,
                    }
                    for index in range(1, 51)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO application_events
                        (id, application_id, event_key, event_type, occurred_at,
                         notes, source, created_at)
                    VALUES
                        (:id, :id, :event_key, 'SUBMITTED', :now, NULL,
                         'synthetic', :now)
                    """
                ),
                [
                    {"id": index, "event_key": f"synthetic-app-event-{index}", "now": now}
                    for index in range(1, 51)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO job_profile_comparisons
                        (id, job_id, profile_version_id, overall_score, summary,
                         score_breakdown_json, attention_points_json, rules_version,
                         job_content_hash, created_at)
                    VALUES
                        (:id, :id, 1, 75, 'Compatibilidade sintetica',
                         '{}', '[]', 'test',
                         :job_content_hash, :now)
                    """
                ),
                [
                    {
                        "id": index,
                        "job_content_hash": f"{(index + 1000):064x}",
                        "now": now,
                    }
                    for index in range(1, 51)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO job_requirement_matches
                        (id, comparison_id, requirement_text, requirement_kind,
                         match_status, evidence_json, explanation, weight)
                    VALUES
                        (:id, :id, 'SQL', 'MANDATORY', 'MATCHED', '[]',
                         'Evidencia sintetica', 10)
                    """
                ),
                [{"id": index} for index in range(1, 51)],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO career_events
                        (id, job_id, application_id, event_key, event_type, title,
                         starts_at, ends_at, all_day, timezone, source, confidence,
                         confirmation_status, location, meeting_url, notes,
                         completed_at, cancelled_at, created_at, updated_at)
                    VALUES
                        (:id, :id, :id, :event_key, 'INTERVIEW', :title, :now,
                         NULL, 0, 'America/Sao_Paulo', 'MANUAL', NULL, 'CONFIRMED',
                         NULL, NULL, NULL, NULL, NULL, :now, :now)
                    """
                ),
                [
                    {
                        "id": index,
                        "event_key": f"synthetic-career-event-{index}",
                        "title": f"Entrevista {index}",
                        "now": now,
                    }
                    for index in range(1, 51)
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO career_event_audits
                        (id, event_id, action, previous_values_json, new_values_json,
                         source, occurred_at, created_at)
                    VALUES
                        (:id, :id, 'created', NULL, '{}', 'synthetic', :now, :now)
                    """
                ),
                [{"id": index, "now": now} for index in range(1, 51)],
            )
    finally:
        engine.dispose()


def _table_counts(settings: Settings, table_names: list[str]) -> dict[str, int]:
    engine = create_engine(settings.database_url, future=True)
    try:
        with engine.connect() as connection:
            return {
                table_name: connection.execute(
                    text(f"SELECT COUNT(*) FROM {table_name}")
                ).scalar_one()
                for table_name in table_names
            }
    finally:
        engine.dispose()


def _columns_for(settings: Settings, table_name: str) -> set[str]:
    engine = create_engine(settings.database_url, future=True)
    try:
        inspector = inspect(engine)
        return {column["name"] for column in inspector.get_columns(table_name)}
    finally:
        engine.dispose()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )
