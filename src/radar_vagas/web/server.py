from __future__ import annotations

import hashlib
import ipaddress
import os

from radar_vagas.config.settings import PROJECT_ROOT, Settings
from radar_vagas.domain.errors import RadarError


def validate_bind_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as exc:
        raise RadarError("A interface web local so aceita host de loopback.") from exc
    if not address.is_loopback:
        raise RadarError("A interface web local nao pode escutar em host publico.")
    return normalized


class WebServerLock:
    def __init__(self, settings: Settings, port: int) -> None:
        database = settings.database_url
        digest = hashlib.sha256(f"{database}:{port}".encode()).hexdigest()[:16]
        self.path = PROJECT_ROOT / ".tmp" / f"radar-web-{digest}.lock"
        self._held = False

    def __enter__(self) -> WebServerLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raw_pid = self.path.read_text(encoding="utf-8").strip()
            if raw_pid and _process_is_running(raw_pid):
                raise RadarError("Ja existe uma interface web usando este banco e porta.")
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self._held = True
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        if self._held and self.path.exists():
            self.path.unlink()


def _process_is_running(raw_pid: str) -> bool:
    try:
        pid = int(raw_pid)
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
