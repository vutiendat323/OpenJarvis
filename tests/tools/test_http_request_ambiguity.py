"""A state-changing request that may have landed says so."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from openjarvis.tools.http_request import HttpRequestTool

_URL = "https://trendcoffee.net/api/latest/orders/public"
_AMBIGUOUS = "may already have been applied"


@pytest.fixture(autouse=True)
def _force_httpx_fallback():
    """Patch the Rust bridge so no test in this file can reach the network.

    Mirrors tests/tools/test_http_request.py's fixture of the same name: the
    Rust HttpRequestTool().execute() raises, forcing the httpx code path
    where this file's ``httpx.request`` monkeypatching actually bites. Also
    stubs ``check_ssrf`` -- ``openjarvis.security.ssrf.check_ssrf`` calls
    ``get_rust_module().check_ssrf(url)`` whenever the real Rust extension is
    compiled, ahead of any method routing, so a stub lacking it would break
    the SSRF guard before the code under test ever runs.
    """
    mock_rust = MagicMock()
    mock_rust.HttpRequestTool.return_value.execute.side_effect = RuntimeError(
        "mocked out"
    )
    mock_rust.check_ssrf.return_value = None
    with patch(
        "openjarvis._rust_bridge.get_rust_module",
        return_value=mock_rust,
    ):
        yield


def _raise(monkeypatch, exc: Exception) -> None:
    def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(httpx, "request", _boom)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.WriteTimeout("write timed out"),
        httpx.RemoteProtocolError("peer closed"),
    ],
)
def test_post_that_may_have_landed_warns(monkeypatch, exc):
    _raise(monkeypatch, exc)

    result = HttpRequestTool().execute(url=_URL, method="POST", body="{}")

    assert result.success is False
    assert _AMBIGUOUS in result.content


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("refused"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.PoolTimeout("pool timed out"),
    ],
)
def test_post_that_never_reached_the_server_does_not_warn(monkeypatch, exc):
    _raise(monkeypatch, exc)

    result = HttpRequestTool().execute(url=_URL, method="POST", body="{}")

    assert result.success is False
    assert _AMBIGUOUS not in result.content


def test_a_read_never_warns(monkeypatch):
    _raise(monkeypatch, httpx.ReadTimeout("read timed out"))

    result = HttpRequestTool().execute(url=_URL, method="GET")

    assert result.success is False
    assert _AMBIGUOUS not in result.content


def test_a_post_never_takes_the_rust_path(monkeypatch):
    """A Rust failure after the request left would be re-sent by httpx."""
    calls: list[str] = []

    class _Rust:
        class HttpRequestTool:
            def execute(self, url, method="GET", body=None):
                calls.append(method)
                raise RuntimeError("boom after the request left")

        def check_ssrf(self, url):
            return None

    monkeypatch.setattr(
        "openjarvis._rust_bridge.get_rust_module", lambda: _Rust(), raising=False
    )
    _raise(monkeypatch, httpx.ConnectError("refused"))

    HttpRequestTool().execute(url=_URL, method="POST", body="{}")

    assert calls == []


def test_the_rust_path_does_not_claim_a_status_it_cannot_know(monkeypatch):
    class _Rust:
        class HttpRequestTool:
            def execute(self, url, method="GET", body=None):
                return '{"ok": true}'

        def check_ssrf(self, url):
            return None

    monkeypatch.setattr(
        "openjarvis._rust_bridge.get_rust_module", lambda: _Rust(), raising=False
    )

    result = HttpRequestTool().execute(url=_URL, method="GET")

    assert result.success is True
    assert result.metadata["status_code"] is None


def test_post_to_blocked_redirect_warns_that_outcome_is_ambiguous(monkeypatch):
    response = httpx.Response(
        302,
        headers={"location": "http://169.254.169.254/latest/"},
        request=httpx.Request("POST", _URL),
    )
    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        "openjarvis.tools.http_request.check_ssrf",
        MagicMock(side_effect=[None, "blocked metadata host"]),
    )

    result = HttpRequestTool().execute(url=_URL, method="POST", body="{}")

    assert result.success is False
    assert "blocked redirect" in result.content
    assert _AMBIGUOUS in result.content


def test_post_redirect_then_connect_error_warns_that_outcome_is_ambiguous(
    monkeypatch,
):
    responses: list[httpx.Response | Exception] = [
        httpx.Response(
            302,
            headers={"location": "https://trendcoffee.net/order-status"},
            request=httpx.Request("POST", _URL),
        ),
        httpx.ConnectError("redirect target refused connection"),
    ]

    def request(*args, **kwargs):
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx, "request", request)
    monkeypatch.setattr(
        "openjarvis.tools.http_request.check_ssrf", MagicMock(return_value=None)
    )

    result = HttpRequestTool().execute(url=_URL, method="POST", body="{}")

    assert result.success is False
    assert "Request error" in result.content
    assert _AMBIGUOUS in result.content


def test_post_redirect_loop_at_max_depth_warns_that_outcome_is_ambiguous(
    monkeypatch,
):
    def redirect(method, url, **kwargs):
        return httpx.Response(
            307,
            headers={"location": "/again"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(httpx, "request", redirect)
    monkeypatch.setattr(
        "openjarvis.tools.http_request.check_ssrf", MagicMock(return_value=None)
    )

    result = HttpRequestTool().execute(url=_URL, method="POST", body="{}")

    assert result.success is False
    assert "maximum" in result.content
    assert _AMBIGUOUS in result.content
