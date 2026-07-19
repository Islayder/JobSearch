from pathlib import Path
from shutil import copy2

from sqlalchemy import func, select
from typer.testing import CliRunner

from radar_vagas.canonicalization.normalize import normalize_company_name
from radar_vagas.cli.app import app
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.enums import EligibilityStatus
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.models import Company, Decision, Job, Posting

runner = CliRunner()


def test_full_cli_flow_and_fixture_idempotency(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.sqlite3"
    config_dir = _config_with_example_blocked(tmp_path)
    env = {
        "RADAR_DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
        "RADAR_CONFIG_DIR": str(config_dir),
    }
    fixture_path = PROJECT_ROOT / "data" / "fixtures" / "jobs.json"

    init_result = runner.invoke(app, ["init-db"], env=env)
    assert init_result.exit_code == 0, init_result.output

    import_result = runner.invoke(app, ["import-fixture", str(fixture_path)], env=env)
    assert import_result.exit_code == 0, import_result.output
    assert "Publicações criadas" in import_result.output

    second_import = runner.invoke(app, ["import-fixture", str(fixture_path)], env=env)
    assert second_import.exit_code == 0, second_import.output

    evaluate_result = runner.invoke(app, ["evaluate-all"], env=env)
    assert evaluate_result.exit_code == 0, evaluate_result.output

    list_result = runner.invoke(app, ["list-jobs"], env=env)
    assert list_result.exit_code == 0, list_result.output
    assert "Vagas" in list_result.output

    stats_result = runner.invoke(app, ["stats"], env=env)
    assert stats_result.exit_code == 0, stats_result.output
    assert "Resumo" in stats_result.output

    review_queue_result = runner.invoke(app, ["review-queue", "--limit", "5"], env=env)
    assert review_queue_result.exit_code == 0, review_queue_result.output
    assert "Fila" in review_queue_result.output

    applications_result = runner.invoke(app, ["applications"], env=env)
    assert applications_result.exit_code == 0, applications_result.output
    assert "candidatura" in applications_result.output.lower()

    profile_path = PROJECT_ROOT / "config" / "professional_profile.example.yaml"
    profile_result = runner.invoke(app, ["import-profile", str(profile_path)], env=env)
    assert profile_result.exit_code == 0, profile_result.output
    assert "Perfil" in profile_result.output

    profiles_result = runner.invoke(app, ["profiles"], env=env)
    assert profiles_result.exit_code == 0, profiles_result.output
    assert "Perfis" in profiles_result.output

    compare_result = runner.invoke(app, ["compare-profile", "1"], env=env)
    assert compare_result.exit_code == 0, compare_result.output
    assert "Compatibilidade" in compare_result.output

    compatibility_result = runner.invoke(app, ["show-compatibility", "1"], env=env)
    assert compatibility_result.exit_code == 0, compatibility_result.output
    assert "Requisitos" in compatibility_result.output

    settings = Settings(database_url=env["RADAR_DATABASE_URL"], config_dir=PROJECT_ROOT / "config")
    with session_scope(settings) as session:
        assert session.scalar(select(func.count(Posting.id))) == 18
        assert session.scalar(select(func.count(Job.id))) == 18

        blocked_job = _job_by_company(session, "Companhia Fictícia Bloqueada")
        assert blocked_job.decision is not None
        assert blocked_job.decision.eligibility_status is EligibilityStatus.INELIGIBLE

        trainee_six_hours = _job_by_title(session, "Trainee em Automação")
        assert trainee_six_hours.decision is not None
        assert trainee_six_hours.decision.eligibility_status is EligibilityStatus.ELIGIBLE

        trainee_eight_hours = _job_by_title(session, "Trainee em Software")
        assert trainee_eight_hours.decision is not None
        assert trainee_eight_hours.decision.eligibility_status is EligibilityStatus.INELIGIBLE

        trainee_without_hours = _job_by_title(session, "Trainee em Produto de Dados")
        assert trainee_without_hours.decision is not None
        assert trainee_without_hours.decision.eligibility_status is EligibilityStatus.MANUAL_REVIEW

        contagem = _job_by_company(session, "Mapa Planejamento SA")
        assert contagem.decision is not None
        assert contagem.decision.eligibility_status is EligibilityStatus.INELIGIBLE

        junior_onsite = _job_by_title(session, "Engenheiro de Software Júnior")
        assert junior_onsite.decision is not None
        assert junior_onsite.decision.eligibility_status is EligibilityStatus.INELIGIBLE

        remote_unknown = _job_by_company(session, "Norte Analytics SA")
        assert remote_unknown.decision is not None
        assert remote_unknown.decision.eligibility_status is EligibilityStatus.MANUAL_REVIEW

        ineligible_with_no_score = session.scalar(
            select(Decision).where(Decision.reason_code == "JUNIOR_ONSITE_NOT_ALLOWED")
        )
        assert ineligible_with_no_score is not None
        assert ineligible_with_no_score.ranking_score is None

        probable_jobs = session.scalar(
            select(func.count(Job.id))
            .join(Company)
            .where(Company.normalized_name == normalize_company_name("Bússola Riscos LTDA"))
        )
        assert probable_jobs == 2


def _job_by_company(session, company_name: str) -> Job:
    job = session.scalar(
        select(Job)
        .join(Company)
        .where(Company.normalized_name == normalize_company_name(company_name))
    )
    assert job is not None
    return job


def _job_by_title(session, title: str) -> Job:
    job = session.scalar(select(Job).where(Job.canonical_title == title))
    assert job is not None
    return job


def _config_with_example_blocked(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for filename in [
        "eligibility_rules.yaml",
        "ranking_weights.yaml",
        "profile.example.yaml",
        "professional_profile.example.yaml",
        "blocked_companies.example.yaml",
        "sources.example.yaml",
    ]:
        copy2(PROJECT_ROOT / "config" / filename, config_dir / filename)
    return config_dir
