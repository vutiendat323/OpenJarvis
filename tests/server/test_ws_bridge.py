"""Tests for WebSocket event bridge."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from openjarvis.core.events import EventBus, EventType

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def app(event_bus):
    from openjarvis.server.ws_bridge import create_ws_router

    app = FastAPI()
    router = create_ws_router(event_bus)
    app.include_router(router)
    return app


class _FakePresentationManager:
    def __init__(self, replay_payload=None):
        self.replay_payload = replay_payload
        self.connected: list[str] = []
        self.disconnected: list[str] = []

    def replay(self, session_id):
        return self.replay_payload

    def mark_display_connected(self, session_id):
        self.connected.append(session_id)

    def mark_display_disconnected(self, session_id):
        self.disconnected.append(session_id)


class TestWSBridge:
    def test_websocket_receives_events(self, app, event_bus):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events") as ws:
            event_bus.publish(
                EventType.AGENT_TICK_START,
                {
                    "agent_id": "test-123",
                    "agent_name": "test",
                },
            )
            time.sleep(0.05)  # Let call_soon_threadsafe deliver to queue
            data = ws.receive_json()
            assert data["type"] == "agent_tick_start"
            assert data["data"]["agent_id"] == "test-123"

    def test_websocket_filters_by_agent_id(self, app, event_bus):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events?agent_id=agent-A") as ws:
            # This event should NOT be received (different agent)
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-B"})
            # This event SHOULD be received
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-A"})
            time.sleep(0.05)  # Let call_soon_threadsafe deliver to queue
            data = ws.receive_json()
            assert data["data"]["agent_id"] == "agent-A"

    def test_websocket_filters_display_updates_by_presentation_session(self, event_bus):
        from openjarvis.server.ws_bridge import create_ws_router

        app = FastAPI()
        app.include_router(create_ws_router(event_bus))
        client = TestClient(app)
        with (
            client.websocket_connect(
                "/v1/agents/events?presentation_session_id=customer-A"
            ) as customer_a,
            client.websocket_connect(
                "/v1/agents/events?presentation_session_id=customer-B"
            ) as customer_b,
        ):
            event_bus.publish(
                EventType.DISPLAY_UPDATE,
                {"presentation_session_id": "customer-B", "view": "payment"},
            )
            event_bus.publish(
                EventType.DISPLAY_UPDATE,
                {"presentation_session_id": "customer-A", "view": "menu"},
            )
            time.sleep(0.05)

            assert customer_a.receive_json()["data"] == {
                "presentation_session_id": "customer-A",
                "view": "menu",
            }
            assert customer_b.receive_json()["data"] == {
                "presentation_session_id": "customer-B",
                "view": "payment",
            }

    def test_websocket_without_session_does_not_receive_display_updates(
        self, app, event_bus
    ):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events") as websocket:
            event_bus.publish(
                EventType.DISPLAY_UPDATE,
                {"presentation_session_id": "customer-A", "view": "menu"},
            )
            event_bus.publish(EventType.DISPLAY_UPDATE, {"view": "legacy-menu"})
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-A"})
            time.sleep(0.05)

            message = websocket.receive_json()
            assert message["type"] == "agent_tick_start"
            assert message["data"] == {"agent_id": "agent-A"}

    def test_websocket_replays_presentation_state_after_connecting(self, event_bus):
        from openjarvis.server.ws_bridge import create_ws_router

        replay = {
            "view": "menu",
            "presentation_session_id": "customer-A",
            "items": [],
        }
        manager = _FakePresentationManager(replay)
        app = FastAPI()
        app.include_router(create_ws_router(event_bus, presentation_manager=manager))
        client = TestClient(app)

        with client.websocket_connect(
            "/v1/agents/events?presentation_session_id=customer-A"
        ) as websocket:
            message = websocket.receive_json()
            assert message["type"] == "display_update"
            assert message["data"] == replay
            assert manager.connected == ["customer-A"]

        assert manager.disconnected == ["customer-A"]

    def test_agent_filter_does_not_receive_unscoped_display_updates(
        self, app, event_bus
    ):
        client = TestClient(app)
        with client.websocket_connect("/v1/agents/events?agent_id=agent-A") as ws:
            event_bus.publish(EventType.DISPLAY_UPDATE, {"view": "menu"})
            event_bus.publish(EventType.AGENT_TICK_START, {"agent_id": "agent-A"})
            time.sleep(0.05)

            assert ws.receive_json()["data"] == {"agent_id": "agent-A"}

    def test_client_disconnect_stops_handler(self, event_bus):
        async def exercise():
            from openjarvis.server.ws_bridge import create_ws_router

            class FakeWebSocket:
                app = SimpleNamespace(state=SimpleNamespace(api_key=""))
                query_params = {}
                headers = {}

                async def accept(self):
                    pass

                async def receive(self):
                    return {"type": "websocket.disconnect"}

            endpoint = create_ws_router(event_bus).routes[0].endpoint
            await asyncio.wait_for(endpoint(FakeWebSocket()), timeout=1)

        asyncio.run(exercise())

    def test_simultaneous_client_message_does_not_drop_event(self, event_bus):
        async def exercise():
            from openjarvis.server.ws_bridge import create_ws_router

            class FakeWebSocket:
                def __init__(self):
                    self.app = SimpleNamespace(state=SimpleNamespace(api_key=""))
                    self.query_params = {}
                    self.headers = {}
                    self.sent = []
                    self.receive_count = 0
                    self.disconnect = asyncio.Event()

                async def accept(self):
                    pass

                async def receive(self):
                    self.receive_count += 1
                    if self.receive_count == 1:
                        event_bus.publish(
                            EventType.AGENT_TICK_START, {"agent_id": "not-dropped"}
                        )
                        return {"type": "websocket.receive", "text": "client message"}
                    await self.disconnect.wait()
                    return {"type": "websocket.disconnect"}

                async def send_json(self, payload):
                    self.sent.append(payload)
                    self.disconnect.set()

            websocket = FakeWebSocket()
            endpoint = create_ws_router(event_bus).routes[0].endpoint

            await asyncio.wait_for(endpoint(websocket), timeout=1)

            assert websocket.sent[0]["data"]["agent_id"] == "not-dropped"

        asyncio.run(exercise())

    def test_kiosk_state_event_is_forwarded(self, event_bus):
        async def exercise():
            from openjarvis.server.ws_bridge import create_ws_router

            class FakeWebSocket:
                def __init__(self):
                    self.app = SimpleNamespace(state=SimpleNamespace(api_key=""))
                    self.query_params = {}
                    self.headers = {}
                    self.sent = []
                    self.receive_count = 0
                    self.disconnect = asyncio.Event()

                async def accept(self):
                    pass

                async def receive(self):
                    self.receive_count += 1
                    if self.receive_count == 1:
                        event_bus.publish(
                            EventType.KIOSK_STATE_CHANGED,
                            {"state": "prompting", "mic_enabled": False},
                        )
                        return {"type": "websocket.receive", "text": "client message"}
                    await self.disconnect.wait()
                    return {"type": "websocket.disconnect"}

                async def send_json(self, payload):
                    self.sent.append(payload)
                    self.disconnect.set()

            websocket = FakeWebSocket()
            endpoint = create_ws_router(event_bus).routes[0].endpoint

            await asyncio.wait_for(endpoint(websocket), timeout=1)

            assert websocket.sent[0]["type"] == "kiosk_state_changed"
            assert websocket.sent[0]["data"] == {
                "state": "prompting",
                "mic_enabled": False,
            }

        asyncio.run(exercise())

    def test_app_routes_bridge_uses_injected_event_bus(self):
        async def exercise():
            from openjarvis.server.api_routes import include_all_routes

            event_bus = EventBus()
            app = FastAPI()
            app.state.api_key = ""
            app.state.bus = event_bus
            include_all_routes(app)
            endpoint = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/v1/agents/events"
            )

            class FakeWebSocket:
                def __init__(self):
                    self.app = app
                    self.query_params = {}
                    self.headers = {}
                    self.sent = []
                    self.receive_count = 0
                    self.disconnect = asyncio.Event()

                async def accept(self):
                    pass

                async def receive(self):
                    self.receive_count += 1
                    if self.receive_count == 1:
                        event_bus.publish(
                            EventType.KIOSK_STATE_CHANGED,
                            {"state": "prompting", "mic_enabled": False},
                        )
                        return {"type": "websocket.receive", "text": "client message"}
                    await self.disconnect.wait()
                    return {"type": "websocket.disconnect"}

                async def send_json(self, payload):
                    self.sent.append(payload)
                    self.disconnect.set()

            websocket = FakeWebSocket()
            await asyncio.wait_for(endpoint(websocket), timeout=1)

            assert websocket.sent[0]["type"] == "kiosk_state_changed"

        asyncio.run(exercise())

    def test_app_routes_bridge_replays_injected_presentation_manager(self):
        async def exercise():
            from openjarvis.server.api_routes import include_all_routes

            event_bus = EventBus()
            manager = _FakePresentationManager(
                {
                    "view": "menu",
                    "presentation_session_id": "customer-A",
                    "items": [],
                }
            )
            app = FastAPI()
            app.state.api_key = ""
            app.state.bus = event_bus
            app.state.presentation_session_manager = manager
            include_all_routes(app)
            endpoint = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/v1/agents/events"
            )

            class FakeWebSocket:
                def __init__(self):
                    self.app = app
                    self.query_params = {"presentation_session_id": "customer-A"}
                    self.headers = {}
                    self.sent = []
                    self.receive_count = 0
                    self.disconnect = asyncio.Event()

                async def accept(self):
                    pass

                async def receive(self):
                    self.receive_count += 1
                    if self.receive_count == 1:
                        event_bus.publish(
                            EventType.DISPLAY_UPDATE,
                            {
                                "view": "live",
                                "presentation_session_id": "customer-A",
                            },
                        )
                        return {"type": "websocket.receive", "text": "ready"}
                    await self.disconnect.wait()
                    return {"type": "websocket.disconnect"}

                async def send_json(self, payload):
                    self.sent.append(payload)
                    self.disconnect.set()

            websocket = FakeWebSocket()
            await asyncio.wait_for(endpoint(websocket), timeout=1)

            assert websocket.sent[0]["data"] == {
                "view": "menu",
                "presentation_session_id": "customer-A",
                "items": [],
            }
            assert manager.connected == ["customer-A"]
            assert manager.disconnected == ["customer-A"]

        asyncio.run(exercise())

    def test_cancelling_handler_cleans_up_child_tasks(self, event_bus):
        async def exercise():
            from openjarvis.server.ws_bridge import create_ws_router

            class FakeWebSocket:
                def __init__(self):
                    self.app = SimpleNamespace(state=SimpleNamespace(api_key=""))
                    self.query_params = {}
                    self.headers = {}
                    self.receiving = asyncio.Event()
                    self.receive_cancelled = asyncio.Event()

                async def accept(self):
                    pass

                async def receive(self):
                    self.receiving.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        self.receive_cancelled.set()

            websocket = FakeWebSocket()
            endpoint = create_ws_router(event_bus).routes[0].endpoint
            handler = asyncio.create_task(endpoint(websocket))
            await websocket.receiving.wait()

            handler.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handler

            assert websocket.receive_cancelled.is_set()
            assert not [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task() and not task.done()
            ]

        asyncio.run(exercise())
