from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str | None
    sender: str
    subject: str
    received_at: datetime
    body: str


class GmailReadOnlyClient(Protocol):
    def search_messages(self, query: str, max_results: int) -> Sequence[GmailMessage]:
        """Return messages without mutating mailbox state."""
