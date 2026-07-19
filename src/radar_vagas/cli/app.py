import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import Select, desc, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from radar_vagas.collection.orchestrator import (
    build_collection_context,
    load_board_cache_headers,
    record_failed_collection,
    run_collection_persistence,
)
from radar_vagas.collection.result import (
    CollectionExecutionReport,
    write_collection_report,
)
from radar_vagas.collectors.registry import get_collector, list_collectors
from radar_vagas.config.loaders import (
    load_blocked_companies,
    load_company_boards,
    load_eligibility_rules,
    load_network_config,
    load_profile,
    load_ranking_weights,
)
from radar_vagas.config.schemas import BoardConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    WorkModel,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.eligibility.workflow import (
    evaluate_all_jobs,
    evaluate_job_by_id,
)
from radar_vagas.http.client import HttpClient
from radar_vagas.ingestion.file_import_service import (
    ImportExecutionResult,
    ImportFileReport,
    import_file,
    validate_import_file,
    write_import_report,
)
from radar_vagas.ingestion.service import import_fixture
from radar_vagas.persistence.database import session_scope
from radar_vagas.persistence.migrations import database_display_path, run_migrations
from radar_vagas.persistence.models import (
    Company,
    CompanyBoard,
    Decision,
    ImportItemAudit,
    Job,
    Posting,
    SourceRun,
)

console = Console()
app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Radar de Vagas: avaliação local de oportunidades profissionais.",
)


@dataclass(frozen=True)
class CliState:
    debug: bool


@dataclass(frozen=True)
class ResolvedBoard:
    config: BoardConfig
    persist: bool


@app.callback()
def main(
    ctx: typer.Context,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Exibe traceback completo para depuração."),
    ] = False,
) -> None:
    ctx.obj = CliState(debug=debug)


@app.command("init-db")
def init_db(ctx: typer.Context) -> None:
    """Cria diretórios e aplica migrações Alembic."""

    def action() -> None:
        settings = _settings(ctx)
        run_migrations(settings)
        console.print(f"Banco inicializado em: [bold]{database_display_path(settings)}[/bold]")

    _run(ctx, action)


@app.command("import-fixture")
def import_fixture_command(
    ctx: typer.Context,
    fixture_path: Annotated[Path, typer.Argument(help="Arquivo JSON local com vagas de teste.")],
) -> None:
    """Importa publicações de um JSON local."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            summary = import_fixture(session, fixture_path, settings)
        table = Table(title="Importação concluída")
        table.add_column("Métrica")
        table.add_column("Valor", justify="right")
        table.add_row("Itens no arquivo", str(summary.items_found))
        table.add_row("Publicações criadas", str(summary.postings_created))
        table.add_row("Publicações ignoradas", str(summary.postings_skipped))
        table.add_row("Vagas criadas", str(summary.jobs_created))
        table.add_row("Vagas associadas com segurança", str(summary.jobs_linked))
        table.add_row("Duplicatas prováveis pendentes", str(summary.probable_duplicates))
        table.add_row("Fontes criadas", str(summary.sources_created))
        table.add_row("Empresas criadas", str(summary.companies_created))
        console.print(table)

    _run(ctx, action)


@app.command("import-file")
def import_file_command(
    ctx: typer.Context,
    file_path: Annotated[Path, typer.Argument(help="Arquivo local .json ou .csv.")],
    delimiter: Annotated[
        str | None,
        typer.Option("--delimiter", help="Delimitador CSV: vírgula ou ponto e vírgula."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Valida e simula sem alterar o banco."),
    ] = False,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatório JSON de validação."),
    ] = None,
) -> None:
    """Importa publicações de JSON ou CSV local."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            result = import_file(
                session,
                file_path,
                settings,
                delimiter=delimiter,
                dry_run=dry_run,
            )
            if dry_run:
                session.rollback()
        if report is not None:
            write_import_report(result.report, report)
        _print_import_file_result(result, dry_run=dry_run)

    _run(ctx, action)


@app.command("validate-file")
def validate_file_command(
    ctx: typer.Context,
    file_path: Annotated[Path, typer.Argument(help="Arquivo local .json ou .csv.")],
    delimiter: Annotated[
        str | None,
        typer.Option("--delimiter", help="Delimitador CSV: vírgula ou ponto e vírgula."),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatório JSON de validação."),
    ] = None,
) -> None:
    """Valida e simula uma importação sem escrita."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            validation_report = validate_import_file(
                session,
                file_path,
                settings,
                delimiter=delimiter,
                dry_run=True,
            )
            session.rollback()
        if report is not None:
            write_import_report(validation_report, report)
        _print_import_report(validation_report, title="Validação concluída")

    _run(ctx, action)


@app.command("import-url")
def import_url_command(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="Pagina publica com JSON-LD JobPosting.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Coleta e valida sem alterar o banco."),
    ] = False,
    include_all: Annotated[
        bool,
        typer.Option("--all", help="Processa todos os objetos JobPosting da pagina."),
    ] = False,
    selected_index: Annotated[
        int | None,
        typer.Option("--select", help="Processa apenas o objeto JobPosting de indice informado."),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatorio JSON de coleta."),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Exibe traceback completo para este comando."),
    ] = False,
) -> None:
    """Importa uma pagina publica com dados estruturados JobPosting."""

    def action() -> None:
        _apply_command_debug(ctx, debug)
        settings = _settings(ctx)
        network = load_network_config(settings.config_dir)
        client = HttpClient(network.http)
        context = build_collection_context(
            collector="jobposting",
            company_name=None,
            url=url,
            dry_run=dry_run,
        )
        context = replace(
            context,
            http_client=client,
            collection_config=network.collection,
            include_all=include_all,
            selected_index=selected_index,
        )
        try:
            result = get_collector("jobposting").collect(context)
        except Exception as exc:
            with session_scope(settings) as session:
                record_failed_collection(session, context, exc, settings=settings)
            raise
        finally:
            client.close()
        with session_scope(settings) as session:
            execution = run_collection_persistence(session, settings, context, result)
        _write_and_print_collection_report(execution, report)

    _run(ctx, action)


@app.command("collectors")
def collectors_command() -> None:
    """Lista coletores publicos registrados."""

    table = Table(title="Coletores")
    table.add_column("Slug")
    table.add_column("Nome")
    table.add_column("Tipo")
    table.add_column("Snapshot")
    table.add_column("Autenticacao")
    table.add_column("Estado")
    for metadata in list_collectors():
        table.add_row(
            metadata.slug,
            metadata.name,
            metadata.collector_type,
            "sim" if metadata.supports_complete_snapshot else "nao",
            metadata.authentication,
            metadata.status,
        )
    console.print(table)


@app.command("collect-board")
def collect_board_command(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Key configurada ou slug do coletor.")],
    board_token: Annotated[
        str | None,
        typer.Option("--board-token", help="Token publico do board Greenhouse ou Lever."),
    ] = None,
    company: Annotated[
        str | None,
        typer.Option("--company", help="Nome canonico da empresa."),
    ] = None,
    max_items: Annotated[
        int | None,
        typer.Option("--max-items", help="Limite de itens nesta coleta."),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option("--since", help="Data minima ISO 8601, quando o coletor suportar."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Coleta e valida sem alterar o banco."),
    ] = False,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatorio JSON de coleta."),
    ] = None,
    save_board: Annotated[
        str | None,
        typer.Option("--save-board", help="Persiste o board direto com a key informada."),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Exibe traceback completo para este comando."),
    ] = False,
) -> None:
    """Coleta um board configurado ou um board publico por token."""

    def action() -> None:
        _apply_command_debug(ctx, debug)
        settings = _settings(ctx)
        board = _resolve_board_config(
            settings,
            target=target,
            board_token=board_token,
            company=company,
            save_board=save_board,
        )
        execution = _collect_single_board(
            settings,
            board,
            dry_run=dry_run,
            max_items=max_items,
            since=since,
        )
        _write_and_print_collection_report(execution, report)

    _run(ctx, action)


@app.command("collect-all")
def collect_all_command(
    ctx: typer.Context,
    collector: Annotated[
        str | None,
        typer.Option("--collector", help="Filtra por coletor: greenhouse, lever ou jobposting."),
    ] = None,
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Filtra boards por tag. Pode ser usado mais de uma vez."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Coleta e valida sem alterar o banco."),
    ] = False,
    max_items_per_board: Annotated[
        int | None,
        typer.Option("--max-items-per-board", help="Limite por board."),
    ] = None,
    continue_on_error: Annotated[
        bool,
        typer.Option("--continue-on-error/--stop-on-error", help="Continua apos falhas."),
    ] = True,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatorio JSON consolidado."),
    ] = None,
) -> None:
    """Coleta todos os boards ativos configurados."""

    def action() -> None:
        settings = _settings(ctx)
        boards = _filtered_boards(settings, collector=collector, tags=tag or [])
        executions: list[CollectionExecutionReport] = []
        errors: list[str] = []
        for board in boards:
            try:
                executions.append(
                    _collect_single_board(
                        settings,
                        ResolvedBoard(config=board, persist=True),
                        dry_run=dry_run,
                        max_items=max_items_per_board,
                        since=None,
                    )
                )
            except Exception as exc:
                errors.append(f"{board.key}: {exc}")
                if not continue_on_error:
                    raise
        _print_collect_all_result(executions, errors)
        if report is not None:
            _write_collect_all_report(executions, errors, report)
        if errors:
            raise typer.Exit(1)

    _run(ctx, action)


@app.command("boards")
def boards_command(
    ctx: typer.Context,
    collector: Annotated[
        str | None,
        typer.Option("--collector", help="Filtra por coletor."),
    ] = None,
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Filtra boards ativos ou inativos."),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", help="Filtra por tag."),
    ] = None,
) -> None:
    """Lista boards configurados e a saude persistida."""

    def action() -> None:
        settings = _settings(ctx)
        boards = load_company_boards(settings.config_dir).boards
        if collector is not None:
            boards = [board for board in boards if board.collector == collector]
        if enabled is not None:
            boards = [board for board in boards if board.enabled is enabled]
        if tag is not None:
            boards = [board for board in boards if tag.lower() in board.tags]
        with session_scope(settings) as session:
            _print_boards(session, boards)

    _run(ctx, action)


@app.command("show-board")
def show_board_command(
    ctx: typer.Context,
    board_key: Annotated[str, typer.Argument(help="Key do board configurado.")],
) -> None:
    """Mostra configuracao segura e historico recente de um board."""

    def action() -> None:
        settings = _settings(ctx)
        board_config = _board_by_key(load_company_boards(settings.config_dir).boards, board_key)
        if board_config is None:
            raise RadarError(f"Board nao encontrado: {board_key}")
        with session_scope(settings) as session:
            _print_board_detail(session, board_config)

    _run(ctx, action)


@app.command("source-health")
def source_health_command(ctx: typer.Context) -> None:
    """Mostra um resumo da saude das fontes configuradas."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            _print_source_health(session)

    _run(ctx, action)


@app.command("evaluate-all")
def evaluate_all(ctx: typer.Context) -> None:
    """Avalia vagas novas ou pendentes."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            summary = evaluate_all_jobs(session, settings)
        table = Table(title="Avaliação concluída")
        table.add_column("Métrica")
        table.add_column("Valor", justify="right")
        table.add_row("Vagas avaliadas", str(summary.total))
        table.add_row("Elegíveis", str(summary.eligible))
        table.add_row("Revisão manual", str(summary.manual_review))
        table.add_row("Incompatíveis", str(summary.ineligible))
        table.add_row("Apenas acompanhamento", str(summary.track_only))
        table.add_row("Recomendadas", str(summary.recommended))
        console.print(table)

    _run(ctx, action)


@app.command("evaluate-job")
def evaluate_job(
    ctx: typer.Context, job_id: Annotated[int, typer.Argument(help="ID da vaga.")]
) -> None:
    """Avalia uma vaga específica."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            decision = evaluate_job_by_id(session, job_id, settings)
            session.flush()
            console.print(
                f"Vaga {job_id} avaliada: "
                f"[bold]{decision.eligibility_status.value.lower()}[/bold] "
                f"({decision.reason_code})."
            )

    _run(ctx, action)


@app.command("list-jobs")
def list_jobs(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option(help="Filtra por estado da vaga.")] = None,
    employment_type: Annotated[
        str | None, typer.Option(help="Filtra por tipo: internship, trainee, junior...")
    ] = None,
    work_model: Annotated[
        str | None, typer.Option(help="Filtra por modalidade: remote, hybrid, onsite.")
    ] = None,
    city: Annotated[str | None, typer.Option(help="Filtra por cidade canônica.")] = None,
    min_score: Annotated[int | None, typer.Option(help="Nota mínima do ranking.")] = None,
) -> None:
    """Lista vagas com filtros básicos."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            rows = _query_jobs(
                session,
                status=status,
                employment_type=employment_type,
                work_model=work_model,
                city=city,
                min_score=min_score,
            )
            _print_jobs_table(rows)

    _run(ctx, action)


@app.command("show-job")
def show_job(
    ctx: typer.Context, job_id: Annotated[int, typer.Argument(help="ID da vaga.")]
) -> None:
    """Mostra detalhes de uma vaga."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            job = session.get(Job, job_id)
            if job is None:
                raise RadarError(f"Vaga não encontrada: {job_id}")
            _print_job_detail(job)

    _run(ctx, action)


@app.command("stats")
def stats(ctx: typer.Context) -> None:
    """Mostra resumo do banco."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            _print_stats(session)

    _run(ctx, action)


@app.command("show-config")
def show_config(
    ctx: typer.Context,
    profile: Annotated[
        Path | None,
        typer.Option("--profile", help="Caminho alternativo para profile.yaml."),
    ] = None,
) -> None:
    """Mostra configuração carregada de forma segura."""

    def action() -> None:
        settings = _settings(ctx)
        loaded_profile = load_profile(settings.config_dir, profile or settings.profile_path)
        rules = load_eligibility_rules(settings.config_dir)
        blocked = load_blocked_companies(settings.config_dir)
        profile_data = loaded_profile.profile

        table = Table(title="Configuração")
        table.add_column("Campo")
        table.add_column("Valor")
        table.add_row("Perfil", str(loaded_profile.path))
        table.add_row("Usando exemplo", "sim" if loaded_profile.used_example else "não")
        table.add_row("Cidade", f"{profile_data.user.city}, {profile_data.user.state}")
        table.add_row("Curso", profile_data.education.course)
        table.add_row("Instituição", profile_data.education.institution)
        table.add_row("Modalidades", ", ".join(profile_data.location_preferences.priority))
        table.add_row("Tipos", ", ".join(profile_data.opportunity_priority))
        table.add_row("Empresas bloqueadas", str(len(blocked.all_companies)))
        table.add_row("Banco", database_display_path(settings))
        table.add_row("Versão das regras", rules.rules_version)
        console.print(table)

        if loaded_profile.used_example:
            console.print(
                "[yellow]Aviso:[/yellow] profile.yaml real não encontrado; usando exemplo."
            )

    _run(ctx, action)


@app.command("doctor")
def doctor(ctx: typer.Context) -> None:
    """Verifica ambiente local sem alterar arquivos."""

    def action() -> None:
        settings = _settings(ctx)
        checks = _doctor_checks(settings)
        table = Table(title="Doctor")
        table.add_column("Status")
        table.add_column("Verificação")
        table.add_column("Detalhe")
        for status, name, detail in checks:
            style = {"OK": "green", "AVISO": "yellow", "ERRO": "red"}[status]
            table.add_row(f"[{style}]{status}[/{style}]", name, detail)
        console.print(table)
        if any(status == "ERRO" for status, _name, _detail in checks):
            raise typer.Exit(1)

    _run(ctx, action)


def _settings(ctx: typer.Context) -> Settings:
    return Settings.from_env(debug=_state(ctx).debug)


def _state(ctx: typer.Context) -> CliState:
    if isinstance(ctx.obj, CliState):
        return ctx.obj
    return CliState(debug=False)


def _apply_command_debug(ctx: typer.Context, debug: bool) -> None:
    if debug:
        ctx.obj = CliState(debug=True)


def _resolve_board_config(
    settings: Settings,
    *,
    target: str,
    board_token: str | None,
    company: str | None,
    save_board: str | None,
) -> ResolvedBoard:
    configured = _board_by_key(load_company_boards(settings.config_dir).boards, target)
    if configured is not None:
        return ResolvedBoard(config=configured, persist=True)

    collector_slug = target.strip().lower()
    if collector_slug not in {"greenhouse", "lever", "jobposting"}:
        raise RadarError(f"Board ou coletor desconhecido: {target}")
    if collector_slug in {"greenhouse", "lever"} and (not board_token or not company):
        raise RadarError("--board-token e --company sao obrigatorios para coleta direta.")
    if collector_slug == "jobposting":
        raise RadarError("Use `radar import-url <url>` para paginas JobPosting individuais.")
    key = save_board or f"{collector_slug}-{board_token}"
    board = BoardConfig(
        key=key,
        company_name=company or "",
        collector=collector_slug,
        board_token=board_token,
        enabled=True,
        priority=100,
        tags=[],
        notes="Criado pela CLI com --save-board." if save_board else None,
    )
    return ResolvedBoard(config=board, persist=save_board is not None)


def _collect_single_board(
    settings: Settings,
    resolved: ResolvedBoard,
    *,
    dry_run: bool,
    max_items: int | None,
    since: str | None,
) -> CollectionExecutionReport:
    board = resolved.config
    network = load_network_config(settings.config_dir)
    client = HttpClient(network.http)
    cache_etag: str | None = None
    cache_last_modified: str | None = None
    if not dry_run and resolved.persist:
        with session_scope(settings) as session:
            cache_etag, cache_last_modified = load_board_cache_headers(session, board.key)
    context = build_collection_context(
        collector=board.collector,
        company_name=board.company_name,
        board_key=board.key,
        board_token=board.board_token,
        url=board.url,
        dry_run=dry_run,
        max_items=max_items,
        since=_parse_optional_datetime(since),
    )
    context = replace(
        context,
        http_client=client,
        collection_config=network.collection,
        cache_etag=cache_etag,
        cache_last_modified=cache_last_modified,
    )
    try:
        result = get_collector(board.collector).collect(context)
    except Exception as exc:
        with session_scope(settings) as session:
            record_failed_collection(
                session,
                context,
                exc,
                board_config=board if resolved.persist else None,
                settings=settings,
            )
        raise
    finally:
        client.close()
    with session_scope(settings) as session:
        return run_collection_persistence(
            session,
            settings,
            context,
            result,
            board_config=board if resolved.persist else None,
        )


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise RadarError(f"Data invalida em --since: {value}") from exc


def _filtered_boards(
    settings: Settings,
    *,
    collector: str | None,
    tags: list[str],
) -> list[BoardConfig]:
    boards = load_company_boards(settings.config_dir).enabled_boards()
    if collector is not None:
        boards = [board for board in boards if board.collector == collector.strip().lower()]
    normalized_tags = {tag.strip().lower() for tag in tags if tag.strip()}
    if normalized_tags:
        boards = [board for board in boards if normalized_tags.issubset(set(board.tags))]
    return sorted(boards, key=lambda board: (board.priority, board.key))


def _board_by_key(boards: list[BoardConfig], board_key: str) -> BoardConfig | None:
    for board in boards:
        if board.key == board_key:
            return board
    return None


def _write_and_print_collection_report(
    execution: CollectionExecutionReport,
    report_path: Path | None,
) -> None:
    if report_path is not None:
        write_collection_report(execution, report_path)
    _print_collection_execution(execution)


def _print_collection_execution(execution: CollectionExecutionReport) -> None:
    title = "Simulacao de coleta concluida" if execution.dry_run else "Coleta concluida"
    table = Table(title=title)
    table.add_column("Metrica")
    table.add_column("Valor", justify="right")
    table.add_row("Coletor", execution.collector)
    table.add_row("Board", execution.board or "-")
    table.add_row("Requests", str(execution.network.get("requests", 0)))
    table.add_row("Bytes", str(execution.network.get("bytes_received", 0)))
    labels = {
        "found": "Encontradas",
        "new": "Novas",
        "unchanged": "Conhecidas inalteradas",
        "changed": "Alteradas",
        "exact_duplicates": "Duplicatas exatas",
        "probable_duplicates": "Duplicatas provaveis",
        "eligible": "Elegiveis",
        "manual_review": "Revisao manual",
        "ineligible": "Incompativeis",
        "closed": "Encerradas",
        "reopened": "Reabertas",
        "invalid_items": "Itens invalidos",
    }
    summary = execution.summary.to_dict()
    for key, label in labels.items():
        table.add_row(label, str(summary.get(key, 0)))
    console.print(table)
    for warning in execution.warnings:
        console.print(f"[yellow]Aviso:[/yellow] {warning}")


def _print_collect_all_result(
    executions: list[CollectionExecutionReport],
    errors: list[str],
) -> None:
    table = Table(title="Coleta de boards")
    table.add_column("Board", no_wrap=True)
    table.add_column("Coletor")
    table.add_column("Encontradas", justify="right")
    table.add_column("Novas", justify="right")
    table.add_column("Alteradas", justify="right")
    table.add_column("Encerradas", justify="right")
    for execution in executions:
        summary = execution.summary
        table.add_row(
            execution.board or "-",
            execution.collector,
            str(summary.found),
            str(summary.new),
            str(summary.changed),
            str(summary.closed),
        )
    console.print(table)
    for error in errors:
        console.print(f"[red]Falha:[/red] {error}")


def _write_collect_all_report(
    executions: list[CollectionExecutionReport],
    errors: list[str],
    report_path: Path,
) -> None:
    payload = {
        "boards": [execution.to_dict() for execution in executions],
        "errors": errors,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run[ReturnType](ctx: typer.Context, action: Callable[[], ReturnType]) -> ReturnType:
    try:
        return action()
    except RadarError as exc:
        _fail(ctx, str(exc))
    except SQLAlchemyError as exc:
        _fail(
            ctx,
            "Banco indisponível ou não inicializado. Execute `radar init-db` e tente novamente.",
            exc,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(ctx, f"Falha inesperada: {exc}", exc)


def _fail(ctx: typer.Context, message: str, exc: Exception | None = None) -> NoReturn:
    if _state(ctx).debug and exc is not None:
        raise exc
    console.print(f"[red]Erro:[/red] {message}")
    raise typer.Exit(1)


def _print_import_file_result(result: ImportExecutionResult, *, dry_run: bool) -> None:
    title = "Simulação concluída" if dry_run else "Importação concluída"
    _print_import_report(result.report, title=title)
    if not dry_run:
        table = Table(title="Persistência")
        table.add_column("Métrica")
        table.add_column("Valor", justify="right")
        table.add_row("Publicações criadas", str(result.postings_created))
        table.add_row("Publicações ignoradas", str(result.postings_skipped))
        table.add_row("Vagas criadas", str(result.jobs_created))
        table.add_row("Duplicatas prováveis preservadas", str(result.probable_duplicates))
        console.print(table)


def _print_import_report(report: ImportFileReport, *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Métrica")
    table.add_column("Valor", justify="right")
    labels = {
        "linhas_lidas": "Linhas lidas",
        "validas": "Válidas",
        "invalidas": "Inválidas",
        "duplicatas_exatas": "Duplicatas exatas",
        "duplicatas_provaveis": "Duplicatas prováveis",
        "elegiveis": "Elegíveis",
        "revisao_manual": "Revisão manual",
        "incompativeis": "Incompatíveis",
    }
    for key, label in labels.items():
        table.add_row(label, str(report.summary.get(key, 0)))
    console.print(table)


def _query_jobs(
    session: Session,
    *,
    status: str | None,
    employment_type: str | None,
    work_model: str | None,
    city: str | None,
    min_score: int | None,
) -> list[tuple[Job, Company, Decision | None]]:
    statement = select(Job, Company, Decision).join(Company).outerjoin(Decision)
    if status is not None:
        statement = statement.where(Job.status == parse_enum_value(JobStatus, status))
    if employment_type is not None:
        statement = statement.where(
            Job.employment_type == parse_enum_value(EmploymentType, employment_type)
        )
    if work_model is not None:
        statement = statement.where(Job.work_model == parse_enum_value(WorkModel, work_model))
    if city is not None:
        statement = statement.where(Job.city == city)
    if min_score is not None:
        statement = statement.where(Decision.ranking_score >= min_score)
    statement = statement.order_by(Decision.ranking_score.desc(), Job.id.asc())
    rows = session.execute(statement).all()
    return [(row[0], row[1], row[2]) for row in rows]


def _print_jobs_table(rows: list[tuple[Job, Company, Decision | None]]) -> None:
    table = Table(title="Vagas")
    table.add_column("ID", justify="right")
    table.add_column("Empresa")
    table.add_column("Título")
    table.add_column("Tipo")
    table.add_column("Modalidade")
    table.add_column("Localização")
    table.add_column("Elegibilidade")
    table.add_column("Nota", justify="right")
    table.add_column("Estado")

    for job, company, decision in rows:
        table.add_row(
            str(job.id),
            company.canonical_name,
            job.canonical_title,
            job.employment_type.value.lower(),
            job.work_model.value.lower(),
            _location_label(job),
            _eligibility_label(decision),
            "-"
            if decision is None or decision.ranking_score is None
            else str(decision.ranking_score),
            job.status.value.lower(),
        )

    if rows:
        console.print(table)
    else:
        console.print("Nenhuma vaga encontrada para os filtros informados.")


def _print_job_detail(job: Job) -> None:
    decision = job.decision
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("ID", str(job.id))
    details.add_row("Empresa", job.company.canonical_name)
    details.add_row("Título", job.canonical_title)
    details.add_row("Tipo", job.employment_type.value.lower())
    details.add_row("Modalidade", job.work_model.value.lower())
    details.add_row("Localização", _location_label(job))
    details.add_row("Estado", job.status.value.lower())
    details.add_row("URL", job.application_url or "-")
    details.add_row("Curso", job.course_requirement or "-")
    console.print(Panel(details, title="Vaga canônica"))

    postings = Table(title="Publicações associadas")
    postings.add_column("ID", justify="right")
    postings.add_column("Fonte")
    postings.add_column("URL")
    postings.add_column("Status")
    for posting in job.postings:
        postings.add_row(
            str(posting.id),
            posting.source.name,
            posting.original_url,
            posting.status.value.lower(),
        )
    console.print(postings)

    decision_table = Table(title="Decisão")
    decision_table.add_column("Campo")
    decision_table.add_column("Valor")
    if decision is None:
        decision_table.add_row("Elegibilidade", "não avaliada")
    else:
        decision_table.add_row("Elegibilidade", decision.eligibility_status.value.lower())
        decision_table.add_row("Motivo", decision.reason_code)
        decision_table.add_row("Descrição", decision.reason_text)
        decision_table.add_row(
            "Nota", "-" if decision.ranking_score is None else str(decision.ranking_score)
        )
        decision_table.add_row("Ranking", _ranking_breakdown_label(decision))
        decision_table.add_row("Versão das regras", decision.rules_version)
    console.print(decision_table)

    applications = Table(title="Candidaturas")
    applications.add_column("ID", justify="right")
    applications.add_column("Status")
    applications.add_column("Plataforma")
    if job.applications:
        for application in job.applications:
            applications.add_row(
                str(application.id),
                application.status.value.lower(),
                application.platform or "-",
            )
    else:
        applications.add_row("-", "nenhuma", "-")
    console.print(applications)


def _print_stats(session: Session) -> None:
    total_postings = _count_postings(session)
    total_jobs = _count_jobs_total(session)
    eligible = _count_decisions(session, EligibilityStatus.ELIGIBLE)
    manual_review = _count_decisions(session, EligibilityStatus.MANUAL_REVIEW)
    ineligible = _count_decisions(session, EligibilityStatus.INELIGIBLE)
    applied = _count_jobs(session, JobStatus.APPLIED)
    dismissed = _count_jobs(session, JobStatus.DISMISSED)
    source_run_skipped = session.scalar(select(func.coalesce(func.sum(SourceRun.items_skipped), 0)))
    import_skipped = session.scalar(
        select(func.count(ImportItemAudit.id)).where(ImportItemAudit.status == "skipped_duplicate")
    )

    table = Table(title="Resumo")
    table.add_column("Métrica")
    table.add_column("Valor", justify="right")
    table.add_row("Publicações", str(total_postings))
    table.add_row("Vagas canônicas", str(total_jobs))
    table.add_row("Elegíveis", str(eligible))
    table.add_row("Revisão manual", str(manual_review))
    table.add_row("Incompatíveis", str(ineligible))
    table.add_row("Aplicadas", str(applied))
    table.add_row("Descartadas", str(dismissed))
    table.add_row("Duplicatas ignoradas", str((source_run_skipped or 0) + (import_skipped or 0)))
    console.print(table)

    _print_distribution(
        "Distribuição por tipo",
        _employment_type_distribution(session),
    )
    _print_distribution(
        "Distribuição por modalidade",
        _work_model_distribution(session),
    )


def _print_boards(session: Session, boards: list[BoardConfig]) -> None:
    table = Table(title="Boards")
    table.add_column("Key", no_wrap=True)
    table.add_column("Empresa")
    table.add_column("Coletor")
    table.add_column("Ativo")
    table.add_column("Ultima execucao")
    table.add_column("Ultimo sucesso")
    table.add_column("Falhas", justify="right")
    table.add_column("Publicacoes ativas", justify="right")
    table.add_column("Estado")
    for board in boards:
        db_board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board.key))
        active_postings = _active_postings_for_board(session, db_board) if db_board else 0
        table.add_row(
            board.key,
            board.company_name,
            board.collector,
            "sim" if board.enabled else "nao",
            _date_or_dash(db_board.last_checked_at if db_board else None),
            _date_or_dash(db_board.last_success_at if db_board else None),
            str(db_board.consecutive_failures if db_board else 0),
            str(active_postings),
            _board_state(board, db_board),
        )
    if boards:
        console.print(table)
    else:
        console.print("Nenhum board encontrado para os filtros informados.")


def _print_board_detail(session: Session, board_config: BoardConfig) -> None:
    db_board = session.scalar(select(CompanyBoard).where(CompanyBoard.key == board_config.key))
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Key", board_config.key)
    details.add_row("Empresa", board_config.company_name)
    details.add_row("Coletor", board_config.collector)
    details.add_row("Ativo", "sim" if board_config.enabled else "nao")
    details.add_row("Board token", "presente" if board_config.board_token else "-")
    details.add_row("URL", board_config.url or "-")
    details.add_row("Tags", ", ".join(board_config.tags) or "-")
    details.add_row("Notas", board_config.notes or "-")
    if db_board is not None:
        details.add_row("Ultima coleta", _date_or_dash(db_board.last_checked_at))
        details.add_row("Ultimo sucesso", _date_or_dash(db_board.last_success_at))
        details.add_row("Ultima falha", _date_or_dash(db_board.last_failed_at))
        details.add_row("Falhas consecutivas", str(db_board.consecutive_failures))
        details.add_row("Ultimo ETag", db_board.last_etag or "-")
        details.add_row("Last-Modified", db_board.last_modified or "-")
        details.add_row(
            "Ultimo snapshot completo",
            _date_or_dash(db_board.last_complete_snapshot_at),
        )
        details.add_row(
            "Publicacoes ativas",
            str(_active_postings_for_board(session, db_board)),
        )
        details.add_row(
            "Ausencias pendentes",
            str(_pending_absences_for_board(session, db_board)),
        )
    console.print(Panel(details, title="Board"))

    if db_board is None:
        console.print("Board ainda nao possui historico persistido.")
        return

    runs = session.scalars(
        select(SourceRun)
        .where(SourceRun.source_id == db_board.source_id)
        .order_by(desc(SourceRun.started_at))
        .limit(5)
    ).all()
    run_table = Table(title="Execucoes recentes")
    run_table.add_column("ID", justify="right")
    run_table.add_column("Status")
    run_table.add_column("Inicio")
    run_table.add_column("Fim")
    run_table.add_column("Encontradas", justify="right")
    run_table.add_column("Criadas", justify="right")
    run_table.add_column("Ignoradas", justify="right")
    run_table.add_column("Erro")
    for run in runs:
        run_table.add_row(
            str(run.id),
            run.status.value.lower(),
            _date_or_dash(run.started_at),
            _date_or_dash(run.finished_at),
            str(run.items_found),
            str(run.items_created),
            str(run.items_skipped),
            run.error_message or "-",
        )
    console.print(run_table)


def _print_source_health(session: Session) -> None:
    boards = session.scalars(select(CompanyBoard).order_by(CompanyBoard.key.asc())).all()
    active_boards = [board for board in boards if board.is_active]
    healthy = [board for board in active_boards if board.consecutive_failures == 0]
    warning = [board for board in active_boards if 0 < board.consecutive_failures < 3]
    failing = [board for board in active_boards if board.consecutive_failures >= 3]

    summary = Table(title="Saude das fontes")
    summary.add_column("Metrica")
    summary.add_column("Valor", justify="right")
    summary.add_row("Boards ativos", str(len(active_boards)))
    summary.add_row("Saudaveis", str(len(healthy)))
    summary.add_row("Com avisos", str(len(warning)))
    summary.add_row("Falhando", str(len(failing)))
    console.print(summary)

    table = Table(title="Boards persistidos")
    table.add_column("Key", no_wrap=True)
    table.add_column("Coletor")
    table.add_column("Estado")
    table.add_column("Ultima execucao")
    table.add_column("Itens encontrados", justify="right")
    table.add_column("Itens novos", justify="right")
    table.add_column("Ignorados", justify="right")
    for board in boards:
        last_run = session.get(SourceRun, board.last_run_id) if board.last_run_id else None
        table.add_row(
            board.key or "-",
            board.collector_type or "-",
            _health_label(board),
            _date_or_dash(board.last_checked_at),
            str(last_run.items_found if last_run else 0),
            str(last_run.items_created if last_run else 0),
            str(last_run.items_skipped if last_run else 0),
        )
    if boards:
        console.print(table)
    else:
        console.print("Nenhum board persistido ainda.")


def _active_postings_for_board(session: Session, board: CompanyBoard) -> int:
    value = session.scalar(_posting_count_statement(board, active_only=True))
    return int(value or 0)


def _pending_absences_for_board(session: Session, board: CompanyBoard) -> int:
    value = session.scalar(
        _posting_count_statement(board, active_only=True).where(Posting.missing_count > 0)
    )
    return int(value or 0)


def _posting_count_statement(board: CompanyBoard, *, active_only: bool) -> Select[tuple[int]]:
    statement = select(func.count(Posting.id))
    if board.collection_scope_key:
        statement = statement.where(Posting.collection_scope_key == board.collection_scope_key)
    else:
        statement = statement.where(Posting.source_id == board.source_id)
    if active_only:
        statement = statement.where(Posting.is_active.is_(True))
    return statement


def _board_state(board: BoardConfig, db_board: CompanyBoard | None) -> str:
    if not board.enabled:
        return "desativado"
    if db_board is None or db_board.last_checked_at is None:
        return "sem historico"
    return _health_label(db_board)


def _health_label(board: CompanyBoard) -> str:
    if not board.is_active:
        return "desativado"
    if board.consecutive_failures >= 3:
        return "falhando"
    if board.consecutive_failures > 0:
        return "aviso"
    if board.last_success_at is not None:
        return "saudavel"
    return "sem historico"


def _date_or_dash(value: datetime | None) -> str:
    return "-" if value is None else value.isoformat()


def _count_postings(session: Session) -> int:
    value = session.scalar(select(func.count(Posting.id)))
    return int(value or 0)


def _count_jobs_total(session: Session) -> int:
    value = session.scalar(select(func.count(Job.id)))
    return int(value or 0)


def _count_decisions(session: Session, status: EligibilityStatus) -> int:
    value = session.scalar(
        select(func.count(Decision.id)).where(Decision.eligibility_status == status)
    )
    return int(value or 0)


def _count_jobs(session: Session, status: JobStatus) -> int:
    value = session.scalar(select(func.count(Job.id)).where(Job.status == status))
    return int(value or 0)


def _employment_type_distribution(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(Job.employment_type, func.count(Job.id)).group_by(Job.employment_type)
    ).all()
    return [(employment_type.value.lower(), int(total)) for employment_type, total in rows]


def _work_model_distribution(session: Session) -> list[tuple[str, int]]:
    rows = session.execute(
        select(Job.work_model, func.count(Job.id)).group_by(Job.work_model)
    ).all()
    return [(work_model.value.lower(), int(total)) for work_model, total in rows]


def _print_distribution(title: str, rows: list[tuple[str, int]]) -> None:
    table = Table(title=title)
    table.add_column("Valor")
    table.add_column("Total", justify="right")
    for label, total in rows:
        table.add_row(label, str(total))
    console.print(table)


def _location_label(job: Job) -> str:
    if job.work_model is WorkModel.REMOTE:
        return f"Remoto ({job.remote_country_scope or 'escopo desconhecido'})"
    city = job.city or "-"
    state = job.state or "-"
    return f"{city}, {state}"


def _eligibility_label(decision: Decision | None) -> str:
    if decision is None:
        return "não avaliada"
    return decision.eligibility_status.value.lower()


def _ranking_breakdown_label(decision: Decision) -> str:
    if not decision.ranking_breakdown_json:
        return "-"
    try:
        data = json.loads(decision.ranking_breakdown_json)
    except json.JSONDecodeError:
        return decision.ranking_breakdown_json
    return ", ".join(f"{key}: {value}" for key, value in data.items())


def _doctor_checks(settings: Settings) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    checks.append(_check_python_version())
    checks.append(_check_virtualenv())
    checks.append(_check_database(settings))
    checks.append(_check_migrations(settings))
    checks.extend(_check_yaml_configs(settings))
    checks.append(_check_permissions(settings))
    checks.append(_check_git_available())
    checks.append(_check_git_status())
    checks.extend(_check_dependencies())
    return checks


def _check_python_version() -> tuple[str, str, str]:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    status = "OK" if sys.version_info >= (3, 12) else "ERRO"
    return status, "Python", version


def _check_virtualenv() -> tuple[str, str, str]:
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return "OK", "Ambiente virtual", sys.prefix
    return "AVISO", "Ambiente virtual", "não detectado"


def _check_database(settings: Settings) -> tuple[str, str, str]:
    database_path = settings.database_path
    if database_path is None:
        return "OK", "Banco", settings.database_url
    if database_path.exists():
        return "OK", "Banco", str(database_path)
    if database_path.parent.exists():
        return "AVISO", "Banco", f"arquivo ainda não existe: {database_path}"
    return "ERRO", "Banco", f"diretório não existe: {database_path.parent}"


def _check_migrations(settings: Settings) -> tuple[str, str, str]:
    database_path = settings.database_path
    if database_path is not None and not database_path.exists():
        return "AVISO", "Migrações", "banco ainda não existe"
    try:
        with session_scope(settings) as session:
            version = session.execute(text("select version_num from alembic_version")).scalar()
    except Exception as exc:
        return "AVISO", "Migrações", f"não foi possível ler alembic_version: {exc}"
    if version == "0004_collection_scope_keys":
        return "OK", "Migrações", version
    return "AVISO", "Migrações", f"versão atual: {version}"


def _check_yaml_configs(settings: Settings) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    loaders: list[tuple[str, Callable[[], object]]] = [
        ("Perfil", lambda: load_profile(settings.config_dir, settings.profile_path).path),
        ("Elegibilidade", lambda: load_eligibility_rules(settings.config_dir).rules_version),
        ("Ranking", lambda: str(load_ranking_weights(settings.config_dir).recommended_min_score)),
        ("Rede", lambda: load_network_config(settings.config_dir).http.user_agent),
        ("Boards", lambda: str(len(load_company_boards(settings.config_dir).boards))),
        (
            "Empresas bloqueadas",
            lambda: str(len(load_blocked_companies(settings.config_dir).all_companies)),
        ),
    ]
    for name, loader in loaders:
        try:
            checks.append(("OK", name, str(loader())))
        except Exception as exc:
            checks.append(("ERRO", name, str(exc)))
    return checks


def _check_permissions(settings: Settings) -> tuple[str, str, str]:
    database_path = settings.database_path
    target = database_path.parent if database_path is not None else settings.config_dir
    readable = os.access(target, os.R_OK)
    writable = os.access(target, os.W_OK)
    status = "OK" if readable and writable else "ERRO"
    return status, "Permissões", f"leitura={readable}, escrita={writable}: {target}"


def _check_git_available() -> tuple[str, str, str]:
    result = _run_subprocess(["git", "--version"])
    if result[0] == 0:
        return "OK", "Git disponível", result[1].strip()
    return "ERRO", "Git disponível", result[2].strip() or "git não encontrado"


def _check_git_status() -> tuple[str, str, str]:
    result = _run_subprocess(["git", "status", "--short"])
    if result[0] == 0:
        detail = "limpo" if not result[1].strip() else "há alterações locais"
        return "OK", "Repositório Git", detail
    return "ERRO", "Repositório Git", result[2].strip() or result[1].strip()


def _check_dependencies() -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    modules = ["sqlalchemy", "alembic", "pydantic", "yaml", "typer", "rich", "httpx", "bs4"]
    for module in modules:
        try:
            import_module(module)
            checks.append(("OK", f"Dependência {module}", "importável"))
        except Exception as exc:
            checks.append(("ERRO", f"Dependência {module}", str(exc)))
    return checks


def _run_subprocess(args: list[str]) -> tuple[int, str, str]:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout, completed.stderr
