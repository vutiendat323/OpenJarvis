"""Bounded HTTP transport for structured source discovery."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.security.ssrf import check_ssrf

_ALLOWED_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRY_STATUSES = frozenset({429, 503})
_MAX_REDIRECTS = 5
_USER_AGENT = "OpenJarvis/structured-discovery"


class DiscoveryHttpClient:
    """Fetch discovery evidence without allowing active HTTP methods."""

    def __init__(
        self,
        *,
        max_response_bytes: int = 1_048_576,
        require_https: bool = True,
        timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        client: httpx.Client | None = None,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes
        self._require_https = require_https
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._sleep = sleep
        self._client = client or httpx.Client(follow_redirects=False)
        self._owns_client = client is None
        self._deadline: float | None = None

    def set_deadline(self, deadline: float | None) -> None:
        """Set an absolute monotonic deadline for subsequent fetches."""
        self._deadline = deadline

    def set_require_https(self, require_https: bool) -> None:
        """Apply the active discovery constraints to subsequent fetches."""
        self._require_https = require_https

    def fetch(self, url: str, method: str = "GET") -> httpx.Response:
        method = method.upper()
        if method not in _ALLOWED_METHODS:
            raise DataPlaneError(
                DataPlaneErrorCode.DISCOVERY_UNSAFE_METHOD,
                f"Discovery method {method!r} is not safe",
            )

        current_url = url
        validation_context = "request"
        redirects = 0
        retry_used = False
        while True:
            self._validate_url(current_url, context=validation_context)
            response = self._request(method, current_url)

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    return response
                if redirects >= _MAX_REDIRECTS:
                    raise DataPlaneError(
                        DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                        "Discovery exceeded the maximum of five redirects",
                    )
                target = urljoin(str(response.url), location)
                current_url = target
                validation_context = "redirect"
                redirects += 1
                continue

            retry_delay = self._retry_delay(response)
            if retry_delay is not None and not retry_used:
                if (
                    self._deadline is not None
                    and self._clock() + retry_delay > self._deadline
                ):
                    raise DataPlaneError(
                        DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                        "Retry-After exceeds the discovery deadline",
                    )
                self._sleep(retry_delay)
                retry_used = True
                continue

            return response

    def _validate_url(self, url: str, *, context: str) -> None:
        try:
            parsed = urlsplit(url)
            parsed.port
        except ValueError as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                f"Unsafe discovery {context}: invalid URL",
            ) from exc
        if parsed.username is not None or parsed.password is not None:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                f"Unsafe discovery {context}: URL credentials are forbidden",
            )
        ssrf_error = check_ssrf(url)
        if ssrf_error:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                f"Unsafe discovery {context}: {ssrf_error}",
            )
        if not parsed.hostname:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                f"Unsafe discovery {context}: URL has no hostname",
            )
        if self._require_https and parsed.scheme.casefold() != "https":
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                f"Unsafe discovery {context}: HTTPS is required",
            )
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                f"Unsafe discovery {context}: unsupported URL scheme",
            )

    def _request(self, method: str, url: str) -> httpx.Response:
        timeout = self._timeout_seconds
        if self._deadline is not None:
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                raise DataPlaneError(
                    DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                    "Discovery deadline exceeded before HTTP request",
                )
            timeout = min(timeout, remaining)

        try:
            with self._client.stream(
                method,
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=timeout,
                follow_redirects=False,
            ) as streamed:
                body = bytearray()
                for chunk in streamed.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        raise DataPlaneError(
                            DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                            "Discovery response exceeded the size limit",
                        )
                return httpx.Response(
                    status_code=streamed.status_code,
                    headers=streamed.headers,
                    content=bytes(body),
                    request=streamed.request,
                    extensions=streamed.extensions,
                )
        except httpx.TimeoutException as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                "Discovery HTTP request exceeded its deadline",
            ) from exc
        except httpx.RequestError as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.PROVIDER_UNAVAILABLE,
                "Discovery HTTP request failed",
            ) from exc

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float | None:
        if response.status_code not in _RETRY_STATUSES:
            return None
        value = response.headers.get("retry-after")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["DiscoveryHttpClient"]
