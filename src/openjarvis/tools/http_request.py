"""HTTP request tool — make HTTP requests with SSRF protection."""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security.ssrf import check_ssrf
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

# Maximum response body size: 1 MB
_MAX_RESPONSE_BYTES = 1_048_576

_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"})

# Cap redirect chains so a malicious server cannot loop us indefinitely.
_MAX_REDIRECTS = 5

# Methods whose failure can leave the server changed.
_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# A timeout after sending is not a failure -- it is an unknown. Retrying it
# sends a second email, books a second appointment, charges a second time.
_AMBIGUOUS_OUTCOME = (
    " This request may already have been applied and its outcome is unknown."
    " Observe whether it happened before retrying -- do not repeat it blindly."
)


def _never_reached_server(exc: Exception) -> bool:
    """True when the request provably never left, so a retry is safe."""
    return isinstance(
        exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
    )


class _SSRFRedirectError(Exception):
    """Raised when a redirect target fails the SSRF check."""


class _RequestProgress:
    """What reached a server before a redirect-stage failure."""

    def __init__(self) -> None:
        self.mutation_response_seen = False


@ToolRegistry.register("http_request")
class HttpRequestTool(BaseTool):
    """Make HTTP requests to external APIs with SSRF protection."""

    tool_id = "http_request"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_request",
            description=(
                "Make an HTTP request to a URL."
                " Supports GET, POST, PUT, DELETE, PATCH,"
                " and HEAD methods. Includes SSRF protection"
                " against private IPs and cloud metadata."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to send the request to.",
                    },
                    "method": {
                        "type": "string",
                        "description": (
                            "HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD)."
                            " Defaults to GET."
                        ),
                    },
                    "headers": {
                        "type": "object",
                        "description": "Optional HTTP headers as key-value pairs.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Optional request body (for POST, PUT, PATCH).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Request timeout in seconds. Defaults to 30.",
                    },
                },
                "required": ["url"],
            },
            category="network",
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        url = params.get("url", "")
        if not url:
            return ToolResult(
                tool_name="http_request",
                content="No URL provided.",
                success=False,
            )

        method = params.get("method", "GET").upper()
        if method not in _ALLOWED_METHODS:
            return ToolResult(
                tool_name="http_request",
                content=(
                    f"Unsupported HTTP method: {method}."
                    f" Allowed: {', '.join(sorted(_ALLOWED_METHODS))}."
                ),
                success=False,
            )

        # SSRF protection check
        ssrf_error = check_ssrf(url)
        if ssrf_error:
            return ToolResult(
                tool_name="http_request",
                content=f"SSRF protection blocked request: {ssrf_error}",
                success=False,
            )

        headers = {
            k: os.path.expandvars(v) if isinstance(v, str) else v
            for k, v in (params.get("headers") or {}).items()
        }
        body = params.get("body")
        timeout = params.get("timeout", 30)

        _rust = None
        # Reads only. A POST that fails inside the Rust path may already have
        # reached the server, and falling through to httpx would send it twice
        # -- a second order, a second email, a second charge.
        if method in {"GET", "HEAD"}:
            try:
                from openjarvis._rust_bridge import get_rust_module

                _rust = get_rust_module()
            except ImportError:
                pass
        if _rust is not None and not headers:
            try:
                content = _rust.HttpRequestTool().execute(url, method, body)
                return ToolResult(
                    tool_name="http_request",
                    content=(
                        content[:_MAX_RESPONSE_BYTES]
                        if len(content) > _MAX_RESPONSE_BYTES
                        else content
                    ),
                    success=True,
                    metadata={
                        # The Rust API returns no status. Reporting 200 would be
                        # a guess, and evidence checks that require a 2xx would
                        # then trust a number nobody observed.
                        "status_code": None,
                        "truncated": len(content) > _MAX_RESPONSE_BYTES,
                    },
                )
            except Exception as exc:
                logger.debug("Rust HTTP request fallback to httpx: %s", exc)

        progress = _RequestProgress()
        try:
            t0 = time.time()
            # Follow redirects manually so each hop is re-checked for SSRF — an
            # allowed public URL must not be able to 30x-redirect us to an
            # internal/metadata address.
            response = self._request_following_redirects(
                method,
                url,
                headers=headers,
                content=body,
                timeout=float(timeout),
                progress=progress,
            )
            elapsed_ms = (time.time() - t0) * 1000

            content_type = response.headers.get("content-type", "")
            response_headers = dict(response.headers)

            # Truncate response body if larger than 1 MB
            raw_body = response.text
            truncated = False
            if len(raw_body) > _MAX_RESPONSE_BYTES:
                raw_body = raw_body[:_MAX_RESPONSE_BYTES]
                truncated = True

            content = raw_body
            if truncated:
                content += "\n\n[Response truncated at 1 MB]"

            return ToolResult(
                tool_name="http_request",
                content=content,
                success=True,
                metadata={
                    "status_code": response.status_code,
                    "headers": response_headers,
                    "content_type": content_type,
                    "elapsed_ms": round(elapsed_ms, 2),
                    "truncated": truncated,
                    "final_url": str(response.url),
                },
            )
        except httpx.TimeoutException as exc:
            content = f"Request timed out after {timeout}s: {exc}"
            if method in _STATE_CHANGING_METHODS and (
                progress.mutation_response_seen or not _never_reached_server(exc)
            ):
                content += _AMBIGUOUS_OUTCOME
            return ToolResult(
                tool_name="http_request",
                content=content,
                success=False,
            )
        except _SSRFRedirectError as exc:
            content = f"SSRF protection blocked redirect: {exc}"
            if method in _STATE_CHANGING_METHODS and progress.mutation_response_seen:
                content += _AMBIGUOUS_OUTCOME
            return ToolResult(
                tool_name="http_request",
                content=content,
                success=False,
            )
        except httpx.RequestError as exc:
            content = f"Request error: {exc}"
            if method in _STATE_CHANGING_METHODS and (
                progress.mutation_response_seen or not _never_reached_server(exc)
            ):
                content += _AMBIGUOUS_OUTCOME
            return ToolResult(
                tool_name="http_request",
                content=content,
                success=False,
            )
        except Exception as exc:
            content = f"Unexpected error: {exc}"
            if method in _STATE_CHANGING_METHODS and progress.mutation_response_seen:
                content += _AMBIGUOUS_OUTCOME
            return ToolResult(
                tool_name="http_request",
                content=content,
                success=False,
            )

    @staticmethod
    def _request_following_redirects(
        method: str,
        url: str,
        *,
        headers: dict,
        content: Any,
        timeout: float,
        progress: _RequestProgress,
    ) -> httpx.Response:
        """Issue the request, re-checking SSRF on every redirect hop.

        httpx's built-in ``follow_redirects`` would chase a 30x ``Location``
        without re-validating it, letting a public URL bounce us to an internal
        host. We follow manually and run :func:`check_ssrf` on each target.
        """
        current_url = url
        current_method = method
        body = content
        # Use module-level ``httpx.request`` (not a private Client) so the SSRF
        # re-check seam stays patchable by callers' tests, with redirects
        # disabled so we control every hop ourselves.
        for _ in range(_MAX_REDIRECTS + 1):
            response = httpx.request(
                current_method,
                current_url,
                headers=headers,
                content=body,
                timeout=timeout,
                follow_redirects=False,
            )
            if current_method in _STATE_CHANGING_METHODS:
                progress.mutation_response_seen = True
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            location = response.headers.get("location", "")
            if not location:
                return response
            # Resolve relative redirects against the URL we just fetched.
            current_url = urllib.parse.urljoin(str(response.url), location)
            ssrf_error = check_ssrf(current_url)
            if ssrf_error:
                raise _SSRFRedirectError(ssrf_error)
            # Per RFC 7231, 301/302/303 turn the method into GET and drop
            # the body (except for HEAD).
            if response.status_code in (301, 302, 303) and current_method != "HEAD":
                current_method = "GET"
                body = None
        raise _SSRFRedirectError(f"Exceeded maximum of {_MAX_REDIRECTS} redirects.")


__all__ = ["HttpRequestTool"]
