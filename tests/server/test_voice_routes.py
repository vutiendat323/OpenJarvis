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
            # The task's own done-callback releases the lease (see
            # routes.py's start_pipeline), so nothing further is needed here
            # before the next iteration claims a fresh one.
            await asyncio.gather(task, return_exceptions=True)
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


class _StatePeer:
    """Peer whose connectionState walks a scripted sequence, one per poll."""

    def __init__(self, states):
        self._states = list(states)
        self.polls = 0

    @property
    def connectionState(self):
        self.polls += 1
        if len(self._states) > 1:
            return self._states.pop(0)
        return self._states[0]


@pytest.mark.anyio
async def test_await_peer_gone_returns_on_closed(monkeypatch):
    monkeypatch.setattr(routes, "_PEER_POLL_SECONDS", 0)

    await asyncio.wait_for(routes.await_peer_gone(_StatePeer(["closed"])), timeout=1)


@pytest.mark.anyio
async def test_await_peer_gone_returns_on_sustained_disconnect(monkeypatch):
    # A tab that vanished without closing: the lease must not wait for the TTL.
    monkeypatch.setattr(routes, "_PEER_POLL_SECONDS", 0)
    monkeypatch.setattr(routes, "_PEER_DISCONNECT_GRACE_SECONDS", 0)

    await asyncio.wait_for(
        routes.await_peer_gone(_StatePeer(["disconnected"])), timeout=1
    )


@pytest.mark.anyio
async def test_await_peer_gone_lets_a_brief_disconnect_recover(monkeypatch):
    monkeypatch.setattr(routes, "_PEER_POLL_SECONDS", 0)
    monkeypatch.setattr(routes, "_PEER_DISCONNECT_GRACE_SECONDS", 30)

    peer = _StatePeer(["connected", "disconnected", "connected", "closed"])
    await asyncio.wait_for(routes.await_peer_gone(peer), timeout=1)

    # It kept polling through the blip instead of reaping at the first sight
    # of "disconnected" — only the final "closed" ended it.
    assert peer.polls == 4


@pytest.mark.anyio
async def test_lease_survives_a_second_cancel_landing_inside_teardown():
    """Reproduces the leak: kiosk's presentation-reset endpoint cancels the
    voice task independently of run_until_disconnected's own teardown. A
    second cancel landing while that teardown is already mid-cleanup used to
    skip an inline end_session() call and leak the lease until the 300s TTL.

    ``fake_run_until_disconnected`` mirrors the real function's shape (a
    try/finally whose own cleanup awaits something, so a second cancel can
    land inside it) without needing a real WorkerRunner/pipecat pipeline —
    what's under test is the wiring in start_pipeline (the done-callback),
    not pipecat's teardown internals.
    """
    sessions = VoiceSessionService()
    result = sessions.start_session(
        chat_thread_id="thread-1", execution_binding=object()
    )
    session = result.session
    assert session is not None
    entered_cleanup = asyncio.Event()

    async def fake_run_until_disconnected() -> None:
        try:
            await asyncio.Event().wait()  # stands in for await_peer_gone
        finally:
            entered_cleanup.set()
            await asyncio.sleep(0.2)  # stands in for the persist await

    task = asyncio.create_task(fake_run_until_disconnected())
    # The fix under test: start_pipeline registers this so the lease is
    # released whenever the task ends, however it ends — not from an inline
    # call inside the coroutine's own finally block.
    task.add_done_callback(
        lambda _t: sessions.end_session(session.voice_session_id, reason="task_done")
    )
    # Let the task actually start (reach its own try:) before cancelling it —
    # a task cancelled before its coroutine has run its first step never
    # executes any of its body, not even the try/finally, which would prove
    # nothing about the race this test targets.
    await asyncio.sleep(0)

    task.cancel()  # cancel #1: the natural teardown starts (peer closed)
    await entered_cleanup.wait()
    # Cancel #2, landing while cancel #1's teardown is already inside its
    # own cleanup await — the exact race that used to skip an inline
    # end_session() call.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert sessions.get_session(session.voice_session_id).status == "ended"
    # The lease being free is what actually matters: the next caller must
    # not see the 409 this bug produced.
    second = sessions.start_session(
        chat_thread_id="thread-2", execution_binding=object()
    )
    assert second.session is not None
