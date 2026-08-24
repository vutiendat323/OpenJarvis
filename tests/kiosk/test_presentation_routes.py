"""HTTP contract for kiosk customer-display presentation sessions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import openjarvis.kiosk.routes as kiosk_routes
from openjarvis.core.events import EventBus
from openjarvis.kiosk.presentation import (
    PresentationSessionManager,
    PresentationUnavailableError,
)
from openjarvis.server.app import create_app


@dataclass
class _FakePresentationManager:
    session_id: str = "session-1"
    ensure_error: Exception | None = None
    ensure_origins: list[str] = field(default_factory=list)

    def ensure(self, display_origin: str):
        self.ensure_origins.append(display_origin)
        if self.ensure_error is not None:
            raise self.ensure_error
        return SimpleNamespace(session_id=self.session_id)

    def reset(self, session_id: str) -> bool:
        return session_id == self.session_id

    def replay(self, session_id: str):
        return {"view": "none"} if session_id == self.session_id else None


@pytest.fixture
def manager() -> _FakePresentationManager:
    return _FakePresentationManager()


@pytest.fixture
def client(manager: _FakePresentationManager) -> TestClient:
    app = FastAPI()
    app.state.presentation_session_manager = manager
    app.include_router(kiosk_routes.router)
    return TestClient(app)


def test_ensure_returns_one_session_and_passes_the_frontend_origin(
    client: TestClient, manager: _FakePresentationManager
) -> None:
    response = client.post(
        "/api/kiosk/presentation/ensure",
        json={"display_origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 200
    assert response.json() == {"presentation_session_id": "session-1"}
    assert manager.ensure_origins == ["http://127.0.0.1:5173"]


def test_ensure_awaits_the_threadpool_for_blocking_mcp_work(
    client: TestClient,
    manager: _FakePresentationManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin = "http://127.0.0.1:5173"
    blocking_ensure = Mock(
        side_effect=AssertionError("blocking ensure ran on the ASGI event loop")
    )
    manager.ensure = blocking_ensure
    run_in_threadpool = AsyncMock(
        return_value=SimpleNamespace(session_id="threadpool-session")
    )
    monkeypatch.setattr(
        kiosk_routes,
        "run_in_threadpool",
        run_in_threadpool,
        raising=False,
    )

    response = client.post(
        "/api/kiosk/presentation/ensure",
        json={"display_origin": origin},
    )

    assert response.status_code == 200
    assert response.json() == {"presentation_session_id": "threadpool-session"}
    run_in_threadpool.assert_awaited_once_with(blocking_ensure, origin)
    blocking_ensure.assert_not_called()


def test_ensure_returns_503_when_presentation_is_unavailable(
    client: TestClient,
) -> None:
    client.app.state.presentation_session_manager = PresentationSessionManager(
        EventBus(), None
    )

    response = client.post(
        "/api/kiosk/presentation/ensure",
        json={"display_origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "presentation_unavailable"}


def test_ensure_returns_503_when_manager_reports_an_mcp_failure(
    client: TestClient, manager: _FakePresentationManager
) -> None:
    manager.ensure_error = PresentationUnavailableError("browser_navigate unavailable")

    response = client.post(
        "/api/kiosk/presentation/ensure",
        json={"display_origin": "http://127.0.0.1:5173"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "presentation_unavailable"}


def test_ensure_returns_400_for_a_malformed_origin(
    client: TestClient, manager: _FakePresentationManager
) -> None:
    manager.ensure_error = ValueError("invalid origin")

    response = client.post(
        "/api/kiosk/presentation/ensure",
        json={"display_origin": "not-an-origin"},
    )

    assert response.status_code == 400


def test_reset_returns_404_for_another_session(client: TestClient) -> None:
    response = client.post("/api/kiosk/presentation/other-session/reset")

    assert response.status_code == 404


def test_reset_returns_ok_for_the_active_session(client: TestClient) -> None:
    response = client.post("/api/kiosk/presentation/session-1/reset")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_create_app_stores_the_composed_presentation_manager(
    manager: _FakePresentationManager,
) -> None:
    app = create_app(
        engine=None,
        model="test-model",
        presentation_session_manager=manager,
    )

    assert app.state.presentation_session_manager is manager


@pytest.mark.asyncio
async def test_reset_drains_a_late_voice_publication_before_clearing_replay() -> None:
    bus = EventBus(record_history=True)
    mcp_client = Mock()
    mcp_client._server_name = "playwright"
    mcp_client.call_tool.return_value = {"content": []}
    presentation = PresentationSessionManager(bus, mcp_client)
    session = presentation.ensure("http://127.0.0.1:5173")
    presentation.publish({"view": "cart", "lines": [], "total": 0})

    async def active_voice_turn() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            presentation.publish({"view": "menu", "items": [{"id": "late"}]})

    voice_task = asyncio.create_task(active_voice_turn())
    await asyncio.sleep(0)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                pipecat_voice_task=voice_task,
                presentation_session_manager=presentation,
            )
        )
    )

    try:
        response = await kiosk_routes.reset_presentation(session.session_id, request)
    finally:
        if not voice_task.done():
            voice_task.cancel()
            await asyncio.gather(voice_task, return_exceptions=True)

    assert response == {"ok": True}
    assert presentation.replay(session.session_id) == {
        "view": "none",
        "presentation_session_id": session.session_id,
    }
    assert bus.history[-1].data["view"] == "none"


@pytest.mark.asyncio
async def test_reset_for_another_session_does_not_cancel_the_active_voice_task(
    manager: _FakePresentationManager,
) -> None:
    voice_task = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                pipecat_voice_task=voice_task,
                presentation_session_manager=manager,
            )
        )
    )

    try:
        with pytest.raises(HTTPException) as exc_info:
            await kiosk_routes.reset_presentation("other-session", request)
        assert exc_info.value.status_code == 404
        assert not voice_task.done()
    finally:
        voice_task.cancel()
        await asyncio.gather(voice_task, return_exceptions=True)
