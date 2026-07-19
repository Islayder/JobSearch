from radar_vagas.domain.errors import RadarError


class HttpClientError(RadarError):
    """Base error for controlled HTTP failures."""


class InvalidUrlError(HttpClientError):
    """Raised when an URL cannot be safely parsed."""


class ForbiddenAddressError(HttpClientError):
    """Raised when an URL resolves to a blocked address."""


class DnsResolutionError(HttpClientError):
    """Raised when DNS resolution fails or returns no usable address."""


class HttpTimeoutError(HttpClientError):
    """Raised when a request times out after retries."""


class ResponseTooLargeError(HttpClientError):
    """Raised when a response exceeds the configured size limit."""


class InvalidContentTypeError(HttpClientError):
    """Raised when the response media type is outside the policy."""


class HttpStatusError(HttpClientError):
    """Raised for non-recoverable HTTP status codes."""


class RateLimitError(HttpStatusError):
    """Raised when rate limit responses persist after retry."""


class TooManyRedirectsError(HttpClientError):
    """Raised when redirect count exceeds the policy."""
