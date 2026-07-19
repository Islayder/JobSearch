from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from radar_vagas.config.schemas import HttpConfig
from radar_vagas.http.client import HttpClient, cache_request_headers, safe_request_log_payload
from radar_vagas.http.errors import (
    ForbiddenAddressError,
    HttpStatusError,
    HttpTimeoutError,
    InvalidContentTypeError,
    InvalidUrlError,
    ResponseTooLargeError,
    TooManyRedirectsError,
)
from radar_vagas.http.security import UrlPolicy


class FakeResolver:
    def __init__(self, mapping: dict[str, Sequence[str]] | None = None) -> None:
        self.mapping = mapping or {"public.example": ["93.184.216.34"]}

    def resolve(self, hostname: str) -> Sequence[str]:
        if hostname not in self.mapping:
            raise AssertionError(f"Host inesperado no teste: {hostname}")
        return self.mapping[hostname]


class RebindingResolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, hostname: str) -> Sequence[str]:
        assert hostname == "public.example"
        self.calls += 1
        if self.calls == 1:
            return ["93.184.216.34"]
        return ["127.0.0.1"]


def test_http_get_reads_valid_response() -> None:
    client = _client(lambda _request: httpx.Response(200, json={"ok": True}))

    result = client.get("https://public.example/jobs")

    assert result.status_code == 200
    assert result.requests_made == 1
    assert result.bytes_received > 0
    client.close()


def test_http_retries_timeout_without_real_sleep() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.TimeoutException("timeout")
        return httpx.Response(200, json={"ok": True})

    client = _client(handler, sleep=sleeps.append)

    result = client.get("https://public.example/jobs")

    assert result.status_code == 200
    assert result.retries == 1
    assert sleeps == [0.5]
    client.close()


def test_http_respects_retry_after_for_429() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler, sleep=sleeps.append)

    result = client.get("https://public.example/jobs")

    assert result.retries == 1
    assert sleeps == [2.0]
    client.close()


def test_http_500_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": True})

    client = _client(handler)

    with pytest.raises(HttpStatusError):
        client.get("https://public.example/jobs")
    assert calls == 1
    client.close()


def test_http_response_size_limit() -> None:
    client = _client(
        lambda _request: httpx.Response(
            200,
            content=b"x" * 1025,
            headers={"Content-Type": "text/html"},
        ),
        config=HttpConfig(max_response_bytes=1024),
    )

    with pytest.raises(ResponseTooLargeError):
        client.get("https://public.example/jobs")
    client.close()


def test_http_rejects_unexpected_content_type() -> None:
    client = _client(
        lambda _request: httpx.Response(
            200,
            content=b"x",
            headers={"Content-Type": "image/png"},
        )
    )

    with pytest.raises(InvalidContentTypeError):
        client.get("https://public.example/jobs")
    client.close()


def test_http_follows_safe_redirect_and_blocks_excessive_redirects() -> None:
    client = _client(
        lambda _request: httpx.Response(302, headers={"Location": "/again"}),
        config=HttpConfig(max_redirects=1),
    )

    with pytest.raises(TooManyRedirectsError):
        client.get("https://public.example/jobs")
    client.close()


def test_http_blocks_redirect_to_private_address() -> None:
    client = _client(
        lambda _request: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
    )

    with pytest.raises(ForbiddenAddressError):
        client.get("https://public.example/jobs")
    client.close()


def test_http_cache_headers_and_304() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["if-none-match"] == '"abc"'
        assert request.headers["if-modified-since"] == "Wed, 01 Jul 2026 10:00:00 GMT"
        return httpx.Response(304, headers={"ETag": '"abc"'})

    client = _client(handler)

    result = client.get(
        "https://public.example/jobs",
        headers=cache_request_headers('"abc"', "Wed, 01 Jul 2026 10:00:00 GMT"),
    )

    assert result.not_modified is True
    assert result.content == b""
    client.close()


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/job.html",
        "ftp://public.example/job",
        "http://user:pass@public.example/job",
        "http://localhost/job",
        "http://127.0.0.1/job",
        "http://0.0.0.0/job",
        "http://10.0.0.1/job",
        "http://172.16.0.1/job",
        "http://192.168.0.1/job",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/job",
        "http://[fc00::1]/job",
        "https://public.example:8443/job",
        "https://host.local/job",
    ],
)
def test_url_policy_blocks_dangerous_urls(url: str) -> None:
    policy = UrlPolicy(resolver=FakeResolver())

    with pytest.raises((InvalidUrlError, ForbiddenAddressError)):
        policy.validate_url(url)


def test_url_policy_accepts_public_host_with_fake_dns() -> None:
    policy = UrlPolicy(resolver=FakeResolver({"jobs.example": ["93.184.216.34"]}))

    assert policy.validate_url("https://jobs.example/job/123") == "https://jobs.example/job/123"


def test_http_revalidates_dns_before_send_and_blocks_rebinding() -> None:
    resolver = RebindingResolver()
    requests_sent = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests_sent
        requests_sent += 1
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(
        HttpConfig(),
        resolver=resolver,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ForbiddenAddressError):
        client.get("https://public.example/jobs")
    assert resolver.calls == 2
    assert requests_sent == 0
    client.close()


def test_safe_log_payload_excludes_query_and_headers() -> None:
    payload = safe_request_log_payload(
        url="https://public.example/jobs?token=secret",
        status_code=200,
        bytes_received=10,
        attempt=1,
        collector="greenhouse",
    )

    assert payload == {
        "host": "public.example",
        "path": "/jobs",
        "status": 200,
        "bytes": 10,
        "attempt": 1,
        "collector": "greenhouse",
    }


def test_head_is_allowed_but_timeout_failure_is_controlled() -> None:
    client = _client(lambda _request: (_ for _ in ()).throw(httpx.TimeoutException("timeout")))

    with pytest.raises(HttpTimeoutError):
        client.head("https://public.example/jobs")
    client.close()


def _client(
    handler: httpx.MockTransport | object,
    *,
    config: HttpConfig | None = None,
    sleep: object | None = None,
) -> HttpClient:
    transport = (
        handler if isinstance(handler, httpx.MockTransport) else httpx.MockTransport(handler)
    )
    return HttpClient(
        config or HttpConfig(),
        resolver=FakeResolver(),
        transport=transport,
        sleep=sleep if callable(sleep) else (lambda _seconds: None),
    )
