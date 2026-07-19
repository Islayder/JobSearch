from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

import httpx
import pytest

from radar_vagas.config.schemas import HttpConfig
from radar_vagas.http.client import (
    HostRateLimiter,
    HttpClient,
    HttpRequestBudget,
    cache_request_headers,
    safe_request_log_payload,
)
from radar_vagas.http.errors import (
    ForbiddenAddressError,
    HttpBudgetExceededError,
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


def test_http_rate_limiter_waits_per_host_without_real_sleep() -> None:
    calls: list[str] = []
    sleeps: list[float] = []
    clock_value = 0.0

    def clock() -> float:
        return clock_value

    def sleep(seconds: float) -> None:
        nonlocal clock_value
        sleeps.append(seconds)
        clock_value += seconds

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    client = HttpClient(
        HttpConfig(),
        resolver=FakeResolver(
            {
                "public.example": ["93.184.216.34"],
                "other.example": ["93.184.216.35"],
            }
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        monotonic=clock,
        minimum_interval_between_requests_seconds=2,
    )

    client.get("https://public.example/jobs")
    client.get("https://public.example/jobs?page=2")
    client.get("https://other.example/jobs")

    assert len(calls) == 3
    assert sleeps == [2.0]
    client.close()


def test_host_rate_limiter_serializes_same_host_across_threads() -> None:
    sleeps: list[float] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(3)
    limiter = HostRateLimiter(
        minimum_interval_seconds=2,
        monotonic=lambda: 0.0,
        sleep=sleeps.append,
    )

    def wait_on_same_host() -> None:
        try:
            barrier.wait(timeout=1)
            limiter.wait("https://public.example/jobs")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=wait_on_same_host) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(sleeps) == [2.0, 4.0]


def test_host_rate_limiter_does_not_block_other_hosts_while_one_host_sleeps() -> None:
    sleeping = threading.Event()
    release_sleep = threading.Event()
    waits: list[float] = []
    limiter = HostRateLimiter(
        minimum_interval_seconds=5,
        monotonic=lambda: 0.0,
        sleep=lambda seconds: _blocking_sleep(seconds, waits, sleeping, release_sleep),
    )
    limiter.wait("https://public.example/jobs")

    first_host_thread = threading.Thread(
        target=lambda: limiter.wait("https://public.example/jobs?page=2")
    )
    first_host_thread.start()
    assert sleeping.wait(timeout=1)

    other_host_thread = threading.Thread(target=lambda: limiter.wait("https://other.example/jobs"))
    other_host_thread.start()
    other_host_thread.join(timeout=1)

    release_sleep.set()
    first_host_thread.join(timeout=1)

    assert not other_host_thread.is_alive()
    assert not first_host_thread.is_alive()
    assert waits == [5.0]


def test_http_rate_limiter_applies_to_redirect_and_retry() -> None:
    responses = [
        httpx.Response(302, headers={"Location": "/redirected"}),
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True}),
    ]
    sleeps: list[float] = []
    clock_value = 0.0

    def clock() -> float:
        return clock_value

    def sleep(seconds: float) -> None:
        nonlocal clock_value
        sleeps.append(seconds)
        clock_value += seconds

    def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = HttpClient(
        HttpConfig(max_retries=1),
        resolver=FakeResolver(),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        monotonic=clock,
        minimum_interval_between_requests_seconds=2,
    )

    result = client.get("https://public.example/jobs")

    assert result.status_code == 200
    assert result.requests_made == 3
    assert result.retries == 1
    assert sleeps == [2.0, 3.0]
    client.close()


def test_http_budget_counts_redirect_retry_and_blocks_next_attempt() -> None:
    responses = [
        httpx.Response(302, headers={"Location": "/redirected"}),
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, json={"ok": True}),
    ]
    sent_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_paths.append(request.url.path)
        return responses.pop(0)

    budget = HttpRequestBudget(max_requests=2)
    client = _client(
        handler,
        config=HttpConfig(max_retries=1, retry_backoff_seconds=0),
        request_budget=budget,
    )

    with pytest.raises(HttpBudgetExceededError) as exc_info:
        client.get("https://public.example/jobs")

    assert sent_paths == ["/jobs", "/redirected"]
    assert budget.requests_used == 2
    assert exc_info.value.limited_by == "max_total_requests"
    assert exc_info.value.requests_made == 2
    assert exc_info.value.retries == 1
    assert exc_info.value.redirects == 1
    client.close()


def test_http_budget_counts_head_attempts() -> None:
    sent_methods: list[str] = []
    budget = HttpRequestBudget(max_requests=1)
    client = _client(
        lambda request: sent_methods.append(request.method) or httpx.Response(200, headers={}),
        request_budget=budget,
    )

    result = client.head("https://public.example/jobs")
    with pytest.raises(HttpBudgetExceededError):
        client.head("https://public.example/jobs/again")

    assert result.requests_made == 1
    assert budget.requests_used == 1
    assert sent_methods == ["HEAD"]
    client.close()


def test_http_budget_checks_deadline_before_request() -> None:
    clock_value = 0.0
    sent = 0

    def clock() -> float:
        return clock_value

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent += 1
        return httpx.Response(200, json={"ok": True})

    budget = HttpRequestBudget(max_duration_seconds=1, monotonic=clock)
    clock_value = 1.0
    client = _client(handler, request_budget=budget)

    with pytest.raises(HttpBudgetExceededError) as exc_info:
        client.get("https://public.example/jobs")

    assert sent == 0
    assert exc_info.value.limited_by == "max_duration_seconds"
    client.close()


def test_http_budget_checks_deadline_before_rate_limit_wait() -> None:
    clock_value = 0.0
    sleeps: list[float] = []
    sent_paths: list[str] = []

    def clock() -> float:
        return clock_value

    def handler(request: httpx.Request) -> httpx.Response:
        sent_paths.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    budget = HttpRequestBudget(max_duration_seconds=0.75, monotonic=clock)
    client = _client(
        handler,
        sleep=sleeps.append,
        monotonic=clock,
        minimum_interval_between_requests_seconds=1,
        request_budget=budget,
    )
    client.get("https://public.example/jobs")
    clock_value = 0.5

    with pytest.raises(HttpBudgetExceededError) as exc_info:
        client.get("https://public.example/jobs?page=2")

    assert exc_info.value.limited_by == "max_duration_seconds"
    assert sent_paths == ["/jobs"]
    assert sleeps == []
    client.close()


def _client(
    handler: httpx.MockTransport | object,
    *,
    config: HttpConfig | None = None,
    sleep: object | None = None,
    monotonic: Callable[[], float] | None = None,
    minimum_interval_between_requests_seconds: float = 0,
    request_budget: HttpRequestBudget | None = None,
) -> HttpClient:
    transport = (
        handler if isinstance(handler, httpx.MockTransport) else httpx.MockTransport(handler)
    )
    return HttpClient(
        config or HttpConfig(),
        resolver=FakeResolver(),
        transport=transport,
        sleep=sleep if callable(sleep) else (lambda _seconds: None),
        monotonic=monotonic or time_monotonic_zero,
        minimum_interval_between_requests_seconds=minimum_interval_between_requests_seconds,
        request_budget=request_budget,
    )


def _blocking_sleep(
    seconds: float,
    waits: list[float],
    sleeping: threading.Event,
    release_sleep: threading.Event,
) -> None:
    waits.append(seconds)
    sleeping.set()
    release_sleep.wait(timeout=1)


def time_monotonic_zero() -> float:
    return 0.0
