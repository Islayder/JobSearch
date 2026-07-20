import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import Select, desc, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from radar_vagas.applications.guard import ApplicationGuard
from radar_vagas.applications.history_import import (
    ApplicationHistoryImportResult,
    import_application_history,
    validate_application_history_file,
    write_application_history_report,
)
from radar_vagas.applications.review import (
    ReviewQueueRow,
    add_application_event,
    current_review_state,
    shortlist_job,
)
from radar_vagas.applications.review import (
    dismiss_job as dismiss_job_service,
)
from radar_vagas.applications.review import (
    mark_applied as mark_applied_service,
)
from radar_vagas.applications.review import (
    mark_seen as mark_seen_service,
)
from radar_vagas.applications.review import (
    restore_job as restore_job_service,
)
from radar_vagas.applications.review import (
    review_queue as review_queue_service,
)
from radar_vagas.applications.state import rebuild_application_state
from radar_vagas.calendar.service import (
    cancel_event,
    complete_event,
    confirm_event,
    create_event,
    get_event,
    list_upcoming_events,
)
from radar_vagas.canonicalization.normalize import normalize_company_name
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
from radar_vagas.collection.search_plan import (
    SearchPlanBudget,
    SearchPlanBudgetState,
    run_search_plan,
)
from radar_vagas.collectors.registry import get_collector, list_collectors
from radar_vagas.config.loaders import (
    load_blocked_companies,
    load_company_boards,
    load_eligibility_rules,
    load_network_config,
    load_profile,
    load_ranking_weights,
    load_relevance_rules,
    load_search_queries,
)
from radar_vagas.config.schemas import BoardConfig, NetworkConfig, SearchQueryConfig
from radar_vagas.config.settings import Settings
from radar_vagas.domain.enums import (
    ApplicationEventType,
    ApplicationStatus,
    CareerEventConfirmationStatus,
    CareerEventSource,
    CareerEventType,
    CollectionAuthority,
    EligibilityStatus,
    EmploymentType,
    JobStatus,
    RelevanceStatus,
    ReviewState,
    WorkModel,
    parse_enum_value,
)
from radar_vagas.domain.errors import RadarError
from radar_vagas.eligibility.workflow import (
    ReevaluateJobsSummary,
    evaluate_all_jobs,
    evaluate_job_by_id,
    reevaluate_jobs,
)
from radar_vagas.http.client import HttpClient, HttpRequestBudget
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
    Application,
    CareerEvent,
    Company,
    CompanyBoard,
    Decision,
    DiscoveryHit,
    ImportItemAudit,
    Job,
    JobProfileComparison,
    JobRequirementMatch,
    Posting,
    ProfessionalProfileVersion,
    SearchQuery,
    Source,
    SourceRun,
)
from radar_vagas.profile.service import (
    ProfileComparisonResult,
    ProfileImportResult,
    RequirementCandidate,
    RequirementEvaluation,
    activate_profile_version,
    compare_job_to_profile,
    import_professional_profile,
    latest_comparison_for_job,
    list_profile_versions,
)
from radar_vagas.relevance.service import technologies_from_json

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


class _SearchPlanBudgetState(SearchPlanBudgetState):
    pass


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


@app.command("web")
def web_command(
    ctx: typer.Context,
    port: Annotated[int, typer.Option("--port", help="Porta local da interface.")] = 8000,
    host: Annotated[
        str,
        typer.Option("--host", help="Host local de loopback."),
    ] = "127.0.0.1",
    no_open_browser: Annotated[
        bool,
        typer.Option("--no-open-browser", help="Nao abre o navegador automaticamente."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Exibe traceback completo na interface web."),
    ] = False,
) -> None:
    """Inicia a interface web local."""

    def action() -> None:
        _apply_command_debug(ctx, debug)
        if port <= 0 or port > 65535:
            raise RadarError("--port deve ficar entre 1 e 65535.")
        try:
            import threading
            import webbrowser

            import uvicorn

            from radar_vagas.config.loaders import load_ui_config
            from radar_vagas.web.app import create_app
            from radar_vagas.web.server import WebServerLock, validate_bind_host
        except ImportError as exc:
            console.print(
                '[red]Erro:[/red] Interface web nao instalada. Execute: pip install -e ".[web]"'
            )
            raise typer.Exit(1) from exc

        settings = _settings(ctx)
        bind_host = validate_bind_host(host)
        web_app = create_app(settings, debug=debug)
        url = f"http://{bind_host}:{port}"
        ui_config = load_ui_config(settings.config_dir)
        auto_open = ui_config.auto_open_browser and not no_open_browser
        with WebServerLock(settings, port):
            if auto_open:
                threading.Timer(0.8, lambda: webbrowser.open(url)).start()
            console.print(f"Interface web local em: [bold]{url}[/bold]")
            uvicorn.run(
                web_app,
                host=bind_host,
                port=port,
                log_level="debug" if debug else "warning",
            )

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
        client = _http_client(network)
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
        network = load_network_config(settings.config_dir)
        client = _http_client(network)
        try:
            for board in boards:
                try:
                    executions.append(
                        _collect_single_board(
                            settings,
                            ResolvedBoard(config=board, persist=True),
                            dry_run=dry_run,
                            max_items=max_items_per_board,
                            since=None,
                            http_client=client,
                        )
                    )
                except Exception as exc:
                    errors.append(f"{board.key}: {exc}")
                    if not continue_on_error:
                        raise
        finally:
            client.close()
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


@app.command("queries")
def queries_command(
    ctx: typer.Context,
    collector: Annotated[
        str | None,
        typer.Option("--collector", help="Filtra por coletor."),
    ] = None,
    enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Filtra consultas ativas ou inativas."),
    ] = None,
    tag: Annotated[
        str | None,
        typer.Option("--tag", help="Filtra por tag."),
    ] = None,
) -> None:
    """Lista consultas de descoberta configuradas."""

    def action() -> None:
        settings = _settings(ctx)
        queries = load_search_queries(settings.config_dir).queries
        if collector is not None:
            queries = [query for query in queries if query.collector == collector.strip().lower()]
        if enabled is not None:
            queries = [query for query in queries if query.enabled is enabled]
        if tag is not None:
            queries = [query for query in queries if tag.lower() in query.tags]
        with session_scope(settings) as session:
            _print_queries(session, queries)

    _run(ctx, action)


@app.command("show-query")
def show_query_command(
    ctx: typer.Context,
    query_key: Annotated[str, typer.Argument(help="Key da consulta configurada.")],
) -> None:
    """Mostra configuracao segura e historico de uma consulta."""

    def action() -> None:
        settings = _settings(ctx)
        query = _query_by_key(load_search_queries(settings.config_dir).queries, query_key)
        if query is None:
            raise RadarError(f"Consulta nao encontrada: {query_key}")
        with session_scope(settings) as session:
            _print_query_detail(session, query)

    _run(ctx, action)


@app.command("collect-query")
def collect_query_command(
    ctx: typer.Context,
    query_key: Annotated[str, typer.Argument(help="Key da consulta configurada.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Coleta e valida sem alterar o banco."),
    ] = False,
    max_pages: Annotated[
        int | None,
        typer.Option("--max-pages", help="Limite positivo de paginas para esta execucao."),
    ] = None,
    max_items: Annotated[
        int | None,
        typer.Option("--max-items", help="Limite positivo de itens para esta execucao."),
    ] = None,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatorio JSON de consulta."),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Exibe traceback completo para este comando."),
    ] = False,
) -> None:
    """Executa uma consulta de descoberta configurada."""

    def action() -> None:
        _apply_command_debug(ctx, debug)
        settings = _settings(ctx)
        query = _query_by_key(load_search_queries(settings.config_dir).queries, query_key)
        if query is None:
            raise RadarError(f"Consulta nao encontrada: {query_key}")
        execution = _collect_single_query(
            settings,
            query,
            dry_run=dry_run,
            max_pages=max_pages,
            max_items=max_items,
        )
        _write_and_print_query_report(execution, query, report)

    _run(ctx, action)


@app.command("collect-search-plan")
def collect_search_plan_command(
    ctx: typer.Context,
    collector: Annotated[
        str | None,
        typer.Option("--collector", help="Filtra por coletor."),
    ] = "gupy",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Filtra consultas por tag. Pode ser usado mais de uma vez."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Coleta e valida sem alterar o banco."),
    ] = False,
    max_queries: Annotated[
        int | None,
        typer.Option("--max-queries", help="Limite positivo de consultas."),
    ] = None,
    max_pages_per_query: Annotated[
        int | None,
        typer.Option("--max-pages-per-query", help="Limite positivo de paginas por consulta."),
    ] = None,
    max_items_per_query: Annotated[
        int | None,
        typer.Option("--max-items-per-query", help="Limite positivo de itens por consulta."),
    ] = None,
    max_total_requests: Annotated[
        int | None,
        typer.Option("--max-total-requests", help="Orcamento positivo total de requests."),
    ] = None,
    max_total_items: Annotated[
        int | None,
        typer.Option("--max-total-items", help="Orcamento positivo total de itens."),
    ] = None,
    max_duration_seconds: Annotated[
        int | None,
        typer.Option("--max-duration-seconds", help="Orcamento positivo total de duracao."),
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
    """Executa um plano de consultas de descoberta."""

    def action() -> None:
        settings = _settings(ctx)
        result = run_search_plan(
            settings,
            collector=collector,
            tags=tag or [],
            dry_run=dry_run,
            max_queries=max_queries,
            max_pages_per_query=max_pages_per_query,
            max_items_per_query=max_items_per_query,
            max_total_requests=max_total_requests,
            max_total_items=max_total_items,
            max_duration_seconds=max_duration_seconds,
            continue_on_error=continue_on_error,
        )
        _print_collect_search_plan_result(result.executions, result.errors, result.budget_state)
        if report is not None:
            _write_collect_search_plan_report(
                result.executions,
                result.errors,
                report,
                result.budget_state,
            )
        if result.errors:
            raise typer.Exit(1)

    _run(ctx, action)


@app.command("query-health")
def query_health_command(ctx: typer.Context) -> None:
    """Mostra um resumo de saude das consultas de descoberta."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            _print_query_health(session)

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


@app.command("reevaluate-jobs")
def reevaluate_jobs_command(
    ctx: typer.Context,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Filtra por provider da publicacao."),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filtra por estado da vaga."),
    ] = None,
    only_missing_relevance: Annotated[
        bool,
        typer.Option(
            "--only-missing-relevance",
            help="Reavalia apenas vagas sem relevancia preenchida.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Limite positivo de vagas para reavaliar."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Simula sem alterar o banco."),
    ] = False,
    report: Annotated[
        Path | None,
        typer.Option("--report", help="Caminho para relatorio JSON."),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Exibe traceback completo para este comando."),
    ] = False,
) -> None:
    """Reavalia elegibilidade, relevancia e ranking de forma controlada."""

    def action() -> None:
        _apply_command_debug(ctx, debug)
        settings = _settings(ctx)
        parsed_status = parse_enum_value(JobStatus, status) if status is not None else None
        parsed_limit = _positive_override(limit, "limit")
        with session_scope(settings) as session:
            summary = reevaluate_jobs(
                session,
                settings,
                provider=provider,
                status=parsed_status,
                only_missing_relevance=only_missing_relevance,
                limit=parsed_limit,
                dry_run=dry_run,
            )
        if report is not None:
            _write_reevaluate_report(summary, report)
        _print_reevaluate_summary(summary)

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
    provider: Annotated[str | None, typer.Option("--provider", help="Filtra por provider.")] = None,
    relevance_status: Annotated[
        str | None,
        typer.Option("--relevance-status", help="Filtra por relevância."),
    ] = None,
    active: Annotated[
        bool | None,
        typer.Option("--active/--inactive", help="Filtra por publicação ativa ou inativa."),
    ] = None,
    source_type: Annotated[
        str | None,
        typer.Option("--source-type", help="Filtra por tipo de fonte proprietária."),
    ] = None,
    query_key: Annotated[
        str | None,
        typer.Option("--query-key", help="Filtra por consulta que encontrou a vaga."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Limite positivo de resultados.")] = 50,
    sort: Annotated[
        str,
        typer.Option("--sort", help="Ordenação: score, newest ou first-seen."),
    ] = "score",
) -> None:
    """Lista vagas com filtros de inspeção."""

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
                provider=provider,
                relevance_status=relevance_status,
                active=active,
                source_type=source_type,
                query_key=query_key,
                limit=limit,
                sort=sort,
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


@app.command("import-profile")
def import_profile_command(
    ctx: typer.Context,
    file_path: Annotated[Path, typer.Argument(help="Arquivo local YAML, JSON ou TXT.")],
    name: Annotated[
        str | None, typer.Option("--name", help="Nome publico local do perfil.")
    ] = None,
    activate: Annotated[
        bool,
        typer.Option("--activate/--no-activate", help="Define esta versao como ativa."),
    ] = True,
) -> None:
    """Importa um perfil profissional/curriculo estruturado para o banco local."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            result = import_professional_profile(
                session,
                file_path,
                profile_name=name,
                activate=activate,
            )
            session.flush()
            _print_profile_import_result(result)

    _run(ctx, action)


@app.command("profiles")
def profiles_command(ctx: typer.Context) -> None:
    """Lista versoes de perfil profissional importadas localmente."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            _print_profiles(list_profile_versions(session))

    _run(ctx, action)


@app.command("show-profile")
def show_profile_command(
    ctx: typer.Context,
    profile_version_id: Annotated[int, typer.Argument(help="ID da versao do perfil.")],
) -> None:
    """Mostra resumo estruturado de uma versao do perfil."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            version = session.get(ProfessionalProfileVersion, profile_version_id)
            if version is None:
                raise RadarError(f"Versao de perfil nao encontrada: {profile_version_id}")
            _print_profile_detail(version)

    _run(ctx, action)


@app.command("activate-profile")
def activate_profile_command(
    ctx: typer.Context,
    profile_version_id: Annotated[int, typer.Argument(help="ID da versao do perfil.")],
) -> None:
    """Ativa uma versao de perfil e desativa todas as demais."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            version = activate_profile_version(session, profile_version_id)
            console.print(f"Versao ativa do perfil: {version.id}.")

    _run(ctx, action)


@app.command("compare-profile")
def compare_profile_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
    profile_version_id: Annotated[
        int | None,
        typer.Option("--profile-version-id", help="Versao do perfil. Usa a ativa por padrao."),
    ] = None,
) -> None:
    """Compara uma vaga com o perfil ativo e grava a analise explicavel."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            result = compare_job_to_profile(
                session,
                job_id,
                profile_version_id=profile_version_id,
            )
            _print_profile_comparison(result)

    _run(ctx, action)


@app.command("show-compatibility")
def show_compatibility_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
) -> None:
    """Mostra a compatibilidade mais recente gravada para uma vaga."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            comparison = latest_comparison_for_job(session, job_id)
            if comparison is None:
                raise RadarError("Nenhuma comparacao encontrada. Execute compare-profile.")
            _print_stored_profile_comparison(comparison)

    _run(ctx, action)


@app.command("review-queue")
def review_queue_command(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option("--status", help="Filtra por JobStatus.")] = None,
    review_state: Annotated[
        str | None, typer.Option("--review-state", help="Filtra por estado de revisao.")
    ] = None,
    provider: Annotated[str | None, typer.Option("--provider", help="Filtra por provider.")] = None,
    employment_type: Annotated[
        str | None, typer.Option("--employment-type", help="Filtra por vinculo.")
    ] = None,
    work_model: Annotated[
        str | None, typer.Option("--work-model", help="Filtra por modalidade.")
    ] = None,
    relevance_status: Annotated[
        str | None, typer.Option("--relevance-status", help="Filtra por relevancia.")
    ] = None,
    min_score: Annotated[int | None, typer.Option("--min-score", help="Ranking minimo.")] = None,
    query_key: Annotated[
        str | None, typer.Option("--query-key", help="Filtra por consulta de descoberta.")
    ] = None,
    company: Annotated[str | None, typer.Option("--company", help="Filtra por empresa.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Limite positivo.")] = 50,
    sort: Annotated[
        str, typer.Option("--sort", help="Ordenacao: score, newest ou first-seen.")
    ] = "score",
) -> None:
    """Mostra a fila de revisao humana, sem imprimir descricoes integrais."""

    def action() -> None:
        settings = _settings(ctx)
        rows = _review_queue_rows(
            settings,
            status=status,
            review_state=review_state,
            provider=provider,
            employment_type=employment_type,
            work_model=work_model,
            relevance_status=relevance_status,
            min_score=min_score,
            query_key=query_key,
            company=company,
            limit=limit,
            sort=sort,
        )
        _print_review_queue(rows)

    _run(ctx, action)


@app.command("mark-seen")
def mark_seen_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
) -> None:
    """Marca uma vaga como vista sem criar candidatura."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            state = mark_seen_service(session, job_id)
            console.print(f"Vaga {job_id} marcada como {state.state.value.lower()}.")

    _run(ctx, action)


@app.command("shortlist")
def shortlist_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
) -> None:
    """Coloca uma vaga na shortlist sem criar candidatura."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            state = shortlist_job(session, job_id)
            console.print(f"Vaga {job_id} marcada como {state.state.value.lower()}.")

    _run(ctx, action)


@app.command("dismiss-job")
def dismiss_job_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
    reason: Annotated[str | None, typer.Option("--reason", help="Motivo curto.")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Notas locais opcionais.")] = None,
) -> None:
    """Descarta uma vaga com evento auditavel e sem criar candidatura."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            state = dismiss_job_service(session, job_id, reason_code=reason, notes=notes)
            console.print(f"Vaga {job_id} descartada: {state.state.value.lower()}.")

    _run(ctx, action)


@app.command("restore-job")
def restore_job_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
) -> None:
    """Restaura descarte manual e recalcula o estado pelas regras atuais."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            job = restore_job_service(session, settings, job_id)
            console.print(f"Vaga {job_id} restaurada para {job.status.value.lower()}.")

    _run(ctx, action)


@app.command("mark-applied")
def mark_applied_command(
    ctx: typer.Context,
    job_id: Annotated[int, typer.Argument(help="ID da vaga.")],
    applied_at: Annotated[
        str | None, typer.Option("--applied-at", help="Data ISO da candidatura.")
    ] = None,
    platform: Annotated[str | None, typer.Option("--platform", help="Plataforma.")] = None,
    external_reference: Annotated[
        str | None, typer.Option("--external-reference", help="Referencia externa.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Notas locais opcionais.")] = None,
    application_url: Annotated[
        str | None, typer.Option("--application-url", help="URL publica da vaga.")
    ] = None,
) -> None:
    """Registra manualmente uma candidatura sem acessar a URL."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            application = mark_applied_service(
                session,
                settings,
                job_id,
                applied_at=_parse_datetime_option(applied_at, "--applied-at"),
                platform=platform,
                external_reference=external_reference,
                notes=notes,
                application_url=application_url,
            )
            session.flush()
            console.print(f"Candidatura registrada: {application.id}.")

    _run(ctx, action)


@app.command("applications")
def applications_command(
    ctx: typer.Context,
    status: Annotated[
        str | None, typer.Option("--status", help="Filtra por status da candidatura.")
    ] = None,
    platform: Annotated[
        str | None,
        typer.Option("--platform", help="Filtra por plataforma."),
    ] = None,
    company: Annotated[str | None, typer.Option("--company", help="Filtra por empresa.")] = None,
    after: Annotated[str | None, typer.Option("--after", help="Data minima ISO.")] = None,
    before: Annotated[str | None, typer.Option("--before", help="Data maxima ISO.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Limite positivo.")] = 50,
    sort: Annotated[str, typer.Option("--sort", help="newest, oldest ou company.")] = "newest",
) -> None:
    """Lista candidaturas registradas localmente."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            rows = _query_applications(
                session,
                status=parse_enum_value(ApplicationStatus, status) if status else None,
                platform=platform,
                company=company,
                after=_parse_datetime_option(after, "--after"),
                before=_parse_datetime_option(before, "--before"),
                limit=limit,
                sort=sort,
            )
            _print_applications(rows)

    _run(ctx, action)


@app.command("show-application")
def show_application_command(
    ctx: typer.Context,
    application_id: Annotated[int, typer.Argument(help="ID da candidatura.")],
) -> None:
    """Mostra candidatura e eventos locais."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            application = session.get(Application, application_id)
            if application is None:
                raise RadarError(f"Candidatura nao encontrada: {application_id}")
            _print_application_detail(application)

    _run(ctx, action)


@app.command("application-event")
def application_event_command(
    ctx: typer.Context,
    application_id: Annotated[int, typer.Argument(help="ID da candidatura.")],
    event_type: Annotated[str, typer.Option("--type", help="Tipo do evento.")],
    occurred_at: Annotated[
        str | None, typer.Option("--occurred-at", help="Data ISO do evento.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Notas locais opcionais.")] = None,
) -> None:
    """Registra evento manual de candidatura."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            event = add_application_event(
                session,
                application_id,
                event_type=parse_enum_value(ApplicationEventType, event_type),
                occurred_at=_parse_datetime_option(occurred_at, "--occurred-at"),
                notes=notes,
            )
            session.flush()
            console.print(f"Evento registrado: {event.id}.")

    _run(ctx, action)


@app.command("rebuild-application-stage")
def rebuild_application_stage_command(
    ctx: typer.Context,
    application_id: Annotated[int, typer.Argument(help="ID da candidatura.")],
) -> None:
    """Recalcula status e etapa da candidatura pela timeline persistida."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            application = session.get(Application, application_id)
            if application is None:
                raise RadarError(f"Candidatura nao encontrada: {application_id}")
            result = rebuild_application_state(application)
            console.print(
                "Candidatura recalculada: "
                f"status={result.status.value.lower()}, "
                f"etapa={result.stage.value.lower() if result.stage else '-'}."
            )

    _run(ctx, action)


@app.command("validate-application-history")
def validate_application_history_command(
    ctx: typer.Context,
    file_path: Annotated[Path, typer.Argument(help="Arquivo JSON ou CSV.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Mantido por compatibilidade.")] = True,
    report: Annotated[Path | None, typer.Option("--report", help="Relatorio JSON.")] = None,
    delimiter: Annotated[str | None, typer.Option("--delimiter", help="Delimitador CSV.")] = None,
    allow_probable_matches: Annotated[
        bool,
        typer.Option("--allow-probable-matches", help="Aceita provaveis no import."),
    ] = False,
) -> None:
    """Valida arquivo de historico sem escrever no banco."""

    def action() -> None:
        _ = ctx
        _ = dry_run
        result = validate_application_history_file(
            file_path,
            delimiter=delimiter,
            allow_probable_matches=allow_probable_matches,
        )
        if report is not None:
            write_application_history_report(result, report)
        _print_application_history_result(result)

    _run(ctx, action)


@app.command("import-application-history")
def import_application_history_command(
    ctx: typer.Context,
    file_path: Annotated[Path, typer.Argument(help="Arquivo JSON ou CSV.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simula sem escrever.")] = False,
    report: Annotated[Path | None, typer.Option("--report", help="Relatorio JSON.")] = None,
    delimiter: Annotated[str | None, typer.Option("--delimiter", help="Delimitador CSV.")] = None,
    allow_probable_matches: Annotated[
        bool,
        typer.Option("--allow-probable-matches", help="Permite link provavel."),
    ] = False,
) -> None:
    """Importa historico local sem inventar vagas ou publicacoes."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            result = import_application_history(
                session,
                settings,
                file_path,
                dry_run=dry_run,
                delimiter=delimiter,
                allow_probable_matches=allow_probable_matches,
            )
        if report is not None:
            write_application_history_report(result, report)
        _print_application_history_result(result)

    _run(ctx, action)


@app.command("agenda")
def agenda_command(
    ctx: typer.Context,
    days: Annotated[int, typer.Option("--days", help="Janela futura em dias.")] = 30,
    event_type: Annotated[str | None, typer.Option("--type", help="Tipo do evento.")] = None,
) -> None:
    """Lista proximos eventos locais da agenda."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            events = list_upcoming_events(
                session,
                days=days,
                event_type=parse_enum_value(CareerEventType, event_type) if event_type else None,
            )
            _print_agenda_events(events)

    _run(ctx, action)


@app.command("add-agenda-event")
def add_agenda_event_command(
    ctx: typer.Context,
    event_type: Annotated[str, typer.Option("--type", help="Tipo do evento.")],
    title: Annotated[str, typer.Option("--title", help="Titulo do evento.")],
    job_id: Annotated[int | None, typer.Option("--job-id", help="ID da vaga.")] = None,
    application_id: Annotated[
        int | None, typer.Option("--application-id", help="ID da candidatura.")
    ] = None,
    event_key: Annotated[
        str | None, typer.Option("--event-key", help="Identidade idempotente opcional.")
    ] = None,
    starts_at: Annotated[
        str | None, typer.Option("--starts-at", help="Inicio ISO com timezone.")
    ] = None,
    ends_at: Annotated[str | None, typer.Option("--ends-at", help="Fim ISO com timezone.")] = None,
    all_day: Annotated[bool, typer.Option("--all-day", help="Evento de dia inteiro.")] = False,
    timezone: Annotated[str, typer.Option("--timezone", help="Timezone original.")] = "UTC",
    source: Annotated[str, typer.Option("--source", help="Origem do evento.")] = "manual",
    confidence: Annotated[
        float | None, typer.Option("--confidence", help="Confianca entre 0 e 1.")
    ] = None,
    confirmation_status: Annotated[
        str | None,
        typer.Option("--confirmation-status", help="Estado de confirmacao."),
    ] = None,
    location: Annotated[str | None, typer.Option("--location", help="Local.")] = None,
    meeting_url: Annotated[
        str | None, typer.Option("--meeting-url", help="URL http/https segura.")
    ] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Notas locais.")] = None,
) -> None:
    """Cria evento local de agenda sem integrar calendario externo."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            event = create_event(
                session,
                event_type=parse_enum_value(CareerEventType, event_type),
                title=title,
                job_id=job_id,
                application_id=application_id,
                event_key=event_key,
                starts_at=_parse_datetime_option(starts_at, "--starts-at"),
                ends_at=_parse_datetime_option(ends_at, "--ends-at"),
                all_day=all_day,
                timezone=timezone,
                source=parse_enum_value(CareerEventSource, source),
                confidence=confidence,
                confirmation_status=(
                    parse_enum_value(CareerEventConfirmationStatus, confirmation_status)
                    if confirmation_status
                    else None
                ),
                location=location,
                meeting_url=meeting_url,
                notes=notes,
            )
            session.flush()
            console.print(f"Evento de agenda registrado: {event.id}.")

    _run(ctx, action)


@app.command("show-agenda-event")
def show_agenda_event_command(
    ctx: typer.Context,
    event_id: Annotated[int, typer.Argument(help="ID do evento.")],
) -> None:
    """Mostra um evento local de agenda."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            _print_agenda_event_detail(get_event(session, event_id))

    _run(ctx, action)


@app.command("confirm-agenda-event")
def confirm_agenda_event_command(
    ctx: typer.Context,
    event_id: Annotated[int, typer.Argument(help="ID do evento.")],
) -> None:
    """Confirma evento sugerido da agenda local."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            event = confirm_event(session, event_id)
            console.print(f"Evento confirmado: {event.id}.")

    _run(ctx, action)


@app.command("complete-agenda-event")
def complete_agenda_event_command(
    ctx: typer.Context,
    event_id: Annotated[int, typer.Argument(help="ID do evento.")],
) -> None:
    """Marca evento de agenda como concluido."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            event = complete_event(session, event_id)
            console.print(f"Evento concluido: {event.id}.")

    _run(ctx, action)


@app.command("cancel-agenda-event")
def cancel_agenda_event_command(
    ctx: typer.Context,
    event_id: Annotated[int, typer.Argument(help="ID do evento.")],
) -> None:
    """Cancela evento de agenda sem excluir historico."""

    def action() -> None:
        settings = _settings(ctx)
        with session_scope(settings) as session:
            event = cancel_event(session, event_id)
            console.print(f"Evento cancelado: {event.id}.")

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
        table.add_row("Regras de elegibilidade", rules.rules_version)
        table.add_row("Regras de relevância", load_relevance_rules(settings.config_dir).version)
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


def _http_client(
    network: NetworkConfig,
    *,
    request_budget: HttpRequestBudget | None = None,
) -> HttpClient:
    try:
        return HttpClient(
            network.http,
            minimum_interval_between_requests_seconds=(
                network.collection.minimum_interval_between_requests_seconds
            ),
            request_budget=request_budget,
        )
    except TypeError as exc:
        if "minimum_interval_between_requests_seconds" not in str(
            exc
        ) and "request_budget" not in str(exc):
            raise
        return HttpClient(network.http)


def _search_plan_budget(
    network: NetworkConfig,
    *,
    max_total_requests: int | None,
    max_total_items: int | None,
    max_duration_seconds: int | None,
) -> SearchPlanBudget:
    return SearchPlanBudget(
        max_total_requests=(
            _positive_override(max_total_requests, "max-total-requests")
            or network.search_plan.max_total_requests
        ),
        max_total_items=(
            _positive_override(max_total_items, "max-total-items")
            or network.search_plan.max_total_items
        ),
        max_duration_seconds=(
            _positive_override(max_duration_seconds, "max-duration-seconds")
            or network.search_plan.max_duration_seconds
        ),
    )


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
    http_client: HttpClient | None = None,
) -> CollectionExecutionReport:
    board = resolved.config
    effective_max_items = _positive_override(max_items, "max-items")
    network = load_network_config(settings.config_dir)
    client = http_client or _http_client(network)
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
        max_items=effective_max_items,
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
        if http_client is None:
            client.close()
    with session_scope(settings) as session:
        return run_collection_persistence(
            session,
            settings,
            context,
            result,
            board_config=board if resolved.persist else None,
        )


def _collect_single_query(
    settings: Settings,
    query: SearchQueryConfig,
    *,
    dry_run: bool,
    max_pages: int | None,
    max_items: int | None,
    http_client: HttpClient | None = None,
) -> CollectionExecutionReport:
    if not query.enabled:
        raise RadarError(f"Consulta desativada: {query.key}")
    effective_max_pages = _positive_override(max_pages, "max-pages") or query.max_pages
    effective_max_items = _positive_override(max_items, "max-items") or query.max_items
    network = load_network_config(settings.config_dir)
    client = http_client or _http_client(network)
    context = build_collection_context(
        collector=query.collector,
        company_name=None,
        dry_run=dry_run,
        max_items=effective_max_items,
        max_pages=effective_max_pages,
        authority=CollectionAuthority.DISCOVERY_QUERY,
        query_key=query.key,
        query_mode=query.mode,
        query_parameters={
            "search_text": query.search_text,
            "filters": query.filters,
            "hydrate_details": query.hydrate_details,
        },
    )
    context = replace(
        context,
        source_name=f"Gupy query {query.key}",
        source_type=query.collector,
        collection_scope_key=query.collection_scope_key,
        http_client=client,
        collection_config=network.collection,
    )
    try:
        result = get_collector(query.collector).collect(context)
    except Exception as exc:
        with session_scope(settings) as session:
            record_failed_collection(
                session,
                context,
                exc,
                search_query_config=query,
                settings=settings,
            )
        raise
    finally:
        if http_client is None:
            client.close()
    with session_scope(settings) as session:
        return run_collection_persistence(
            session,
            settings,
            context,
            result,
            search_query_config=query,
        )


def _positive_override(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if value <= 0:
        raise RadarError(f"--{label} deve ser um inteiro positivo.")
    return value


def _parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise RadarError(f"Data invalida em --since: {value}") from exc


def _parse_datetime_option(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise RadarError(f"Data invalida em {label}: {value}") from exc


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
        "core": "Relevancia core",
        "adjacent": "Relevancia adjacente",
        "unrelated": "Relevancia fora do alvo",
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


def _query_by_key(
    queries: list[SearchQueryConfig],
    query_key: str,
) -> SearchQueryConfig | None:
    for query in queries:
        if query.key == query_key:
            return query
    return None


def _filtered_queries(
    settings: Settings,
    *,
    collector: str | None,
    tags: list[str],
    max_queries: int | None,
) -> list[SearchQueryConfig]:
    limit = _positive_override(max_queries, "max-queries")
    queries = load_search_queries(settings.config_dir).enabled_queries()
    if collector is not None:
        queries = [query for query in queries if query.collector == collector.strip().lower()]
    normalized_tags = {tag.strip().lower() for tag in tags if tag.strip()}
    if normalized_tags:
        queries = [query for query in queries if normalized_tags.issubset(set(query.tags))]
    queries = sorted(queries, key=lambda query: (query.priority, query.key))
    return queries[:limit] if limit is not None else queries


def _write_and_print_query_report(
    execution: CollectionExecutionReport,
    query: SearchQueryConfig,
    report_path: Path | None,
) -> None:
    if report_path is not None:
        payload = _query_report_payload(query, execution)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    _print_collection_execution(execution)


def _query_report_payload(
    query: SearchQueryConfig,
    execution: CollectionExecutionReport,
) -> dict[str, object]:
    summary = execution.summary
    metadata = execution.metadata
    return {
        "query_key": query.key,
        "collector": query.collector,
        "mode": query.mode,
        "authority": CollectionAuthority.DISCOVERY_QUERY.value.lower(),
        "started_at": execution.started_at.isoformat(),
        "finished_at": execution.finished_at.isoformat(),
        "dry_run": execution.dry_run,
        "query": {
            "search_text": query.search_text,
            "filters": query.filters,
            "fingerprint": query.configuration_fingerprint,
            "collection_scope_key": query.collection_scope_key,
        },
        "network": {
            "requests": execution.network.get("requests", 0),
            "bytes_received": execution.network.get("bytes_received", 0),
            "retries": execution.network.get("retries", 0),
            "pages": int(metadata.get("pages", 0) or 0),
        },
        "summary": {
            "raw_results": int(metadata.get("raw_results", summary.found) or 0),
            "processed": summary.found,
            "invalid": summary.invalid_items,
            "new": summary.new,
            "known": summary.unchanged,
            "changed": summary.changed,
            "exact_duplicates": summary.exact_duplicates,
            "probable_duplicates": summary.probable_duplicates,
            "core": summary.core,
            "adjacent": summary.adjacent,
            "manual_review": summary.manual_review,
            "unrelated": summary.unrelated,
            "eligible": summary.eligible,
            "ineligible": summary.ineligible,
        },
        "partial": bool(metadata.get("partial", False)),
        "truncated": bool(metadata.get("truncated", False)),
        "warnings": execution.warnings,
        "errors": execution.errors,
        "public_interface": {
            "host": metadata.get("host"),
            "path": metadata.get("path"),
            "type": metadata.get("public_interface"),
        },
    }


def _print_collect_search_plan_result(
    executions: list[tuple[SearchQueryConfig, CollectionExecutionReport]],
    errors: list[str],
    budget_state: SearchPlanBudgetState,
) -> None:
    table = Table(title="Plano de consultas")
    table.add_column("Query", no_wrap=True)
    table.add_column("Coletor")
    table.add_column("Encontradas", justify="right")
    table.add_column("Novas", justify="right")
    table.add_column("Conhecidas", justify="right")
    table.add_column("Incompativeis", justify="right")
    for query, execution in executions:
        summary = execution.summary
        table.add_row(
            query.key,
            query.collector,
            str(summary.found),
            str(summary.new),
            str(summary.unchanged),
            str(summary.ineligible),
        )
    console.print(table)
    budget = Table(title="Orcamento do plano")
    budget.add_column("Metrica")
    budget.add_column("Valor", justify="right")
    budget.add_row(
        "Requests usados",
        f"{budget_state.requests_used}/{budget_state.budget.max_total_requests}",
    )
    budget.add_row(
        "Itens usados",
        f"{budget_state.items_used}/{budget_state.budget.max_total_items}",
    )
    budget.add_row(
        "Duracao usada",
        f"{int(budget_state.elapsed_seconds)}/{budget_state.budget.max_duration_seconds}s",
    )
    budget.add_row("Encerrado por", budget_state.exhausted_by or "-")
    console.print(budget)
    for error in errors:
        console.print(f"[red]Falha:[/red] {error}")


def _write_collect_search_plan_report(
    executions: list[tuple[SearchQueryConfig, CollectionExecutionReport]],
    errors: list[str],
    report_path: Path,
    budget_state: SearchPlanBudgetState,
) -> None:
    payload = {
        "queries": [_query_report_payload(query, execution) for query, execution in executions],
        "errors": errors,
        "budget": {
            "max_total_requests": budget_state.budget.max_total_requests,
            "max_total_items": budget_state.budget.max_total_items,
            "max_duration_seconds": budget_state.budget.max_duration_seconds,
            "requests_used": budget_state.requests_used,
            "items_used": budget_state.items_used,
            "elapsed_seconds": round(budget_state.elapsed_seconds, 3),
            "partial": budget_state.exhausted,
            "truncated": budget_state.exhausted,
            "limited_by": budget_state.exhausted_by,
        },
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


def _print_reevaluate_summary(summary: ReevaluateJobsSummary) -> None:
    title = "Simulacao de reavaliacao" if summary.dry_run else "Reavaliacao concluida"
    table = Table(title=title)
    table.add_column("Metrica")
    table.add_column("Valor", justify="right")
    table.add_row("Vagas avaliadas", str(summary.total))
    table.add_row("Decisoes alteradas", str(summary.changed))
    table.add_row("Sem mudanca", str(summary.unchanged))
    console.print(table)


def _write_reevaluate_report(summary: ReevaluateJobsSummary, report_path: Path) -> None:
    payload = {
        "dry_run": summary.dry_run,
        "summary": {
            "total": summary.total,
            "changed": summary.changed,
            "unchanged": summary.unchanged,
        },
        "changes": [
            {"job_id": change.job_id, "before": change.before, "after": change.after}
            for change in summary.changes
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _query_jobs(
    session: Session,
    *,
    status: str | None,
    employment_type: str | None,
    work_model: str | None,
    city: str | None,
    min_score: int | None,
    provider: str | None,
    relevance_status: str | None,
    active: bool | None,
    source_type: str | None,
    query_key: str | None,
    limit: int,
    sort: str,
) -> list[tuple[Job, Company, Decision | None]]:
    if limit <= 0:
        raise RadarError("--limit deve ser um inteiro positivo.")
    if sort not in {"score", "newest", "first-seen"}:
        raise RadarError("--sort deve ser score, newest ou first-seen.")
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
    if provider is not None:
        statement = statement.where(Job.postings.any(Posting.provider == provider.strip().lower()))
    if relevance_status is not None:
        statement = statement.where(
            Decision.relevance_status == parse_enum_value(RelevanceStatus, relevance_status)
        )
    if active is not None:
        statement = statement.where(Job.postings.any(Posting.is_active.is_(active)))
    if source_type is not None:
        statement = statement.where(
            Job.postings.any(Posting.source.has(Source.source_type == source_type.strip().lower()))
        )
    if query_key is not None:
        statement = statement.where(
            Job.postings.any(
                Posting.discovery_hits.any(
                    DiscoveryHit.search_query.has(SearchQuery.key == query_key)
                )
            )
        )
    first_seen = (
        select(func.min(Posting.first_seen_at)).where(Posting.job_id == Job.id).scalar_subquery()
    )
    if sort == "score":
        statement = statement.order_by(Decision.ranking_score.desc(), Job.id.asc())
    elif sort == "newest":
        statement = statement.order_by(desc(Job.published_at), Job.id.desc())
    else:
        statement = statement.order_by(desc(first_seen), Job.id.asc())
    statement = statement.limit(limit)
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


def _review_queue_rows(
    settings: Settings,
    *,
    status: str | None,
    review_state: str | None,
    provider: str | None,
    employment_type: str | None,
    work_model: str | None,
    relevance_status: str | None,
    min_score: int | None,
    query_key: str | None,
    company: str | None,
    limit: int,
    sort: str,
) -> list[ReviewQueueRow]:
    with session_scope(settings) as session:
        return review_queue_service(
            session,
            status=parse_enum_value(JobStatus, status) if status else None,
            review_state=parse_enum_value(ReviewState, review_state) if review_state else None,
            provider=provider,
            employment_type=(
                parse_enum_value(EmploymentType, employment_type) if employment_type else None
            ),
            work_model=parse_enum_value(WorkModel, work_model) if work_model else None,
            relevance_status=(
                parse_enum_value(RelevanceStatus, relevance_status) if relevance_status else None
            ),
            min_score=min_score,
            query_key=query_key,
            company=company,
            limit=limit,
            sort=sort,
        )


def _print_review_queue(rows: list[ReviewQueueRow]) -> None:
    table = Table(title="Fila de revisão")
    table.add_column("ID", justify="right")
    table.add_column("Empresa")
    table.add_column("Título")
    table.add_column("Vínculo")
    table.add_column("Modalidade")
    table.add_column("Localização")
    table.add_column("Relevância")
    table.add_column("Ranking", justify="right")
    table.add_column("Revisão")
    table.add_column("JobStatus")
    table.add_column("Aplicação")
    table.add_column("Queries", justify="right")
    table.add_column("URL")
    for row in rows:
        decision = row.decision
        table.add_row(
            str(row.job.id),
            row.company.canonical_name,
            row.job.canonical_title,
            row.job.employment_type.value.lower(),
            row.job.work_model.value.lower(),
            _location_label(row.job),
            (
                decision.relevance_status.value.lower()
                if decision and decision.relevance_status
                else "-"
            ),
            (
                "-"
                if decision is None or decision.ranking_score is None
                else str(decision.ranking_score)
            ),
            row.review_state.value.lower(),
            row.job.status.value.lower(),
            "-" if row.application is None else str(row.application.id),
            str(row.query_count),
            row.job.application_url or "-",
        )
    if rows:
        console.print(table)
    else:
        console.print("Nenhuma vaga encontrada na fila de revisão.")


def _query_applications(
    session: Session,
    *,
    status: ApplicationStatus | None,
    platform: str | None,
    company: str | None,
    after: datetime | None,
    before: datetime | None,
    limit: int,
    sort: str,
) -> list[Application]:
    if limit <= 0:
        raise RadarError("--limit deve ser um inteiro positivo.")
    if sort not in {"newest", "oldest", "company"}:
        raise RadarError("--sort deve ser newest, oldest ou company.")
    statement = select(Application).join(Job).join(Company)
    if status is not None:
        statement = statement.where(Application.status == status)
    if platform is not None:
        statement = statement.where(Application.platform == platform.strip().lower())
    if company is not None:
        statement = statement.where(
            Company.normalized_name.contains(normalize_company_name(company))
        )
    if after is not None:
        statement = statement.where(Application.applied_at >= after)
    if before is not None:
        statement = statement.where(Application.applied_at <= before)
    if sort == "newest":
        statement = statement.order_by(desc(Application.applied_at), desc(Application.created_at))
    elif sort == "oldest":
        statement = statement.order_by(Application.applied_at.asc(), Application.created_at.asc())
    else:
        statement = statement.order_by(Company.canonical_name.asc(), Application.id.asc())
    return list(session.scalars(statement.limit(limit)).all())


def _print_applications(applications: list[Application]) -> None:
    table = Table(title="Candidaturas")
    table.add_column("ID", justify="right")
    table.add_column("Vaga", justify="right")
    table.add_column("Empresa")
    table.add_column("Título")
    table.add_column("Status")
    table.add_column("Etapa")
    table.add_column("Plataforma")
    table.add_column("Aplicada em")
    table.add_column("Referência")
    for application in applications:
        table.add_row(
            str(application.id),
            str(application.job_id),
            application.job.company.canonical_name,
            application.job.canonical_title,
            application.status.value.lower(),
            application.stage.value.lower() if application.stage else "-",
            application.platform or "-",
            _date_or_dash(application.applied_at),
            application.external_reference or "-",
        )
    if applications:
        console.print(table)
    else:
        console.print("Nenhuma candidatura registrada.")


def _print_profile_import_result(result: ProfileImportResult) -> None:
    table = Table(title="Perfil profissional importado")
    table.add_column("Campo")
    table.add_column("Valor")
    table.add_row("Perfil", result.profile_name)
    table.add_row("ID do perfil", str(result.profile_id))
    table.add_row("Versao", str(result.version_number))
    table.add_row("ID da versao", str(result.profile_version_id))
    table.add_row("Nova versao", "sim" if result.created_version else "nao")
    table.add_row("Hash", result.content_hash)
    table.add_row("Origem local", str(result.source_path))
    console.print(table)


def _print_profiles(versions: list[ProfessionalProfileVersion]) -> None:
    table = Table(title="Perfis profissionais")
    table.add_column("Versao ID", justify="right")
    table.add_column("Perfil")
    table.add_column("Versao", justify="right")
    table.add_column("Ativa")
    table.add_column("Resumo")
    table.add_column("Origem")
    for version in versions:
        table.add_row(
            str(version.id),
            version.profile.name,
            str(version.version_number),
            "sim" if version.is_active else "nao",
            version.headline or version.summary or "-",
            version.source_path or "-",
        )
    if versions:
        console.print(table)
    else:
        console.print("Nenhum perfil profissional importado.")


def _print_profile_detail(version: ProfessionalProfileVersion) -> None:
    details = Table(title="Perfil profissional")
    details.add_column("Campo")
    details.add_column("Valor")
    details.add_row("ID da versao", str(version.id))
    details.add_row("Perfil", version.profile.name)
    details.add_row("Versao", str(version.version_number))
    details.add_row("Ativa", "sim" if version.is_active else "nao")
    details.add_row("Headline", version.headline or "-")
    details.add_row("Resumo", version.summary or "-")
    details.add_row("Habilidades", str(len(version.skills)))
    details.add_row("Evidencias", str(len(version.evidences)))
    details.add_row("Experiencias", str(len(version.experiences)))
    details.add_row("Projetos", str(len(version.projects)))
    details.add_row("Formacao", str(len(version.education)))
    details.add_row("Idiomas", str(len(version.languages)))
    console.print(details)

    skills = Table(title="Habilidades")
    skills.add_column("Nome")
    skills.add_column("Categoria")
    skills.add_column("Nivel")
    skills.add_column("Evidencias", justify="right")
    for skill in version.skills:
        skills.add_row(
            skill.name,
            skill.category or "-",
            skill.level or "-",
            str(len(skill.evidences)),
        )
    if version.skills:
        console.print(skills)


def _print_profile_comparison(result: ProfileComparisonResult) -> None:
    details = Table(title="Compatibilidade")
    details.add_column("Campo")
    details.add_column("Valor")
    details.add_row("Vaga", str(result.job_id))
    details.add_row("Versao do perfil", str(result.profile_version_id))
    details.add_row("Score", f"{result.overall_score}/100")
    details.add_row("Resumo", result.summary)
    if result.attention_points:
        details.add_row("Pontos de atencao", "; ".join(result.attention_points))
    console.print(details)

    requirements = Table(title="Requisitos")
    requirements.add_column("Tipo")
    requirements.add_column("Status")
    requirements.add_column("Requisito")
    requirements.add_column("Evidencias", justify="right")
    requirements.add_column("Explicacao")
    for item in result.requirements:
        requirements.add_row(
            item.requirement.kind.value.lower(),
            item.status.value.lower(),
            item.requirement.text,
            str(len(item.evidence)),
            item.explanation,
        )
    console.print(requirements)


def _print_stored_profile_comparison(comparison: JobProfileComparison) -> None:
    result = ProfileComparisonResult(
        comparison_id=comparison.id,
        job_id=comparison.job_id,
        profile_version_id=comparison.profile_version_id,
        overall_score=comparison.overall_score,
        summary=comparison.summary,
        attention_points=[str(item) for item in _json_list(comparison.attention_points_json)],
        requirements=[
            _stored_requirement_to_result(item) for item in comparison.requirement_matches
        ],
    )
    _print_profile_comparison(result)


def _stored_requirement_to_result(item: JobRequirementMatch) -> RequirementEvaluation:
    return RequirementEvaluation(
        requirement=RequirementCandidate(
            text=item.requirement_text,
            kind=item.requirement_kind,
        ),
        status=item.match_status,
        evidence=_json_dict_list(item.evidence_json),
        explanation=item.explanation,
        weight=item.weight,
    )


def _json_list(value: str) -> list[Any]:
    decoded = json.loads(value)
    return decoded if isinstance(decoded, list) else []


def _json_dict_list(value: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in _json_list(value):
        if isinstance(item, dict):
            result.append({str(key): str(nested) for key, nested in item.items()})
    return result


def _print_application_detail(application: Application) -> None:
    details = Table(title="Candidatura")
    details.add_column("Campo")
    details.add_column("Valor")
    details.add_row("ID", str(application.id))
    details.add_row("Vaga", str(application.job_id))
    details.add_row("Empresa", application.job.company.canonical_name)
    details.add_row("Título", application.job.canonical_title)
    details.add_row("Status", application.status.value.lower())
    details.add_row("Etapa", application.stage.value.lower() if application.stage else "-")
    details.add_row("Plataforma", application.platform or "-")
    details.add_row("Aplicada em", _date_or_dash(application.applied_at))
    details.add_row("Referência", application.external_reference or "-")
    details.add_row(
        "URL pública",
        application.application_url or application.job.application_url or "-",
    )
    console.print(details)

    events = Table(title="Eventos")
    events.add_column("ID", justify="right")
    events.add_column("Tipo")
    events.add_column("Quando")
    events.add_column("Fonte")
    events.add_column("Notas")
    for event in sorted(application.events, key=lambda value: value.occurred_at):
        events.add_row(
            str(event.id),
            event.event_type.value.lower(),
            _date_or_dash(event.occurred_at),
            event.source,
            event.notes or "-",
        )
    if not application.events:
        events.add_row("-", "-", "-", "-", "-")
    console.print(events)


def _print_application_history_result(result: ApplicationHistoryImportResult) -> None:
    table = Table(title="Histórico de candidaturas")
    table.add_column("Métrica")
    table.add_column("Valor", justify="right")
    table.add_row("Dry-run", "sim" if result.dry_run else "não")
    table.add_row("Total", str(result.total))
    table.add_row("Válidos", str(result.valid))
    table.add_row("Inválidos", str(result.invalid))
    table.add_row("Ligados", str(result.linked))
    table.add_row("Prováveis", str(result.probable))
    table.add_row("Sem match", str(result.unmatched))
    table.add_row("Conflitos", str(result.conflicts))
    table.add_row("Candidaturas criadas", str(result.created_applications))
    table.add_row("Candidaturas atualizadas", str(result.updated_applications))
    table.add_row("Itens inalterados", str(result.unchanged))
    table.add_row("Precisam revisao", str(result.needs_review))
    table.add_row("Matches criados", str(result.created_matches))
    console.print(table)


def _print_agenda_events(events: list[CareerEvent]) -> None:
    table = Table(title="Agenda local")
    table.add_column("ID", justify="right")
    table.add_column("Tipo")
    table.add_column("Titulo")
    table.add_column("Inicio")
    table.add_column("Fim")
    table.add_column("Status")
    table.add_column("Origem")
    table.add_column("Vaga", justify="right")
    table.add_column("Candidatura", justify="right")
    for event in events:
        table.add_row(
            str(event.id),
            event.event_type.value.lower(),
            event.title,
            _date_or_dash(event.starts_at),
            _date_or_dash(event.ends_at),
            event.confirmation_status.value.lower(),
            event.source.value.lower(),
            "-" if event.job_id is None else str(event.job_id),
            "-" if event.application_id is None else str(event.application_id),
        )
    if events:
        console.print(table)
    else:
        console.print("Nenhum evento encontrado na agenda local.")


def _print_agenda_event_detail(event: CareerEvent) -> None:
    details = Table(title="Evento de agenda")
    details.add_column("Campo")
    details.add_column("Valor")
    details.add_row("ID", str(event.id))
    details.add_row("Tipo", event.event_type.value.lower())
    details.add_row("Titulo", event.title)
    details.add_row("Inicio", _date_or_dash(event.starts_at))
    details.add_row("Fim", _date_or_dash(event.ends_at))
    details.add_row("Dia inteiro", "sim" if event.all_day else "nao")
    details.add_row("Timezone original", event.timezone)
    details.add_row("Origem", event.source.value.lower())
    details.add_row("Confianca", "-" if event.confidence is None else str(event.confidence))
    details.add_row("Confirmacao", event.confirmation_status.value.lower())
    details.add_row("Vaga", "-" if event.job_id is None else str(event.job_id))
    details.add_row(
        "Candidatura",
        "-" if event.application_id is None else str(event.application_id),
    )
    details.add_row("Local", event.location or "-")
    details.add_row("URL reuniao", event.meeting_url or "-")
    details.add_row("Notas", event.notes or "-")
    details.add_row("Concluido em", _date_or_dash(event.completed_at))
    details.add_row("Cancelado em", _date_or_dash(event.cancelled_at))
    console.print(details)

    audits = Table(title="Auditoria")
    audits.add_column("ID", justify="right")
    audits.add_column("Acao")
    audits.add_column("Quando")
    audits.add_column("Fonte")
    for audit in event.audits:
        audits.add_row(
            str(audit.id),
            audit.action,
            _date_or_dash(audit.occurred_at),
            audit.source,
        )
    if event.audits:
        console.print(audits)


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
    details.add_row("Departamento", job.department or "-")
    details.add_row("Área", job.area or "-")
    details.add_row("Tecnologias", ", ".join(_job_technologies(job)) or "-")
    details.add_row("Revisão", current_review_state(job).value.lower())
    guard = ApplicationGuard().evaluate(job)
    details.add_row("Guarda candidatura", guard.decision.value.lower())
    latest_profile_comparison = _latest_profile_comparison_label(job)
    if latest_profile_comparison is not None:
        details.add_row("Compatibilidade", latest_profile_comparison)
    details.add_row("Curso", job.course_requirement or "-")
    console.print(Panel(details, title="Vaga canônica"))

    postings = Table(title="Publicações associadas")
    postings.add_column("ID", justify="right")
    postings.add_column("Fonte")
    postings.add_column("Provider")
    postings.add_column("Identity")
    postings.add_column("Scope")
    postings.add_column("Ativa")
    postings.add_column("Missing", justify="right")
    postings.add_column("First seen")
    postings.add_column("Last seen")
    postings.add_column("URL")
    postings.add_column("Status")
    for posting in job.postings:
        postings.add_row(
            str(posting.id),
            posting.source.name,
            posting.provider or "-",
            posting.provider_identity_key or "-",
            posting.collection_scope_key or "-",
            "sim" if posting.is_active else "não",
            str(posting.missing_count),
            _date_or_dash(posting.first_seen_at),
            _date_or_dash(posting.last_seen_at),
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
            "Relevância",
            decision.relevance_status.value.lower()
            if decision.relevance_status is not None
            else "-",
        )
        decision_table.add_row(
            "Score relevância",
            "-" if decision.relevance_score is None else str(decision.relevance_score),
        )
        decision_table.add_row("Resumo relevância", _relevance_summary_label(decision))
        decision_table.add_row(
            "Nota", "-" if decision.ranking_score is None else str(decision.ranking_score)
        )
        decision_table.add_row("Ranking", _ranking_breakdown_label(decision))
        decision_table.add_row("Regras de elegibilidade", decision.rules_version)
        decision_table.add_row("Regras de relevância", decision.relevance_rules_version or "-")
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

    hits = Table(title="Consultas que encontraram a vaga")
    hits.add_column("Query")
    hits.add_column("Último hit")
    hits.add_column("Match")
    hits.add_column("Página", justify="right")
    hits.add_column("Posição", justify="right")
    latest_hits = _latest_hits_by_query(job)
    if latest_hits:
        for hit in latest_hits:
            hits.add_row(
                hit.search_query.key,
                _date_or_dash(hit.observed_at),
                hit.match_status,
                "-" if hit.page_number is None else str(hit.page_number),
                "-" if hit.position_in_results is None else str(hit.position_in_results),
            )
    else:
        hits.add_row("-", "-", "-", "-", "-")
    console.print(hits)


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


def _print_queries(session: Session, queries: list[SearchQueryConfig]) -> None:
    table = Table(title="Consultas")
    table.add_column("Key", no_wrap=True)
    table.add_column("Coletor")
    table.add_column("Modo")
    table.add_column("Ativa")
    table.add_column("Prioridade", justify="right")
    table.add_column("Ultimo sucesso")
    table.add_column("Falhas", justify="right")
    table.add_column("Hits", justify="right")
    table.add_column("Estado")
    for query in sorted(queries, key=lambda item: (item.priority, item.key)):
        db_query = session.scalar(select(SearchQuery).where(SearchQuery.key == query.key))
        hits = _hits_for_query(session, db_query) if db_query is not None else 0
        table.add_row(
            query.key,
            query.collector,
            query.mode,
            "sim" if query.enabled else "nao",
            str(query.priority),
            _date_or_dash(db_query.last_success_at if db_query is not None else None),
            str(db_query.consecutive_failures if db_query is not None else 0),
            str(hits),
            _query_state(query, db_query),
        )
    if queries:
        console.print(table)
    else:
        console.print("Nenhuma consulta encontrada para os filtros informados.")


def _print_query_detail(session: Session, query: SearchQueryConfig) -> None:
    db_query = session.scalar(select(SearchQuery).where(SearchQuery.key == query.key))
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Key", query.key)
    details.add_row("Coletor", query.collector)
    details.add_row("Modo", query.mode)
    details.add_row("Texto", query.search_text)
    details.add_row("Filtros", json.dumps(query.filters, ensure_ascii=False, sort_keys=True))
    details.add_row("Fingerprint", query.configuration_fingerprint)
    details.add_row("Scope", query.collection_scope_key)
    details.add_row("Prioridade", str(query.priority))
    details.add_row("Tags", ", ".join(query.tags) or "-")
    details.add_row("Ativa", "sim" if query.enabled else "nao")
    if db_query is not None:
        details.add_row("Ultima execucao", _date_or_dash(db_query.last_checked_at))
        details.add_row("Ultimo sucesso", _date_or_dash(db_query.last_success_at))
        details.add_row("Ultima falha", _date_or_dash(db_query.last_failed_at))
        details.add_row("Falhas consecutivas", str(db_query.consecutive_failures))
        details.add_row("Hits", str(_hits_for_query(session, db_query)))
        details.add_row("Vagas unicas", str(_unique_postings_for_query(session, db_query)))
        details.add_row(
            "Lifecycle conflicts",
            str(_lifecycle_conflicts_for_query(session, db_query)),
        )
        if db_query.last_run_id is not None:
            run = session.get(SourceRun, db_query.last_run_id)
            details.add_row("Novos resultados", str(run.items_created if run else 0))
            details.add_row("Conhecidos", str(run.items_skipped if run else 0))
            details.add_row("Ultima run", str(db_query.last_run_id))
    console.print(Panel(details, title="Consulta"))
    if db_query is None:
        console.print("Consulta ainda nao possui historico persistido.")


def _print_query_health(session: Session) -> None:
    queries = session.scalars(select(SearchQuery).order_by(SearchQuery.priority.asc())).all()
    active = [query for query in queries if query.is_active]
    healthy = [query for query in active if query.consecutive_failures == 0]
    warning = [query for query in active if 0 < query.consecutive_failures < 3]
    failing = [query for query in active if query.consecutive_failures >= 3]
    no_result = [
        query
        for query in active
        if _hits_for_query(session, query) == 0 and query.last_success_at is not None
    ]

    summary = Table(title="Saude das consultas")
    summary.add_column("Metrica")
    summary.add_column("Valor", justify="right")
    summary.add_row("Consultas ativas", str(len(active)))
    summary.add_row("Saudaveis", str(len(healthy)))
    summary.add_row("Com avisos", str(len(warning)))
    summary.add_row("Falhando", str(len(failing)))
    summary.add_row("Sem resultado", str(len(no_result)))
    console.print(summary)

    table = Table(title="Consultas persistidas")
    table.add_column("Key", no_wrap=True)
    table.add_column("Coletor")
    table.add_column("Estado")
    table.add_column("Ultima execucao")
    table.add_column("Requests", justify="right")
    table.add_column("Encontradas", justify="right")
    table.add_column("Novas", justify="right")
    table.add_column("Hits", justify="right")
    table.add_column("Unicas", justify="right")
    table.add_column("Conflicts", justify="right")
    for query in queries:
        run = session.get(SourceRun, query.last_run_id) if query.last_run_id else None
        table.add_row(
            query.key,
            query.collector_type,
            _persisted_query_health_label(query),
            _date_or_dash(query.last_checked_at),
            "-",
            str(run.items_found if run else 0),
            str(run.items_created if run else 0),
            str(_hits_for_query(session, query)),
            str(_unique_postings_for_query(session, query)),
            str(_lifecycle_conflicts_for_query(session, query)),
        )
    if queries:
        console.print(table)
    else:
        console.print("Nenhuma consulta persistida ainda.")


def _hits_for_query(session: Session, query: SearchQuery | None) -> int:
    if query is None:
        return 0
    value = session.scalar(
        select(func.count(DiscoveryHit.id)).where(DiscoveryHit.search_query_id == query.id)
    )
    return int(value or 0)


def _unique_postings_for_query(session: Session, query: SearchQuery | None) -> int:
    if query is None:
        return 0
    value = session.scalar(
        select(func.count(func.distinct(DiscoveryHit.posting_id))).where(
            DiscoveryHit.search_query_id == query.id,
            DiscoveryHit.posting_id.is_not(None),
        )
    )
    return int(value or 0)


def _lifecycle_conflicts_for_query(session: Session, query: SearchQuery | None) -> int:
    if query is None:
        return 0
    value = session.scalar(
        select(func.count(DiscoveryHit.id)).where(
            DiscoveryHit.search_query_id == query.id,
            DiscoveryHit.match_status == "lifecycle_conflict",
        )
    )
    return int(value or 0)


def _query_state(query: SearchQueryConfig, db_query: SearchQuery | None) -> str:
    if not query.enabled:
        return "desativada"
    if db_query is None or db_query.last_checked_at is None:
        return "sem historico"
    return _persisted_query_health_label(db_query)


def _persisted_query_health_label(query: SearchQuery) -> str:
    if not query.is_active:
        return "desativada"
    if query.consecutive_failures >= 3:
        return "falhando"
    if query.consecutive_failures > 0:
        return "aviso"
    if query.last_success_at is not None:
        return "saudavel"
    return "sem historico"


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


def _job_technologies(job: Job) -> list[str]:
    return list(technologies_from_json(job.technologies_json))


def _latest_profile_comparison_label(job: Job) -> str | None:
    if not job.profile_comparisons:
        return None
    comparison = max(
        job.profile_comparisons,
        key=lambda value: (value.created_at, value.id),
    )
    return f"{comparison.overall_score}/100 (perfil {comparison.profile_version_id})"


def _relevance_summary_label(decision: Decision) -> str:
    if not decision.relevance_reason_json:
        return "-"
    try:
        data = json.loads(decision.relevance_reason_json)
    except json.JSONDecodeError:
        return "-"
    pieces: list[str] = []
    for key in (
        "core_matches",
        "strong_adjacent_matches",
        "contextual_adjacent_matches",
        "supporting_context_matches",
        "negative_matches",
    ):
        matches = data.get(key)
        if not isinstance(matches, dict):
            continue
        terms = [
            str(term)
            for values in matches.values()
            if isinstance(values, list)
            for term in values[:3]
        ]
        if terms:
            pieces.append(f"{key}: {', '.join(terms[:4])}")
    return "; ".join(pieces) if pieces else str(data.get("explanation") or "-")


def _latest_hits_by_query(job: Job) -> list[DiscoveryHit]:
    latest: dict[int, DiscoveryHit] = {}
    for posting in job.postings:
        for hit in posting.discovery_hits:
            current = latest.get(hit.search_query_id)
            if current is None or hit.observed_at > current.observed_at:
                latest[hit.search_query_id] = hit
    return sorted(latest.values(), key=lambda hit: hit.search_query.key)


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
    if version == "0013_company_intelligence_interviews":
        return "OK", "Migrações", version
    return "AVISO", "Migrações", f"versão atual: {version}"


def _check_yaml_configs(settings: Settings) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    loaders: list[tuple[str, Callable[[], object]]] = [
        ("Perfil", lambda: load_profile(settings.config_dir, settings.profile_path).path),
        ("Elegibilidade", lambda: load_eligibility_rules(settings.config_dir).rules_version),
        ("Ranking", lambda: str(load_ranking_weights(settings.config_dir).recommended_min_score)),
        ("Relevância", lambda: load_relevance_rules(settings.config_dir).version),
        ("Rede", lambda: load_network_config(settings.config_dir).http.user_agent),
        ("Boards", lambda: str(len(load_company_boards(settings.config_dir).boards))),
        ("Consultas", lambda: str(len(load_search_queries(settings.config_dir).queries))),
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
