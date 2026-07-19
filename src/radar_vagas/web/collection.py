from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from radar_vagas.collection.search_plan import SearchPlanRunResult, run_search_plan
from radar_vagas.config.settings import Settings
from radar_vagas.domain.errors import RadarError


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
        return self._status

    def run_search_plan(self, settings: Settings) -> SearchPlanRunResult:
        if not self._lock.acquire(blocking=False):
            raise RadarError("Ja existe uma coleta em andamento nesta interface.")
        started_at = datetime.now(UTC)
        self._status = CollectionStatusView(
            state="running",
            started_at=started_at,
            finished_at=None,
            message="Coleta em andamento.",
            found=0,
            created=0,
            errors=[],
        )
        try:
            result = run_search_plan(settings, dry_run=False, continue_on_error=True)
            found = sum(execution.summary.found for _query, execution in result.executions)
            created = sum(execution.summary.new for _query, execution in result.executions)
            state = "failed" if result.errors else "finished"
            message = "Coleta concluida." if not result.errors else "Coleta concluida com falhas."
            self._status = CollectionStatusView(
                state=state,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                message=message,
                found=found,
                created=created,
                errors=result.errors,
            )
            return result
        except Exception as exc:
            self._status = CollectionStatusView(
                state="failed",
                started_at=started_at,
                finished_at=datetime.now(UTC),
                message=f"Falha na coleta: {exc}",
                found=0,
                created=0,
                errors=[str(exc)],
            )
            raise
        finally:
            self._lock.release()
