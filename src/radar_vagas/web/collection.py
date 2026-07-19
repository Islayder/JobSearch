from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from radar_vagas.collection.search_plan import SearchPlanRunResult, run_search_plan
from radar_vagas.config.settings import Settings
from radar_vagas.domain.errors import RadarError
from radar_vagas.web.sanitization import sanitize_message


@dataclass(frozen=True)
class CollectionStatusView:
    state: str
    started_at: datetime | None
    finished_at: datetime | None
    message: str
    found: int
    created: int
    errors: list[str]


class LocalCollectionRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status = CollectionStatusView(
            state="idle",
            started_at=None,
            finished_at=None,
            message="Nenhuma coleta executada nesta sessao.",
            found=0,
            created=0,
            errors=[],
        )

    @property
    def status(self) -> CollectionStatusView:
        with self._status_lock:
            return self._status

    def start_search_plan(self, settings: Settings) -> CollectionStatusView:
        if not self._lock.acquire(blocking=False):
            raise RadarError("Ja existe uma coleta em andamento nesta interface.")
        started_at = datetime.now(UTC)
        self._set_status(
            CollectionStatusView(
                state="running",
                started_at=started_at,
                finished_at=None,
                message="Coleta em andamento.",
                found=0,
                created=0,
                errors=[],
            )
        )
        worker = threading.Thread(
            target=self._run_search_plan_worker,
            args=(settings, started_at),
            name="radar-web-collection",
            daemon=True,
        )
        worker.start()
        return self.status

    def run_search_plan(self, settings: Settings) -> SearchPlanRunResult:
        if not self._lock.acquire(blocking=False):
            raise RadarError("Ja existe uma coleta em andamento nesta interface.")
        started_at = datetime.now(UTC)
        self._set_status(
            CollectionStatusView(
                state="running",
                started_at=started_at,
                finished_at=None,
                message="Coleta em andamento.",
                found=0,
                created=0,
                errors=[],
            )
        )
        try:
            result = run_search_plan(settings, dry_run=False, continue_on_error=True)
            self._finish_from_result(result, started_at)
            return result
        except Exception as exc:
            self._set_status(
                CollectionStatusView(
                    state="failed",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    message=f"Falha na coleta: {sanitize_message(exc)}",
                    found=0,
                    created=0,
                    errors=[sanitize_message(exc)],
                )
            )
            raise
        finally:
            self._lock.release()

    def _run_search_plan_worker(self, settings: Settings, started_at: datetime) -> None:
        try:
            result = run_search_plan(settings, dry_run=False, continue_on_error=True)
            self._finish_from_result(result, started_at)
        except Exception as exc:
            self._set_status(
                CollectionStatusView(
                    state="failed",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    message=f"Falha na coleta: {sanitize_message(exc)}",
                    found=0,
                    created=0,
                    errors=[sanitize_message(exc)],
                )
            )
        finally:
            self._lock.release()

    def _finish_from_result(self, result: SearchPlanRunResult, started_at: datetime) -> None:
        found = sum(execution.summary.found for _query, execution in result.executions)
        created = sum(execution.summary.new for _query, execution in result.executions)
        errors = [sanitize_message(error) for error in result.errors]
        state = "failed" if errors else "finished"
        message = "Coleta concluida." if not errors else "Coleta concluida com falhas."
        self._set_status(
            CollectionStatusView(
                state=state,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                message=message,
                found=found,
                created=created,
                errors=errors,
            )
        )

    def _set_status(self, status: CollectionStatusView) -> None:
        with self._status_lock:
            self._status = status
