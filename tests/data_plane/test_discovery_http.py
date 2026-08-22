import gzip
from unittest.mock import patch

import brotli
import httpx
import pytest
import respx

from openjarvis.data_plane.discovery_http import DiscoveryHttpClient
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode


class _TwoChunkStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"first"
        yield b"second"


@respx.mock
def test_discovery_http_allows_only_safe_methods_and_rechecks_redirects():
    client = DiscoveryHttpClient(max_response_bytes=1024)
    with pytest.raises(DataPlaneError) as exc:
        client.fetch("https://example.test/orders", method="POST")
    assert exc.value.code is DataPlaneErrorCode.DISCOVERY_UNSAFE_METHOD

    respx.get("https://example.test/").mock(
        return_value=httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/"},
        )
    )
    with patch(
        "openjarvis.data_plane.discovery_http.check_ssrf",
        side_effect=[None, "private"],
    ):
        with pytest.raises(DataPlaneError, match="redirect"):
            client.fetch("https://example.test/")


@respx.mock
def test_discovery_http_requires_https_and_sends_honest_user_agent():
    client = DiscoveryHttpClient(max_response_bytes=1024)
    with pytest.raises(DataPlaneError, match="HTTPS"):
        client.fetch("http://example.test/")

    route = respx.options("https://example.test/").mock(
        return_value=httpx.Response(204)
    )
    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        response = client.fetch("https://example.test/", method="OPTIONS")

    assert response.status_code == 204
    assert route.calls[0].request.headers["user-agent"] == (
        "OpenJarvis/structured-discovery"
    )


def test_discovery_http_rejects_url_credentials_before_network():
    client = DiscoveryHttpClient(max_response_bytes=1024)

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        with pytest.raises(DataPlaneError, match="credentials"):
            client.fetch("https://user:password@example.test/")


@respx.mock
def test_discovery_http_stops_after_five_redirects():
    client = DiscoveryHttpClient(max_response_bytes=1024)
    for index in range(6):
        respx.get(f"https://example.test/{index}").mock(
            return_value=httpx.Response(
                302,
                headers={"location": f"/{index + 1}"},
            )
        )

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        with pytest.raises(DataPlaneError, match="five redirects"):
            client.fetch("https://example.test/0")


@respx.mock
def test_discovery_http_rejects_a_response_over_the_size_cap():
    respx.get("https://example.test/large").mock(
        return_value=httpx.Response(200, content=b"x" * 1025)
    )
    client = DiscoveryHttpClient(max_response_bytes=1024)

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        with pytest.raises(DataPlaneError, match="size limit"):
            client.fetch("https://example.test/large")


def test_discovery_http_checks_total_deadline_while_streaming():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=_TwoChunkStream(), request=request)
    )
    ticks = iter((0.0, 0.5, 1.0))
    client = DiscoveryHttpClient(
        max_response_bytes=1024,
        clock=lambda: next(ticks),
        client=httpx.Client(transport=transport),
    )
    client.set_deadline(1.0)

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        with pytest.raises(DataPlaneError) as exc:
            client.fetch("https://example.test/slow")

    assert exc.value.code is DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED


@respx.mock
def test_retry_after_is_not_slept_past_the_discovery_deadline():
    route = respx.get("https://example.test/rate-limited").mock(
        return_value=httpx.Response(429, headers={"retry-after": "2"})
    )
    clock_values = iter((10.0, 10.0, 10.0))
    sleeps: list[float] = []
    client = DiscoveryHttpClient(
        max_response_bytes=1024,
        clock=lambda: next(clock_values),
        sleep=sleeps.append,
    )
    client.set_deadline(11.0)

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        with pytest.raises(DataPlaneError) as exc:
            client.fetch("https://example.test/rate-limited")

    assert exc.value.code is DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED
    assert sleeps == []
    assert route.call_count == 1


@respx.mock
def test_retry_after_is_honored_within_the_discovery_deadline():
    route = respx.get("https://example.test/rate-limited").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0.5"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    sleeps: list[float] = []
    client = DiscoveryHttpClient(
        max_response_bytes=1024,
        clock=lambda: 10.0,
        sleep=sleeps.append,
    )
    client.set_deadline(11.0)

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        response = client.fetch("https://example.test/rate-limited")

    assert response.status_code == 200
    assert sleeps == [0.5]
    assert route.call_count == 2


@pytest.mark.parametrize(
    "encoding, compress",
    [
        ("br", brotli.compress),
        ("gzip", gzip.compress),
        (None, lambda data: data),
    ],
)
def test_discovery_http_decodes_compressed_bodies(encoding, compress):
    body = b"<html>real page body</html>" * 5
    encoded = compress(body)
    headers = {"content-length": str(len(encoded))}
    if encoding is not None:
        headers["content-encoding"] = encoding

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=encoded, headers=headers, request=request)

    transport = httpx.MockTransport(handler)
    client = DiscoveryHttpClient(
        max_response_bytes=1024,
        client=httpx.Client(transport=transport),
    )

    with patch("openjarvis.data_plane.discovery_http.check_ssrf", return_value=None):
        response = client.fetch("https://example.test/compressed")

    assert response.status_code == 200
    assert response.content == body
