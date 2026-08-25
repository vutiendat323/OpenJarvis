"""Focused protocol tests for the Pipecat WebRTC routes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi", reason="openjarvis[server] not installed")
pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from openjarvis.core.conversation import current_conversation_id
from openjarvis.server.voice import routes
from openjarvis.server.voice.session import VoiceSessionService


def test_voice_offer_patch_forwards_trickle_ice_candidates(monkeypatch):
    captured = []

    class FakeHandler:
        async def handle_patch_request(self, request):
            captured.append(request)

    monkeypatch.setattr(routes, "_handler", lambda _request: FakeHandler())
    app = FastAPI()
    app.include_router(routes.router)

    response = TestClient(app).patch(
        "/api/voice/webrtc/offer",
        json={
            "pc_id": "peer-1",
            "candidates": [
                {
                    "candidate": "candidate:1 1 UDP 1 127.0.0.1 5000 typ host",
                    "sdp_mid": "0",
                    "sdp_mline_index": 0,
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    assert captured[0].pc_id == "peer-1"
    assert captured[0].candidates[0].sdp_mid == "0"


@pytest.mark.anyio
async def test_voice_pipeline_scope_uses_distinct_server_ids_for_raw_client_ids(
    monkeypatch,
):
    seen_scopes: list[str] = []
    sessions = VoiceSessionService()
    app = FastAPI()
    app.state.voice_session_service = sessions
    app.state.native_agent_runtime = SimpleNamespace(bind=lambda model: object())
    app.state.model = "test-model"
    app.state.config = None
    app.state.memory_backend = None

    class Context:
        def get_messages(self):
            return []

    class Peer:
        connectionState = "connected"

        def on(self, event, callback):
            return None

    connection = SimpleNamespace(pc=Peer())

    class Handler:
        async def handle_web_request(self, request, webrtc_connection_callback):
            await webrtc_connection_callback(connection)
            task = app.state.pipecat_voice_task
            seen_scopes.append(task.get_context().run(current_conversation_id))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            active = next(
                session
                for session in sessions._sessions.values()
                if session.status != "ended"
            )
            sessions.end_session(active.voice_session_id, reason="test_complete")
            return {"sdp": "answer", "type": "answer"}

    async def renderer():
        return object()

    monkeypatch.setattr(routes, "_handler", lambda request: Handler())
    monkeypatch.setattr(routes, "_renderer", renderer)
    monkeypatch.setattr(routes, "_transcriber", lambda: object())
    monkeypatch.setattr(
        routes, "build_voice_pipeline", lambda **kwargs: (object(), Context())
    )

    request = Request({"type": "http", "app": app, "headers": []})
    for raw_chat_id in ("", "attacker\x00scope"):
        body = routes.WebRTCOfferRequest(
            sdp="offer", type="offer", requestData={"chat_thread_id": raw_chat_id}
        )
        await routes.voice_webrtc_offer(body, request)

    assert len(seen_scopes) == 2
    assert len(set(seen_scopes)) == 2
    assert set(seen_scopes) == set(sessions._sessions)
    assert [session.chat_thread_id for session in sessions._sessions.values()] == [
        "",
        "attacker\x00scope",
    ]
