from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s<>'\"]+")
_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):.*", re.IGNORECASE | re.DOTALL)
_SECRET_PAIR_RE = re.compile(
    r"(?i)(token|secret|key|senha|password|authorization|cookie)=([^&\s]+)"
)


def sanitize_message(
    value: object,
    *,
    fallback: str = "Falha registrada sem detalhes sensiveis.",
) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    text = _TRACEBACK_RE.sub("Detalhes tecnicos omitidos.", text)
    text = _URL_RE.sub(_sanitize_url, text)
    text = _WINDOWS_PATH_RE.sub("[caminho-local]", text)
    text = _SECRET_PAIR_RE.sub(r"\1=[omitido]", text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 300:
        text = f"{text[:297]}..."
    return text or fallback


def _sanitize_url(match: re.Match[str]) -> str:
    value = match.group(0)
    scheme, _, rest = value.partition("://")
    host = rest.split("/", 1)[0].split("?", 1)[0]
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    return f"{scheme}://{host}/[omitido]"
