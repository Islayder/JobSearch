from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def retry_delay_seconds(
    *,
    attempt_index: int,
    backoff_seconds: float,
    retry_after: str | None,
) -> float:
    if retry_after:
        parsed = _parse_retry_after(retry_after)
        if parsed is not None:
            return float(parsed)
    multiplier = 2 ** max(attempt_index - 1, 0)
    return float(backoff_seconds * multiplier)


def _parse_retry_after(value: str) -> float | None:
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        return float(stripped)
    try:
        parsed = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delay = (parsed - datetime.now(UTC)).total_seconds()
    return float(max(delay, 0.0))
