"""WebSocket authentication tests (issue #217).

The HTTP ``AuthMiddleware`` never sees WebSocket upgrade requests, so the
streaming endpoints (`/v1/chat/stream`, `/v1/agents/events`) must validate the
token themselves in the handshake before accepting the connection.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from openjarvis.core.events import EventBus, EventType  # noqa: E402
from openjarvis.server.api_routes import include_all_routes  # noqa: E402
from openjarvis.server.auth_middleware import websocket_authorized  # noqa: E402
from openjarvis.server.ws_bridge import create_ws_router  # noqa: E402

AUTH_PROTOCOL = "openjarvis.auth.v1"
KEY_PROTOCOL_PREFIX = "openjarvis.key.b64url."


def _auth_subprotocols(api_key: str) -> list[str]:
    encoded = base64.urlsafe_b64encode(api_key.encode()).decode().rstrip("=")
    return [AUTH_PROTOCOL, f"{KEY_PROTOCOL_PREFIX}{encoded}"]


def _ws(query=None, headers=None, subprotocols=None, client_host="203.0.113.10"):
    stub = MagicMock()
    stub.query_params = query or {}
    stub.headers = headers or {}
    stub.scope = {"subprotocols": subprotocols or []}
    stub.client = MagicMock(host=client_host)
    return stub


class TestWebsocketAuthorizedHelper:
    def test_no_key_allows_all(self):
        assert websocket_authorized(_ws(), "") is True

    def test_token_via_bearer_header(self):
        ws = _ws(headers={"authorization": "Bearer sek"})
        assert websocket_authorized(ws, "sek") is True

    @pytest.mark.parametrize(
        "api_key,credential_protocol",
        [
            ("secret+/=", "openjarvis.key.b64url.c2VjcmV0Ky89"),
            ("bearer", "openjarvis.key.b64url.YmVhcmVy"),
            ("sëcret🔑", "openjarvis.key.b64url.c8OrY3JldPCflJE"),
        ],
    )
    def test_token_via_subprotocol(self, api_key, credential_protocol):
        ws = _ws(subprotocols=[AUTH_PROTOCOL, credential_protocol])
        assert websocket_authorized(ws, api_key) is True

    def test_valid_subprotocol_survives_conflicting_authorization(self):
        ws = _ws(
            headers={"authorization": "Bearer proxy-token"},
            subprotocols=_auth_subprotocols("secret+/="),
        )
        assert websocket_authorized(ws, "secret+/=") is True

    def test_valid_authorization_survives_conflicting_subprotocol(self):
        ws = _ws(
            headers={"authorization": "Bearer secret"},
            subprotocols=_auth_subprotocols("wrong"),
        )
        assert websocket_authorized(ws, "secret") is True

    def test_query_token_no_longer_accepted(self):
        # A ?token= query param would leak the key into server access logs;
        # only headers and Sec-WebSocket-Protocol are honored.
        assert websocket_authorized(_ws(query={"token": "sek"}), "sek") is False

    def test_wrong_token_rejected(self):
        ws = _ws(subprotocols=_auth_subprotocols("nope"))
        assert websocket_authorized(ws, "sek") is False

    @pytest.mark.parametrize(
        "subprotocols",
        [
            [AUTH_PROTOCOL],
            [AUTH_PROTOCOL, KEY_PROTOCOL_PREFIX],
            [AUTH_PROTOCOL, AUTH_PROTOCOL],
            [AUTH_PROTOCOL, f"{KEY_PROTOCOL_PREFIX}c2Vr", "extra"],
            ["bearer", "sek"],
        ],
    )
    def test_malformed_subprotocol_offer_rejected(self, subprotocols):
        assert websocket_authorized(_ws(subprotocols=subprotocols), "sek") is False

    def test_missing_token_rejected_when_required(self):
        assert websocket_authorized(_ws(), "sek") is False

    def test_loopback_allowed_when_explicitly_enabled(self):
        ws = _ws(client_host="127.0.0.1")
        assert websocket_authorized(ws, "sek", allow_loopback=True) is True

    def test_loopback_rejected_without_explicit_opt_in(self):
        ws = _ws(client_host="127.0.0.1")
        assert websocket_authorized(ws, "sek") is False

    def test_remote_rejected_when_loopback_is_enabled(self):
        ws = _ws(client_host="203.0.113.10")
        assert websocket_authorized(ws, "sek", allow_loopback=True) is False


def _make_app(api_key=""):
    app = FastAPI()
    engine = MagicMock()
    engine.engine_id = "mock"

    async def mock_stream(messages, *, model="test-model", **kwargs):
        for tok in ["hi"]:
            yield tok

    engine.stream = mock_stream
    app.state.engine = engine
    app.state.model = "test-model"
    app.state.api_key = api_key
    include_all_routes(app)
    return app


class TestChatStreamAuth:
    def test_rejected_without_token_when_key_set(self):
        client = TestClient(_make_app(api_key="secret"))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/chat/stream") as ws:
                ws.receive_text()

    def test_rejected_with_wrong_token(self):
        client = TestClient(_make_app(api_key="secret"))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/v1/chat/stream", subprotocols=_auth_subprotocols("wrong")
            ) as ws:
                ws.receive_text()

    def test_rejected_with_query_token(self):
        # ?token= is no longer an accepted auth channel (would leak into logs).
        client = TestClient(_make_app(api_key="secret"))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/chat/stream?token=secret") as ws:
                ws.receive_text()

    def test_accepted_with_correct_token(self):
        api_key = "secret+/="
        client = TestClient(_make_app(api_key=api_key))
        with client.websocket_connect(
            "/v1/chat/stream", subprotocols=_auth_subprotocols(api_key)
        ) as ws:
            assert ws.accepted_subprotocol == AUTH_PROTOCOL
            ws.send_text(json.dumps({"message": "hi"}))
            assert ws.receive_json()["type"] in ("chunk", "done", "error")

    def test_accepted_with_authorization_header(self):
        client = TestClient(_make_app(api_key="secret"))
        with client.websocket_connect(
            "/v1/chat/stream", headers={"Authorization": "Bearer secret"}
        ) as ws:
            assert ws.accepted_subprotocol is None
            ws.send_text(json.dumps({"message": "hi"}))
            assert ws.receive_json()["type"] in ("chunk", "done", "error")

    def test_allowed_when_no_key_configured(self):
        client = TestClient(_make_app(api_key=""))
        with client.websocket_connect("/v1/chat/stream") as ws:
            ws.send_text(json.dumps({"message": "hi"}))
            assert ws.receive_json()["type"] in ("chunk", "done", "error")

    def test_keyless_server_negotiates_stale_client_auth_protocol(self):
        client = TestClient(_make_app(api_key=""))
        with client.websocket_connect(
            "/v1/chat/stream", subprotocols=_auth_subprotocols("stale")
        ) as ws:
            assert ws.accepted_subprotocol == AUTH_PROTOCOL
            ws.send_text(json.dumps({"message": "hi"}))
            assert ws.receive_json()["type"] in ("chunk", "done", "error")


class _AgentEventsSocket:
    app = SimpleNamespace(state=SimpleNamespace(api_key="secret"))
    query_params = {}
    headers = {}

    def __init__(self, client_host: str):
        self.client = SimpleNamespace(host=client_host)
        self.accepted = False
        self.closed_code = None

    async def accept(self, subprotocol=None):
        self.accepted = True

    async def close(self, code):
        self.closed_code = code

    async def receive(self):
        return {"type": "websocket.disconnect"}


class TestAgentEventsAuth:
    def _app(self, api_key=""):
        app = FastAPI()
        app.state.api_key = api_key
        app.include_router(create_ws_router(EventBus()))
        return app

    def test_rejected_without_token_when_key_set(self):
        async def exercise():
            websocket = _AgentEventsSocket("203.0.113.10")
            endpoint = create_ws_router(EventBus()).routes[0].endpoint
            await asyncio.wait_for(endpoint(websocket), timeout=1)
            assert websocket.accepted is False
            assert websocket.closed_code == 1008

        asyncio.run(exercise())

    def test_loopback_accepted_without_token(self):
        async def exercise():
            websocket = _AgentEventsSocket("127.0.0.1")
            endpoint = create_ws_router(EventBus()).routes[0].endpoint
            await asyncio.wait_for(endpoint(websocket), timeout=1)
            assert websocket.accepted is True
            assert websocket.closed_code is None

        asyncio.run(exercise())

    def test_rejected_with_query_token(self):
        client = TestClient(self._app(api_key="secret"))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/v1/agents/events?token=secret") as ws:
                ws.receive_text()

    def test_accepted_with_correct_token(self):
        bus = EventBus()
        app = FastAPI()
        api_key = "secret+/="
        app.state.api_key = api_key
        app.include_router(create_ws_router(bus))
        client = TestClient(app)
        with client.websocket_connect(
            "/v1/agents/events", subprotocols=_auth_subprotocols(api_key)
        ) as ws:
            assert ws.accepted_subprotocol == AUTH_PROTOCOL
            bus.publish(EventType.AGENT_TICK_START, {"agent_id": "a"})
            assert ws.receive_json()["data"]["agent_id"] == "a"

    def test_accepted_with_authorization_header(self):
        bus = EventBus()
        app = FastAPI()
        app.state.api_key = "secret"
        app.include_router(create_ws_router(bus))
        client = TestClient(app)
        with client.websocket_connect(
            "/v1/agents/events", headers={"Authorization": "Bearer secret"}
        ) as ws:
            assert ws.accepted_subprotocol is None
            bus.publish(EventType.AGENT_TICK_START, {"agent_id": "a"})
            assert ws.receive_json()["data"]["agent_id"] == "a"
