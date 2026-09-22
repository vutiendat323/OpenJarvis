"""Browser WebSocket contract: one authenticated Human controls one page."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openjarvis.core.config import JarvisConfig
from openjarvis.server.app import create_app


class _Bridge:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def subscribe(self):
        yield {"type": "state", "url": self.url}
        while True:
            yield await self.events.get()

    async def handle(self, command: dict) -> None:
        if command["type"] == "navigate":
            self.url = command["url"]
            await self.events.put({"type": "state", "url": self.url})


def test_browser_socket_streams_state_and_forwards_navigation() -> None:
    from openjarvis.kiosk.browser_routes import create_browser_router

    bridge = _Bridge()
    app = FastAPI()
    app.state.api_key = ""
    app.include_router(create_browser_router(bridge))

    with TestClient(app).websocket_connect("/api/kiosk/browser/ws") as ws:
        assert ws.receive_json() == {"type": "state", "url": "about:blank"}
        ws.send_json({"type": "navigate", "url": "https://example.test/"})
        assert ws.receive_json() == {
            "type": "state",
            "url": "https://example.test/",
        }


def test_browser_socket_rejects_missing_api_key() -> None:
    from openjarvis.kiosk.browser_routes import create_browser_router

    app = FastAPI()
    app.state.api_key = "test-secret"
    app.include_router(create_browser_router(_Bridge()))

    with pytest.raises(WebSocketDisconnect) as denied:
        with TestClient(app).websocket_connect("/api/kiosk/browser/ws"):
            pass
    assert denied.value.code == 1008

    with TestClient(app).websocket_connect(
        "/api/kiosk/browser/ws?token=test-secret"
    ) as ws:
        assert ws.receive_json()["url"] == "about:blank"


def test_main_app_mounts_browser_socket_when_shared_browser_is_supplied() -> None:
    bridge = _Bridge()
    config = JarvisConfig()
    config.analytics.enabled = False
    app = create_app(
        engine=None, model="test-model", shared_browser=bridge, config=config
    )

    with TestClient(app) as client:
        assert bridge.connected is True
        with client.websocket_connect("/api/kiosk/browser/ws") as ws:
            assert ws.receive_json() == {"type": "state", "url": "about:blank"}
    assert bridge.closed is True


def test_browser_socket_rejects_unrelated_website_origin():
    from openjarvis.kiosk.browser_routes import create_browser_router

    app = FastAPI()
    app.state.api_key = ""
    app.include_router(create_browser_router(_Bridge()))
    with pytest.raises(WebSocketDisconnect) as denied:
        with TestClient(app).websocket_connect(
            "/api/kiosk/browser/ws", headers={"origin": "https://unrelated.test"}
        ):
            pass
    assert denied.value.code == 1008


def test_loading_navigation_does_not_block_following_input():
    from openjarvis.kiosk.browser_routes import create_browser_router

    class SlowBridge(_Bridge):
        async def handle(self, command):
            if command["type"] == "navigate":
                await asyncio.Event().wait()
            await self.events.put({"type": "input_received"})

    app = FastAPI()
    app.state.api_key = ""
    app.include_router(create_browser_router(SlowBridge()))
    with TestClient(app).websocket_connect("/api/kiosk/browser/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "navigate", "url": "https://slow.test"})
        ws.send_json({"type": "text", "text": "still responsive"})
        assert ws.receive_json() == {"type": "input_received"}
