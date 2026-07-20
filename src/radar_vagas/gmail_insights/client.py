from __future__ import annotations

import base64
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from radar_vagas.config.schemas import GMAIL_READ_ONLY_SCOPE
from radar_vagas.domain.errors import RadarError
from radar_vagas.gmail_insights.types import GmailMessage


class GmailApiReadOnlyClient:
    def __init__(
        self,
        *,
        credentials_path: Path,
        token_path: Path,
        scopes: Sequence[str],
    ) -> None:
        if list(scopes) != [GMAIL_READ_ONLY_SCOPE]:
            raise RadarError("Gmail aceita somente o escopo gmail.readonly.")
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._scopes = tuple(scopes)
        self._service: Any | None = None

    def search_messages(self, query: str, max_results: int) -> list[GmailMessage]:
        service = self._gmail_service()
        response = (
            service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        )
        raw_messages = response.get("messages", []) if isinstance(response, dict) else []
        messages: list[GmailMessage] = []
        for item in raw_messages[:max_results]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            raw = (
                service.users().messages().get(userId="me", id=item["id"], format="full").execute()
            )
            if isinstance(raw, dict):
                messages.append(_message_from_api(raw))
        return messages

    def _gmail_service(self) -> Any:
        if self._service is not None:
            return self._service
        creds = self._credentials()
        build = _google_build()
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def _credentials(self) -> Any:
        if not self._token_path.exists():
            raise RadarError(
                "Token local do Gmail nao encontrado. Gere um token revogavel fora do Git "
                "com escopo gmail.readonly antes de sincronizar."
            )
        credentials_cls, request_cls = _google_auth_classes()
        creds = credentials_cls.from_authorized_user_file(str(self._token_path), self._scopes)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(request_cls())
                self._token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise RadarError("Token local do Gmail invalido ou sem refresh token.")
        if not self._credentials_path.exists():
            raise RadarError("Arquivo de credenciais local do Gmail nao encontrado.")
        return creds


def _message_from_api(raw: dict[str, Any]) -> GmailMessage:
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    headers = payload.get("headers")
    header_map = _headers(headers if isinstance(headers, list) else [])
    received = _received_at(raw, header_map.get("date"))
    return GmailMessage(
        message_id=str(raw.get("id", "")),
        thread_id=str(raw.get("threadId")) if raw.get("threadId") else None,
        sender=header_map.get("from", ""),
        subject=header_map.get("subject", ""),
        received_at=received,
        body=_extract_body(payload),
    )


def _headers(values: list[Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in values:
        if isinstance(item, dict) and item.get("name"):
            headers[str(item["name"]).lower()] = str(item.get("value", ""))
    return headers


def _received_at(raw: dict[str, Any], date_header: str | None) -> datetime:
    internal_date = raw.get("internalDate")
    if internal_date is not None:
        try:
            return datetime.fromtimestamp(int(str(internal_date)) / 1000, tz=UTC)
        except ValueError:
            pass
    if date_header:
        try:
            return parsedate_to_datetime(date_header).astimezone(UTC)
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC)


def _extract_body(payload: dict[str, Any]) -> str:
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain
    html = _find_part(payload, "text/html")
    if html:
        return BeautifulSoup(html, "html.parser").get_text(" ")
    return ""


def _find_part(payload: dict[str, Any], mime_type: str) -> str | None:
    if payload.get("mimeType") == mime_type:
        return _decode_part(payload)
    parts = payload.get("parts")
    if not isinstance(parts, list):
        return None
    for part in parts:
        if not isinstance(part, dict):
            continue
        found = _find_part(part, mime_type)
        if found:
            return found
    return None


def _decode_part(part: dict[str, Any]) -> str | None:
    body = part.get("body")
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, str):
        return None
    padded = data + ("=" * (-len(data) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _google_auth_classes() -> tuple[Any, Any]:
    try:
        from google.auth.transport.requests import (  # type: ignore[import-not-found]
            Request as GoogleAuthRequest,
        )
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RadarError(
            "Instale o extra opcional do Gmail antes de usar a integracao real: "
            'pip install -e ".[gmail]".'
        ) from exc
    return Credentials, GoogleAuthRequest


def _google_build() -> Any:
    try:
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RadarError(
            "Instale o extra opcional do Gmail antes de usar a integracao real: "
            'pip install -e ".[gmail]".'
        ) from exc
    return build
