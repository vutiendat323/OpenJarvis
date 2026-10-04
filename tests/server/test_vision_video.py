"""Remote camera over WebRTC, video only, opened when a kiosk page loads with
``?camera=remote`` -- before any voice session, so the remote picture can
drive presence and the kiosk FSM.

The backend answers the page's offer, decodes each frame, stamps it with this
server's clock (the clock audio spans use) and relays it to Vision, newest
only.  The peer connection ending closes the Vision socket, and Vision falls
straight back to the C920.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI

from openjarvis.kiosk import remote_camera, remote_camera_routes
from openjarvis.server.voice.vision_video import VisionVideoBridge

aiortc = pytest.importorskip("aiortc")

JPEG = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"


# -- the loopback relay to Vision -------------------------------------------


class _WS:
    def __init__(self):
        self.sent = []
        self.closed = False
        self._gone = asyncio.Event()

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True
        self._gone.set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._gone.wait()
        raise StopAsyncIteration


async def _wait(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        await asyncio.sleep(0.02)


@pytest.mark.anyio
async def test_bridge_selects_remote_then_sends_only_the_newest_frame():
    ws = _WS()
    bridge = VisionVideoBridge("ws://vision", "page-1", connect=lambda url: ws)
    await bridge.start()
    await _wait(lambda: ws.sent)

    start = json.loads(ws.sent[0])
    assert start["cmd"] == "video_start" and start["session_id"] == "page-1"

    bridge.offer(b"old", 1.0)
    bridge.offer(JPEG, 2.0)  # replaces the unsent one
    await _wait(lambda: len(ws.sent) >= 2)
    await asyncio.sleep(0.05)

    assert [m for m in ws.sent if isinstance(m, bytes)] == [
        struct.pack("<d", 2.0) + JPEG
    ]
    await bridge.close()
    assert ws.closed  # Vision releases the remote camera on disconnect


# -- the WebRTC endpoint ----------------------------------------------------


class _FakeBridge:
    instances: list["_FakeBridge"] = []

    def __init__(self, url, session_id):
        self.url = url
        self.offered = []
        self.started = self.closed = False
        _FakeBridge.instances.append(self)

    async def start(self):
        self.started = True

    def offer(self, jpeg, timestamp):
        self.offered.append((jpeg, timestamp))

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_bridge(monkeypatch):
    _FakeBridge.instances.clear()
    monkeypatch.setattr(remote_camera, "VisionVideoBridge", _FakeBridge)


def _app(vision=True):
    app = FastAPI()
    app.include_router(remote_camera_routes.router)
    app.state.vision_client = MagicMock(url="ws://vision:9876") if vision else None
    return app


async def _post(app, body):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/kiosk/remote-camera/offer", json=body)


async def _camera_offer():
    from aiortc import RTCPeerConnection
    from aiortc.mediastreams import VideoStreamTrack

    page = RTCPeerConnection()
    page.addTrack(VideoStreamTrack())  # a synthetic 640x480 camera
    await page.setLocalDescription(await page.createOffer())
    return page


@pytest.mark.anyio
async def test_a_page_camera_reaches_vision_and_its_end_hands_back_the_c920():
    from aiortc import RTCSessionDescription

    app = _app()
    page = await _camera_offer()
    before = time.time()
    try:
        reply = await _post(
            app, {"sdp": page.localDescription.sdp, "type": page.localDescription.type}
        )
        assert reply.status_code == 200
        await page.setRemoteDescription(RTCSessionDescription(**reply.json()))

        [bridge] = _FakeBridge.instances
        assert bridge.url == "ws://vision:9876" and bridge.started
        await _wait(lambda: len(bridge.offered) >= 3, seconds=15.0)
        jpeg, ts = bridge.offered[0]
        assert jpeg.startswith(b"\xff\xd8") and before <= ts <= time.time()
    finally:
        await page.close()

    await _wait(lambda: _FakeBridge.instances[0].closed, seconds=15.0)
    await app.state.remote_camera.close()


@pytest.mark.anyio
async def test_a_new_page_replaces_the_previous_remote_camera():
    app = _app()
    first = await _camera_offer()
    second = await _camera_offer()
    try:
        for page in (first, second):
            # No candidates: replacement is under test, not connectivity (and
            # closing a peer mid-ICE races aioice's socket teardown).
            sdp = "\r\n".join(
                line
                for line in page.localDescription.sdp.split("\r\n")
                if not line.startswith("a=candidate")
            )
            reply = await _post(app, {"sdp": sdp, "type": "offer"})
            assert reply.status_code == 200
        assert [b.closed for b in _FakeBridge.instances] == [True, False]
    finally:
        await first.close()
        await second.close()
        await app.state.remote_camera.close()


@pytest.mark.anyio
async def test_an_offer_without_video_is_rejected():
    from aiortc import RTCPeerConnection

    page = RTCPeerConnection()
    page.createDataChannel("not-a-camera")
    await page.setLocalDescription(await page.createOffer())
    try:
        reply = await _post(_app(), {"sdp": page.localDescription.sdp, "type": "offer"})
    finally:
        await page.close()
    assert reply.status_code == 400
    assert all(b.closed for b in _FakeBridge.instances)


@pytest.mark.anyio
async def test_without_vision_the_offer_is_refused():
    reply = await _post(_app(vision=False), {"sdp": "v=0", "type": "offer"})
    assert reply.status_code == 503
    assert _FakeBridge.instances == []
