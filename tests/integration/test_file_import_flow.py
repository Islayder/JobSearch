import json
from pathlib import Path

from sqlalchemy import func, select
from typer.testing import CliRunner

from radar_vagas.cli.app import app
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import EligibilityStatus
from radar_vagas.ingestion.file_import_service import (
    import_file,
    validate_import_file,
    write_import_report,
)
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import run_migrations
from radar_vagas.persistence.models import FileImportBatch, ImportItemAudit, Job, Posting

runner = CliRunner()


def test_dry_run_does_not_write_and_generates_report(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    import_path = PROJECT_ROOT / "data" / "fixtures" / "import-example.json"
    report_path = tmp_path / "report.json"

    with session_scope(settings) as session:
        report = validate_import_file(session, import_path, settings)
        write_import_report(report, report_path)
        assert session.scalar(select(func.count(Posting.id))) == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["dry_run"] is True
    assert payload["summary"]["linhas_lidas"] == 3
    assert payload["summary"]["validas"] == 3
    assert payload["summary"]["elegiveis"] == 2
    assert payload["summary"]["incompativeis"] == 1


def test_import_file_persists_audit_and_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    import_path = PROJECT_ROOT / "data" / "fixtures" / "import-example.csv"

    with session_scope(settings) as session:
        first = import_file(session, import_path, settings, delimiter=";")
        assert first.postings_created == 3

    with session_scope(settings) as session:
        second = import_file(session, import_path, settings, delimiter=";")
        assert second.postings_created == 0
        assert second.postings_skipped == 3

    with session_scope(settings) as session:
        assert session.scalar(select(func.count(Posting.id))) == 3
        assert session.scalar(select(func.count(Job.id))) == 3
        assert session.scalar(select(func.count(FileImportBatch.id))) == 2
        assert session.scalar(select(func.count(ImportItemAudit.id))) == 6
        junior_onsite = session.scalar(
            select(Job).where(Job.canonical_title == "Analista Junior de Dados")
        )
        assert junior_onsite is not None
        assert junior_onsite.decision is not None
        assert junior_onsite.decision.eligibility_status is EligibilityStatus.INELIGIBLE


def test_import_file_partial_invalid_processes_valid_items(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    import_path = tmp_path / "partial.json"
    import_path.write_text(
        json.dumps(
            [
                {"source_name": "Manual", "title": "Estágio", "company": "Empresa Parcial"},
                {"source_name": "Manual", "company": "Sem título"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with session_scope(settings) as session:
        result = import_file(session, import_path, settings)

    assert result.report.summary["validas"] == 1
    assert result.report.summary["invalidas"] == 1
    assert result.postings_created == 1


def test_import_file_detects_existing_duplicate_in_dry_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    import_path = PROJECT_ROOT / "data" / "fixtures" / "import-example.json"

    with session_scope(settings) as session:
        import_file(session, import_path, settings)

    with session_scope(settings) as session:
        report = validate_import_file(session, import_path, settings)

    assert report.summary["duplicatas_exatas"] == 3


def test_cli_import_validate_show_config_and_doctor(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run_migrations(settings)
    env = _env(tmp_path)
    import_path = PROJECT_ROOT / "data" / "fixtures" / "import-example.json"

    doctor = runner.invoke(app, ["doctor"], env=env)
    assert doctor.exit_code == 0, doctor.output
    assert "Doctor" in doctor.output

    show_config = runner.invoke(app, ["show-config"], env=env)
    assert show_config.exit_code == 0, show_config.output
    assert "Engenharia de Software" in show_config.output
    assert "PUC Minas" in show_config.output

    validate = runner.invoke(app, ["validate-file", str(import_path)], env=env)
    assert validate.exit_code == 0, validate.output
    assert "Validação concluída" in validate.output

    dry_run = runner.invoke(app, ["import-file", str(import_path), "--dry-run"], env=env)
    assert dry_run.exit_code == 0, dry_run.output
    assert "Simulação concluída" in dry_run.output

    imported = runner.invoke(app, ["import-file", str(import_path)], env=env)
    assert imported.exit_code == 0, imported.output
    assert "Importação concluída" in imported.output


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        config_dir=PROJECT_ROOT / "config",
    )


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "RADAR_DATABASE_URL": f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        "RADAR_CONFIG_DIR": str(PROJECT_ROOT / "config"),
    }
