from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.responses import Response

CSRF_COOKIE_NAME = "radar_csrf"
MUTABLE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_PROFILE_UPLOAD_BYTES = 256 * 1024
ALLOWED_PROFILE_SUFFIXES = {".yaml", ".yml", ".json", ".txt"}


def csrf_serializer(secret_key: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret_key, salt="radar-vagas-csrf")


def csrf_token_for_request(request: Request) -> str:
    token = getattr(request.state, "csrf_token", None)
    if isinstance(token, str) and token:
        return token
    return ""


async def csrf_protect(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie:
        raise HTTPException(status_code=403, detail="Token CSRF ausente.")
    serializer = csrf_serializer(str(request.app.state.csrf_secret))
    try:
        expected = serializer.loads(cookie)
    except BadSignature as exc:
        raise HTTPException(status_code=403, detail="Token CSRF invalido.") from exc
    supplied = request.headers.get("x-csrf-token")
    if not supplied:
        form = await request.form()
        value = form.get("csrf_token")
        supplied = value if isinstance(value, str) else None
    if (
        not isinstance(expected, str)
        or not supplied
        or not secrets.compare_digest(expected, supplied)
    ):
        raise HTTPException(status_code=403, detail="Token CSRF invalido.")


def set_csrf_cookie(request: Request, response: Response) -> None:
    token = csrf_token_for_request(request)
    if not token:
        return
    serializer = csrf_serializer(str(request.app.state.csrf_secret))
    signed = serializer.dumps(token)
    if request.cookies.get(CSRF_COOKIE_NAME) == signed:
        return
    response.set_cookie(
        CSRF_COOKIE_NAME,
        signed,
        httponly=True,
        samesite="strict",
        secure=False,
    )


def resolve_csrf_token(request: Request) -> str:
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    serializer = csrf_serializer(str(request.app.state.csrf_secret))
    if cookie:
        try:
            token = serializer.loads(cookie)
            if isinstance(token, str) and token:
                return token
        except BadSignature:
            pass
    return secrets.token_urlsafe(32)


def apply_security_headers(response: Response) -> None:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"


def safe_external_url(value: str | None) -> str | None:
    if value is None:
        return None
    url = value.strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if not parts.hostname or parts.username or parts.password:
        return None
    return url


def validate_upload_metadata(filename: str, content: bytes) -> None:
    suffix = _suffix(filename)
    if suffix not in ALLOWED_PROFILE_SUFFIXES:
        raise HTTPException(status_code=400, detail="Formato de perfil nao suportado.")
    if len(content) > MAX_PROFILE_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Arquivo de perfil muito grande.")
    if b"\x00" in content:
        raise HTTPException(status_code=400, detail="Conteudo de perfil invalido.")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Perfil deve estar em UTF-8.") from exc


def clean_upload_suffix(filename: str) -> str:
    suffix = _suffix(filename)
    return suffix if suffix in ALLOWED_PROFILE_SUFFIXES else ".txt"


def _suffix(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def positive_id(value: int, label: str = "id") -> int:
    if value <= 0:
        raise HTTPException(status_code=404, detail=f"{label} invalido.")
    return value


def form_value(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None
