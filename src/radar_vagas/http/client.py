from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from radar_vagas.config.schemas import HttpConfig
from radar_vagas.http.errors import (
    HttpClientError,
    HttpStatusError,
    HttpTimeoutError,
    InvalidContentTypeError,
    RateLimitError,
    ResponseTooLargeError,
    TooManyRedirectsError,
)
from radar_vagas.http.retry import RETRYABLE_STATUS_CODES, retry_delay_seconds
from radar_vagas.http.security import DNSResolver, SystemDNSResolver, UrlPolicy

HttpMethod = Literal["GET", "HEAD"]

ALLOWED_CONTENT_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xhtml+xml",
    "text/html",
}


@dataclass(frozen=True)
class HttpRequestResult:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    requests_made: int
    bytes_received: int
    retries: int
    redirects: int

    @property
    def text(self) -> str:
        return self.content.decode(_encoding_from_headers(self.headers), errors="replace")

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


class HttpClient:
    def __init__(
        self,
        config: HttpConfig | None = None,
        *,
        resolver: DNSResolver | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        minimum_interval_between_requests_seconds: float = 0,
    ) -> None:
        self.config = config or HttpConfig()
        self.policy = UrlPolicy(
            allowed_ports=tuple(self.config.allowed_ports),
            resolver=resolver or SystemDNSResolver(),
        )
        self._sleep = sleep
        self._rate_limiter = HostRateLimiter(
            minimum_interval_seconds=minimum_interval_between_requests_seconds,
            monotonic=monotonic,
            sleep=sleep,
        )
        timeout = httpx.Timeout(
            connect=self.config.connect_timeout_seconds,
            read=self.config.read_timeout_seconds,
            write=self.config.write_timeout_seconds,
            pool=self.config.pool_timeout_seconds,
        )
        self._client = httpx.Client(
            follow_redirects=False,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": self.config.user_agent, "Accept-Encoding": "identity"},
        )

    def close(self) -> None:
        self._client.close()

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        allowed_hosts: tuple[str, ...] | None = None,
    ) -> HttpRequestResult:
        return self.request("GET", url, headers=headers, allowed_hosts=allowed_hosts)

    def head(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        allowed_hosts: tuple[str, ...] | None = None,
    ) -> HttpRequestResult:
        return self.request("HEAD", url, headers=headers, allowed_hosts=allowed_hosts)

    def request(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        allowed_hosts: tuple[str, ...] | None = None,
    ) -> HttpRequestResult:
        if method not in {"GET", "HEAD"}:
            raise HttpClientError("Somente GET e HEAD sao permitidos.")

        current_url = self.policy.validate_url(url)
        _validate_allowed_host(current_url, allowed_hosts)
        redirects = 0
        requests_made = 0
        retries = 0

        while True:
            attempt = 0
            while True:
                attempt += 1
                try:
                    current_url = self.policy.validate_url(current_url)
                    _validate_allowed_host(current_url, allowed_hosts)
                    self._rate_limiter.wait(current_url)
                    requests_made += 1
                    response = self._send_once(method, current_url, headers=headers)
                except httpx.TimeoutException as exc:
                    if method == "GET" and attempt <= self.config.max_retries:
                        retries += 1
                        self._sleep(
                            retry_delay_seconds(
                                attempt_index=attempt,
                                backoff_seconds=self.config.retry_backoff_seconds,
                                retry_after=None,
                            )
                        )
                        continue
                    raise HttpTimeoutError("Timeout ao coletar URL apos retries.") from exc
                except httpx.ConnectError as exc:
                    if method == "GET" and attempt <= self.config.max_retries:
                        retries += 1
                        self._sleep(
                            retry_delay_seconds(
                                attempt_index=attempt,
                                backoff_seconds=self.config.retry_backoff_seconds,
                                retry_after=None,
                            )
                        )
                        continue
                    raise HttpClientError("Falha de conexao ao coletar URL.") from exc

                if _should_retry(method, response.status_code, attempt, self.config.max_retries):
                    retries += 1
                    self._sleep(
                        retry_delay_seconds(
                            attempt_index=attempt,
                            backoff_seconds=self.config.retry_backoff_seconds,
                            retry_after=response.headers.get("retry-after"),
                        )
                    )
                    continue
                break

            if _is_redirect(response.status_code):
                redirects += 1
                if redirects > self.config.max_redirects:
                    raise TooManyRedirectsError("Redirecionamentos excederam o limite.")
                location = response.headers.get("location")
                if not location:
                    raise HttpStatusError("Redirect sem header Location.")
                current_url = self.policy.validate_url(urljoin(current_url, location))
                _validate_allowed_host(current_url, allowed_hosts)
                continue

            if response.status_code == 304:
                return HttpRequestResult(
                    url=current_url,
                    status_code=response.status_code,
                    headers=_lower_headers(response.headers),
                    content=b"",
                    requests_made=requests_made,
                    bytes_received=0,
                    retries=retries,
                    redirects=redirects,
                )

            if response.status_code == 429:
                raise RateLimitError("Servidor retornou rate limit persistente: HTTP 429.")
            if response.status_code >= 400:
                raise HttpStatusError(f"HTTP nao recuperavel: {response.status_code}.")

            headers_dict = _lower_headers(response.headers)
            if method == "GET":
                _validate_content_type(headers_dict)
            content = response.content if method == "GET" else b""
            return HttpRequestResult(
                url=current_url,
                status_code=response.status_code,
                headers=headers_dict,
                content=content,
                requests_made=requests_made,
                bytes_received=len(content),
                retries=retries,
                redirects=redirects,
            )

    def _send_once(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        merged_headers = dict(headers or {})
        with self._client.stream(method, url, headers=merged_headers) as response:
            if method == "GET" and response.status_code < 300:
                headers_dict = _lower_headers(response.headers)
                _validate_content_type(headers_dict)
                chunks: list[bytes] = []
                bytes_received = 0
                for chunk in response.iter_bytes():
                    bytes_received += len(chunk)
                    if bytes_received > self.config.max_response_bytes:
                        raise ResponseTooLargeError("Resposta excedeu o limite configurado.")
                    chunks.append(chunk)
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=b"".join(chunks),
                    request=response.request,
                )
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=b"",
                request=response.request,
            )


def cache_request_headers(etag: str | None, last_modified: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return headers


class HostRateLimiter:
    def __init__(
        self,
        *,
        minimum_interval_seconds: float,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("minimum_interval_seconds nao pode ser negativo.")
        self.minimum_interval_seconds = minimum_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last_request_at_by_host: dict[str, float] = {}

    def wait(self, url: str) -> None:
        if self.minimum_interval_seconds <= 0:
            return
        host = (urlsplit(url).hostname or "").strip(".").lower()
        if not host:
            return
        with self._lock:
            now = self._monotonic()
            previous = self._last_request_at_by_host.get(host)
            wait_seconds = (
                max(0.0, self.minimum_interval_seconds - (now - previous))
                if previous is not None
                else 0.0
            )
            if wait_seconds > 0:
                self._sleep(wait_seconds)
                now = self._monotonic()
            self._last_request_at_by_host[host] = now


def safe_request_log_payload(
    *,
    url: str,
    status_code: int | None,
    bytes_received: int,
    attempt: int,
    collector: str,
) -> dict[str, object]:
    parts = urlsplit(url)
    return {
        "host": parts.hostname,
        "path": parts.path or "/",
        "status": status_code,
        "bytes": bytes_received,
        "attempt": attempt,
        "collector": collector,
    }


def _should_retry(method: HttpMethod, status_code: int, attempt: int, max_retries: int) -> bool:
    return method == "GET" and status_code in RETRYABLE_STATUS_CODES and attempt <= max_retries


def _is_redirect(status_code: int) -> bool:
    return status_code in {301, 302, 303, 307, 308}


def _lower_headers(headers: httpx.Headers) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


def _validate_content_type(headers: Mapping[str, str]) -> None:
    content_type = headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type.endswith("+json"):
        return
    if media_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidContentTypeError(f"Tipo de conteudo nao permitido: {content_type or '-'}")


def _validate_allowed_host(url: str, allowed_hosts: tuple[str, ...] | None) -> None:
    if allowed_hosts is None:
        return
    hostname = (urlsplit(url).hostname or "").strip(".").lower()
    normalized_allowed = {host.strip(".").lower() for host in allowed_hosts}
    if hostname not in normalized_allowed:
        raise HttpClientError(f"Host fora da allowlist da coleta: {hostname}.")


def _encoding_from_headers(headers: Mapping[str, str]) -> str:
    content_type = headers.get("content-type", "")
    for part in content_type.split(";"):
        stripped = part.strip()
        if stripped.lower().startswith("charset="):
            return stripped.split("=", 1)[1] or "utf-8"
    return "utf-8"
