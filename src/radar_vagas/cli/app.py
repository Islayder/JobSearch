import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from radar_vagas.config.loaders import (
    load_blocked_companies,
    load_eligibility_rules,
    load_profile,
    load_ranking_weights,
)
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
    if version == "0002_file_import_audit":
        return "OK", "Migrações", version
    return "AVISO", "Migrações", f"versão atual: {version}"


def _check_yaml_configs(settings: Settings) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []
    loaders: list[tuple[str, Callable[[], object]]] = [
        ("Perfil", lambda: load_profile(settings.config_dir, settings.profile_path).path),
        ("Elegibilidade", lambda: load_eligibility_rules(settings.config_dir).rules_version),
        ("Ranking", lambda: str(load_ranking_weights(settings.config_dir).recommended_min_score)),
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
    modules = ["sqlalchemy", "alembic", "pydantic", "yaml", "typer", "rich"]
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
