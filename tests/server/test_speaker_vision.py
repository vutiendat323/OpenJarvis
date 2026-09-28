"""Session audio mirrored to Vision through its dedicated WebSocket protocol."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager

import pytest

from openjarvis.server.voice.speaker import FaceTrackBuffer, load_speaker_settings
from openjarvis.server.voice.speaker_vision import VisionAudioBridge


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.inbound = asyncio.Queue()

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.inbound.get()
        if value is None:
            raise StopAsyncIteration
        return json.dumps(value)

    def status(self, stream_id, status):
        self.inbound.put_nowait(
            {"event": "asd_audio_status", "stream_id": stream_id, "status": status}
        )


async def eventually(check):
    for _ in range(100):
        if check():
            return
        await asyncio.sleep(0.01)
    assert check()


@pytest.mark.anyio
async def test_bridge_packetizes_actual_samples_and_stops(monkeypatch):
    import websockets

    socket = FakeSocket()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    clock = iter((1000.04, 1000.12, 1000.20))
    monkeypatch.setattr(
        "openjarvis.server.voice.speaker_vision._now", lambda: next(clock)
    )
    faces = FaceTrackBuffer()
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    await bridge.start()
    await eventually(lambda: bool(socket.sent))
    start = socket.sent[0]
    assert start == {
        "cmd": "asd_audio_start",
        "version": 1,
        "session_id": "voice-1",
        "stream_id": start["stream_id"],
        "sample_rate": 16000,
        "channels": 1,
    }
    socket.inbound.put_nowait({"event": "person_near"})
    socket.status(start["stream_id"], "ready")
    await eventually(lambda: bridge.ready)
    first = bridge.offer(b"a" * 1280, 16000, 1)
    bridge.offer(b"b" * 2560, 16000, 1)
    bridge.offer(b"c" * 1280, 16000, 1)
    await eventually(lambda: len(socket.sent) == 3)
    packets = socket.sent[1:]
    assert [p["cmd"] for p in packets] == ["asd_audio", "asd_audio"]
    assert [len(base64.b64decode(p["pcm16_b64"])) for p in packets] == [2560, 2560]
    assert packets[1]["seq"] == packets[0]["seq"] + 1
    assert packets[1]["t0"] - packets[0]["t0"] == pytest.approx(0.08)
    assert first.start == pytest.approx(1000.0)
    assert first.end == pytest.approx(1000.04)
    assert first.stream_id == start["stream_id"]
    assert bridge.audio_seconds == pytest.approx(0.16)
    await bridge.close()
    assert socket.sent[-1] == {"cmd": "asd_audio_stop", "stream_id": start["stream_id"]}
    await bridge.close()
    assert not bridge.ready


@pytest.mark.anyio
async def test_disabled_status_ends_attempts_for_session(monkeypatch):
    import websockets

    sockets = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = FakeSocket()
        sockets.append(socket)
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(sockets and sockets[0].sent))
    sockets[0].status(sockets[0].sent[0]["stream_id"], "disabled")
    await asyncio.sleep(0.02)
    bridge.offer(b"a" * 2560, 16000, 1)
    assert len(sockets) == 1
    assert not bridge.ready
    await bridge.close()


@pytest.mark.parametrize("status", ["unavailable", "invalid"])
@pytest.mark.anyio
async def test_other_terminal_statuses_end_attempts(monkeypatch, status):
    import websockets

    sockets = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = FakeSocket()
        sockets.append(socket)
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(sockets and sockets[0].sent))
    sockets[0].status(sockets[0].sent[0]["stream_id"], status)
    await asyncio.sleep(0.02)
    assert len(sockets) == 1
    await bridge.close()


@pytest.mark.anyio
async def test_stalled_socket_drops_oldest_packets_without_blocking_offer(monkeypatch):
    import websockets

    socket = FakeSocket()
    blocked = asyncio.Event()
    original_send = socket.send

    async def send(raw):
        if json.loads(raw)["cmd"] == "asd_audio":
            await blocked.wait()
        await original_send(raw)

    socket.send = send

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    times = iter(1000 + i * 0.08 for i in range(20))
    monkeypatch.setattr(
        "openjarvis.server.voice.speaker_vision._now", lambda: next(times)
    )
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(socket.sent))
    socket.status(socket.sent[0]["stream_id"], "ready")
    await eventually(lambda: bridge.ready)
    for _ in range(12):
        bridge.offer(b"a" * 2560, 16000, 1)
    assert len(bridge._queue) <= 8
    blocked.set()
    await eventually(lambda: len(socket.sent) >= 9)
    seqs = [p["seq"] for p in socket.sent if p["cmd"] == "asd_audio"]
    assert max(seqs) == 11
    assert len(seqs) < 12
    await bridge.close()


@pytest.mark.anyio
async def test_clock_discontinuity_starts_new_stream(monkeypatch):
    import websockets

    sockets = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = FakeSocket()
        sockets.append(socket)
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    times = iter((1000.04, 1001.04, 1001.12))
    monkeypatch.setattr(
        "openjarvis.server.voice.speaker_vision._now", lambda: next(times)
    )
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(sockets and sockets[0].sent))
    first_id = sockets[0].sent[0]["stream_id"]
    sockets[0].status(first_id, "ready")
    await eventually(lambda: bridge.ready)
    bridge.offer(b"x" * 1280, 16000, 1)  # half packet
    span = bridge.offer(b"y" * 1280, 16000, 1)
    assert span.stream_id is None
    await eventually(lambda: len(sockets) == 2 and bool(sockets[1].sent))
    second_id = sockets[1].sent[0]["stream_id"]
    assert second_id != first_id
    sockets[1].status(second_id, "ready")
    await eventually(lambda: bridge.ready)
    bridge.offer(b"z" * 2560, 16000, 1)
    await eventually(lambda: len(sockets[1].sent) == 2)
    assert base64.b64decode(sockets[1].sent[1]["pcm16_b64"]) == b"z" * 2560
    await bridge.close()


@pytest.mark.anyio
async def test_bad_audio_format_skips_bridge_but_preserves_faces(monkeypatch):
    import websockets

    socket = FakeSocket()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    faces = FaceTrackBuffer()
    faces.add(
        {
            "event": "faces",
            "ts": 100.0,
            "tracks": [
                {
                    "track_id": 1,
                    "distance_m": 0.5,
                    "mouth_activity": 0.9,
                    "asd": {"stream_id": "old", "probabilities": [0.9] * 25},
                }
            ],
        }
    )
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    await bridge.start()
    await eventually(lambda: bool(socket.sent))
    socket.status(socket.sent[0]["stream_id"], "ready")
    await eventually(lambda: bridge.ready)
    bridge.offer(b"a" * 2560, 48000, 1)
    bridge.offer(b"a" * 2560, 16000, 2)
    bridge.offer(b"a" * 2559, 16000, 1)
    await asyncio.sleep(0)
    assert socket.sent == [socket.sent[0]]
    assert bridge.audio_seconds == 0
    assert faces.anchor(100.0, 1.0) == 1
    assert faces.mouth(1, 99.9, 100.1) == pytest.approx(0.9)
    assert "asd" not in faces._events[0]["tracks"][0]
    await bridge.close()


@pytest.mark.anyio
async def test_busy_reconnect_uses_new_id_and_only_new_pcm(monkeypatch):
    import websockets

    from openjarvis.server.voice import speaker_vision

    sockets = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = FakeSocket()
        sockets.append(socket)
        yield socket

    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(speaker_vision, "RECONNECT_DELAYS", (0,))
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(sockets and sockets[0].sent))
    old_id = sockets[0].sent[0]["stream_id"]
    sockets[0].status(old_id, "busy")
    await eventually(lambda: len(sockets) == 2 and bool(sockets[1].sent))
    new_id = sockets[1].sent[0]["stream_id"]
    assert new_id != old_id
    sockets[1].status(new_id, "ready")
    await eventually(lambda: bridge.ready)
    bridge._finish(old_id)  # a late callback for the closed socket
    assert bridge.stream_id == new_id
    bridge.offer(b"n" * 2560, 16000, 1)
    await eventually(lambda: len(sockets[1].sent) == 2)
    assert base64.b64decode(sockets[1].sent[1]["pcm16_b64"]) == b"n" * 2560
    await bridge.close()


@pytest.mark.parametrize(
    "body",
    [
        "vision_asd = true\n",
        "vision_asd = true\nenabled = true\nvision_faces = true\n",
        "asd_accept_prob = 0.2\nasd_reject_prob = 0.3\n",
        "asd_accept_prob = inf\n",
        "asd_wait_secs = 0.13\n",
        (
            "vision_asd = true\nenabled = true\nvision_faces = true\n"
            'diarizer = "sortformer"\nstt_mask = true\nstt_mask_delay_secs = 0.6\n'
        ),
    ],
)
def test_invalid_asd_settings_fail_at_load(tmp_path, monkeypatch, body):
    preset = tmp_path / "preset.toml"
    preset.write_text("[voice.speaker]\n" + body)
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    with pytest.raises(ValueError, match="voice_speaker_"):
        load_speaker_settings()
