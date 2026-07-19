from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from typer.testing import CliRunner

from radar_vagas.cli.app import app
from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.http.client import HttpRequestResult
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.models import Posting

runner = CliRunner()


def test_cli_import_url_dry_run_with_mocked_http(tmp_path: Path, monkeypatch) -> None:
    env = _env(tmp_path)
    monkeypatch.setattr("radar_vagas.cli.app.HttpClient", _fake_http_client("jobposting"))
    init_result = runner.invoke(app, ["init-db"], env=env)
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        app,
        ["import-url", "https://public.example/job/123", "--dry-run"],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert "Encontradas" in result.output
    with session_scope(
        Settings(database_url=env["RADAR_DATABASE_URL"], config_dir=Path(env["RADAR_CONFIG_DIR"]))
    ) as session:
        assert session.scalar(select(func.count(Posting.id))) == 0


def test_cli_collectors_boards_show_board_and_source_health(tmp_path: Path) -> None:
    env = _env(tmp_path)
    assert runner.invoke(app, ["init-db"], env=env).exit_code == 0

    collectors = runner.invoke(app, ["collectors"], env=env)
    assert collectors.exit_code == 0
    assert "greenhouse" in collectors.output
    assert "lever" in collectors.output

    boards = runner.invoke(app, ["boards"], env=env)
    assert boards.exit_code == 0, boards.output
    assert "empresa-greenhouse" in boards.output

    show = runner.invoke(app, ["show-board", "empresa-greenhouse"], env=env)
    assert show.exit_code == 0, show.output
    assert "Board token" in show.output

    health = runner.invoke(app, ["source-health"], env=env)
    assert health.exit_code == 0, health.output
    assert "Boards ativos" in health.output


def test_cli_collect_board_direct_and_collect_all_dry_run(tmp_path: Path, monkeypatch) -> None:
    env = _env(tmp_path)
    monkeypatch.setattr("radar_vagas.cli.app.HttpClient", _fake_http_client("greenhouse"))
    assert runner.invoke(app, ["init-db"], env=env).exit_code == 0

    direct = runner.invoke(
        app,
        [
            "collect-board",
            "greenhouse",
            "--board-token",
            "empresa",
            "--company",
            "Empresa Exemplo",
            "--dry-run",
        ],
        env=env,
    )
    assert direct.exit_code == 0, direct.output
    assert "Coleta" in direct.output or "Simulacao" in direct.output

    all_result = runner.invoke(app, ["collect-all", "--dry-run"], env=env)
    assert all_result.exit_code == 0, all_result.output
    assert "empresa-greenhouse" in all_result.output


def test_cli_import_url_report_file(tmp_path: Path, monkeypatch) -> None:
    env = _env(tmp_path)
    report = tmp_path / "report.json"
    monkeypatch.setattr("radar_vagas.cli.app.HttpClient", _fake_http_client("jobposting"))
    assert runner.invoke(app, ["init-db"], env=env).exit_code == 0

    result = runner.invoke(
        app,
        [
            "import-url",
            "https://public.example/job/123",
            "--dry-run",
            "--report",
            str(report),
        ],
        env=env,
    )

    assert result.exit_code == 0, result.output
    assert report.exists()
    assert '"found": 1' in report.read_text(encoding="utf-8")


def _env(tmp_path: Path) -> dict[str, str]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "company_boards.yaml").write_text(
        """
boards:
  - key: empresa-greenhouse
    company_name: Empresa Exemplo
    collector: greenhouse
    board_token: empresa
    enabled: true
    tags: [remote, technology]
""",
        encoding="utf-8",
    )
    return {
        "RADAR_DATABASE_URL": f"sqlite:///{(tmp_path / 'radar.sqlite3').as_posix()}",
        "RADAR_CONFIG_DIR": str(config_dir),
    }


def _fake_http_client(kind: str):
    class FakeHttpClient:
        def __init__(self, _config: Any) -> None:
            pass

        def get(self, url: str, *, headers: dict[str, str] | None = None) -> HttpRequestResult:
            del headers
            if kind == "jobposting":
                content_type = "text/html"
                content = (
                    PROJECT_ROOT / "tests" / "fixtures" / "http" / "jobposting" / "single.html"
                ).read_bytes()
            else:
                content_type = "application/json"
                content = (
                    PROJECT_ROOT / "tests" / "fixtures" / "http" / "greenhouse" / "list.json"
                ).read_bytes()
            return HttpRequestResult(
                url=url,
                status_code=200,
                headers={"content-type": content_type},
                content=content,
                requests_made=1,
                bytes_received=len(content),
                retries=0,
                redirects=0,
            )

        def close(self) -> None:
            pass

    return FakeHttpClient
