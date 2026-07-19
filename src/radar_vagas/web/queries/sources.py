from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from radar_vagas.domain.enums import SourceRunStatus
from radar_vagas.persistence.models import CompanyBoard, SearchQuery, Source, SourceRun
from radar_vagas.web.sanitization import sanitize_message

HealthState = Literal["healthy", "warning", "failing", "never-run", "disabled"]


@dataclass(frozen=True)
class SourceHealthRow:
    kind: str
    key: str
    label: str
    active: bool
    state: HealthState
    last_run: SourceRun | None
    last_success_at: object | None
    last_failed_at: object | None
    consecutive_failures: int
    items_found: int
    items_created: int
    items_skipped: int
    message: str


def sources_context(session: Session) -> dict[str, Any]:
    rows = source_health_rows(session)
    return {
        "health_rows": rows,
        "sources": _sources(session),
        "boards": _boards(session),
        "queries": _queries(session),
        "recent_runs": recent_source_runs(session, limit=20),
        "failed_runs": sum(1 for row in rows if row.state == "failing"),
    }


def source_health_summary(session: Session) -> dict[str, Any]:
    rows = source_health_rows(session)
    problem_states = {"warning", "failing", "never-run"}
    return {
        "rows": rows[:8],
        "problem_count": sum(1 for row in rows if row.state in problem_states),
    }


def source_health_rows(session: Session) -> list[SourceHealthRow]:
    rows: list[SourceHealthRow] = []
    rows.extend(_source_row(source) for source in _sources(session))
    rows.extend(_board_row(board) for board in _boards(session))
    rows.extend(_query_row(query) for query in _queries(session))
    return rows


def recent_source_runs(session: Session, *, limit: int) -> list[SourceRun]:
    return list(
        session.scalars(
            select(SourceRun)
            .options(selectinload(SourceRun.source))
            .order_by(SourceRun.started_at.desc(), SourceRun.id.desc())
            .limit(limit)
        ).all()
    )


def _sources(session: Session) -> list[Source]:
    return list(
        session.scalars(
            select(Source)
            .options(selectinload(Source.runs))
            .order_by(Source.name.asc(), Source.id.asc())
        ).all()
    )


def _boards(session: Session) -> list[CompanyBoard]:
    return list(
        session.scalars(
            select(CompanyBoard)
            .options(
                selectinload(CompanyBoard.source),
                selectinload(CompanyBoard.last_run),
                selectinload(CompanyBoard.company),
            )
            .order_by(CompanyBoard.key.asc().nullslast(), CompanyBoard.id.asc())
        ).all()
    )


def _queries(session: Session) -> list[SearchQuery]:
    return list(
        session.scalars(
            select(SearchQuery)
            .options(selectinload(SearchQuery.last_run))
            .order_by(SearchQuery.priority.asc(), SearchQuery.key.asc())
        ).all()
    )


def _source_row(source: Source) -> SourceHealthRow:
    last_run = max(source.runs, key=lambda run: run.started_at) if source.runs else None
    state = _health_state(source.is_active, last_run, 0)
    return SourceHealthRow(
        kind="Fonte",
        key=source.slug,
        label=source.name,
        active=source.is_active,
        state=state,
        last_run=last_run,
        last_success_at=None,
        last_failed_at=None,
        consecutive_failures=0,
        items_found=last_run.items_found if last_run else 0,
        items_created=last_run.items_created if last_run else 0,
        items_skipped=last_run.items_skipped if last_run else 0,
        message=_run_message(last_run, state),
    )


def _board_row(board: CompanyBoard) -> SourceHealthRow:
    state = _health_state(board.is_active, board.last_run, board.consecutive_failures)
    label = board.company.canonical_name if board.company else board.key or f"Board {board.id}"
    return SourceHealthRow(
        kind="Board",
        key=board.key or f"board-{board.id}",
        label=label,
        active=board.is_active,
        state=state,
        last_run=board.last_run,
        last_success_at=board.last_success_at,
        last_failed_at=board.last_failed_at,
        consecutive_failures=board.consecutive_failures,
        items_found=board.last_run.items_found if board.last_run else 0,
        items_created=board.last_run.items_created if board.last_run else 0,
        items_skipped=board.last_run.items_skipped if board.last_run else 0,
        message=_disabled_or_run_message(board.disabled_reason, board.last_run, state),
    )


def _query_row(query: SearchQuery) -> SourceHealthRow:
    state = _health_state(query.is_active, query.last_run, query.consecutive_failures)
    return SourceHealthRow(
        kind="Consulta",
        key=query.key,
        label=query.key,
        active=query.is_active,
        state=state,
        last_run=query.last_run,
        last_success_at=query.last_success_at,
        last_failed_at=query.last_failed_at,
        consecutive_failures=query.consecutive_failures,
        items_found=query.last_run.items_found if query.last_run else 0,
        items_created=query.last_run.items_created if query.last_run else 0,
        items_skipped=query.last_run.items_skipped if query.last_run else 0,
        message=_disabled_or_run_message(query.disabled_reason, query.last_run, state),
    )


def _health_state(active: bool, last_run: SourceRun | None, failures: int) -> HealthState:
    if not active:
        return "disabled"
    if last_run is None:
        return "never-run"
    if last_run.status is SourceRunStatus.FAILED or failures > 0:
        return "failing"
    if last_run.items_skipped:
        return "warning"
    return "healthy"


def _disabled_or_run_message(
    disabled_reason: str | None,
    last_run: SourceRun | None,
    state: HealthState,
) -> str:
    if state == "disabled" and disabled_reason:
        return sanitize_message(disabled_reason)
    return _run_message(last_run, state)


def _run_message(last_run: SourceRun | None, state: HealthState) -> str:
    if state == "never-run":
        return "Ainda sem execucao registrada."
    if state == "disabled":
        return "Desativado localmente."
    if last_run and last_run.error_message:
        return sanitize_message(last_run.error_message)
    if state == "warning":
        return "Ultima execucao teve itens ignorados."
    if state == "failing":
        return "Ultima execucao falhou."
    return "Ultima execucao saudavel."
