"""Session audio mirrored to Vision through its dedicated WebSocket protocol."""

import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager

import pytest

from openjarvis.server.voice.speaker import FaceTrackBuffer, load_speaker_settings
from openjarvis.server.voice.speaker_vision import VisionAudioBridge


class FakeSocket:
    def __init__(self):
        self.sent = []
        self.inbound = asyncio.Queue()
        self.closed = False

    async def send(self, raw):
        if self.closed:
            raise RuntimeError("socket already closed")
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
async def test_wait_for_asd_evidence_uses_current_chunk_deadline():
    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    event = {
        "event": "faces",
        "ts": now,
        "tracks": [
            {
                "track_id": 2,
                "distance_m": 0.7,
                "mouth_activity": 0.05,
                "active_speaker_probability": None,
            }
        ],
    }
    faces.add(event)
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    bridge._ready = True
    bridge._stream_id = "current"
    bridge._start, bridge._end = now - 1.2, now

    async def publish():
        await asyncio.sleep(0.02)
        result = {"event": "faces", "ts": now, "tracks": [dict(event["tracks"][0])]}
        result["tracks"][0]["asd"] = {
            "stream_id": "current",
            "t0": now - 0.8,
            "frame_secs": 0.04,
            "probabilities": [0.8] * 25,
        }
        faces.add(result)

    task = asyncio.create_task(publish())
    await bridge.wait_for_evidence(faces, now - 0.08, now, timeout=0.12)
    await task
    assert faces.active_speaker(
        2, now - 0.08, now, stream_id="current", now=now
    ) == pytest.approx(0.8)

    old = len(faces._events)
    start = time.monotonic()
    await bridge.wait_for_evidence(faces, now - 0.9, now - 0.82, timeout=0.02)
    assert time.monotonic() - start < 0.07
    assert len(faces._events) == old
    assert bridge.asd_late == 1


@pytest.mark.anyio
async def test_wait_skips_before_ready_or_one_second_warmup():
    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    faces.add(
        {
            "event": "faces",
            "ts": now,
            "tracks": [
                {
                    "track_id": 2,
                    "distance_m": 0.7,
                    "mouth_activity": 0.05,
                }
            ],
        }
    )
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    start = time.monotonic()
    await bridge.wait_for_evidence(faces, now - 0.08, now, timeout=0.12)
    bridge._ready = True
    bridge._stream_id = "current"
    bridge._start, bridge._end = now - 0.9, now
    await bridge.wait_for_evidence(faces, now - 0.08, now, timeout=0.12)
    assert time.monotonic() - start < 0.05
    assert bridge.asd_late == 0


@pytest.mark.anyio
async def test_result_completed_after_deadline_is_late(monkeypatch):
    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    track = {"track_id": 2, "distance_m": 0.7, "mouth_activity": 0.05}
    faces.add({"event": "faces", "ts": now, "tracks": [track]})
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    bridge._ready = True
    bridge._stream_id = "current"
    bridge._start, bridge._end = now - 1.2, now
    original = faces.active_speaker

    def publish_during_lookup(*args, **kwargs):
        time.sleep(0.02)  # the first lookup finishes after the 10 ms deadline
        scored = dict(track)
        scored["asd"] = {
            "stream_id": "current",
            "t0": now - 0.8,
            "frame_secs": 0.04,
            "probabilities": [0.9] * 25,
        }
        faces.add({"event": "faces", "ts": now, "tracks": [scored]})
        return original(*args, **kwargs)

    monkeypatch.setattr(faces, "active_speaker", publish_during_lookup)
    await bridge.wait_for_evidence(faces, now - 0.08, now, timeout=0.01)
    assert bridge.asd_late == 1
    assert bridge.last_wait_timed_out is True


@pytest.mark.anyio
async def test_bridge_packetizes_actual_samples_and_stops(monkeypatch):
    import websockets

    socket = FakeSocket()

    @asynccontextmanager
    async def connect(*args, **kwargs):
        try:
            yield socket
        finally:
            socket.closed = True

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
        (
            "vision_asd = true\nenabled = true\nvision_faces = true\n"
            'diarizer = "sortformer"\nstt_mask = true\nstt_mask_delay_secs = nan\n'
        ),
    ],
)
def test_invalid_asd_settings_fail_at_load(tmp_path, monkeypatch, body):
    preset = tmp_path / "preset.toml"
    preset.write_text("[voice.speaker]\n" + body)
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    with pytest.raises(ValueError, match="voice_speaker_"):
        load_speaker_settings()


@pytest.mark.anyio
async def test_packet_posterior_subscription_gate_and_reconnect(monkeypatch):
    """Fake Vision consumes actual bridge packets; existing subscriber feeds gate."""
    import copy
    from pathlib import Path

    from openjarvis.kiosk.vision_client import VisionClient
    from openjarvis.server.voice.speaker import AudioOnlyGate, SpeakerSettings, Verdict

    example = json.loads(
        (Path(__file__).parents[1] / "fixtures/asd_protocol_v1.json").read_text()
    )
    subscriber = FakeSocket()
    audio_sockets = []
    clock = [10.0]

    class FakeVision(FakeSocket):
        def __init__(self):
            super().__init__()
            self.count = 0
            self.previous_seq = None

        async def send(self, raw):
            await super().send(raw)
            packet = self.sent[-1]
            if packet["cmd"] == "asd_audio_start":
                self.status(packet["stream_id"], "ready")
            elif packet["cmd"] == "asd_audio":
                assert len(base64.b64decode(packet["pcm16_b64"])) == 2560
                if (
                    self.previous_seq is not None
                    and packet["seq"] != self.previous_seq + 1
                ):
                    self.count = 0
                self.previous_seq = packet["seq"]
                self.count += 1
                event = copy.deepcopy(example["faces"])
                event["ts"] = packet["t0"] + 0.08
                track = event["tracks"][0]
                if self.count < 13:
                    track.pop("asd")
                    track["active_speaker_probability"] = None
                else:
                    track["asd"]["stream_id"] = packet["stream_id"]
                    track["asd"]["t0"] = event["ts"] - 1
                subscriber.inbound.put_nowait(event)

    @asynccontextmanager
    async def connect(url, **kwargs):
        if url == "ws://faces":
            yield subscriber
        else:
            socket = FakeVision()
            audio_sockets.append(socket)
            yield socket

    monkeypatch.setattr("websockets.connect", connect)
    monkeypatch.setattr("openjarvis.server.voice.speaker_vision._now", lambda: clock[0])
    monkeypatch.setattr("openjarvis.server.voice.speaker_vision.RECONNECT_DELAYS", (0,))
    monkeypatch.setattr("openjarvis.server.voice.speaker.time.time", lambda: clock[0])
    faces = FaceTrackBuffer()
    client = VisionClient("ws://faces", faces=faces)
    client_task = asyncio.create_task(client.run())
    bridge = VisionAudioBridge("ws://audio", "fixture", faces)
    gate = AudioOnlyGate(SpeakerSettings(vision_asd=True), faces)
    await bridge.start()
    try:
        await eventually(lambda: bridge.ready and bool(subscriber.sent))
        for _ in range(13):
            clock[0] += 0.08
            bridge.offer(bytes(2560), 16000, 1)
            await asyncio.sleep(0.001)
        await eventually(
            lambda: (
                faces.active_speaker(
                    1,
                    clock[0] - 0.08,
                    clock[0],
                    stream_id=bridge.stream_id,
                    now=clock[0],
                )
                is not None
            )
        )
        assert (
            gate.frame(
                [0.9], bot_speaking=False, t=clock[0], asd_stream_id=bridge.stream_id
            )
            == Verdict.ACCEPT
        )
        assert gate.last_evidence_detail["source"] == "asd"
        old = bridge.stream_id
        audio_sockets[-1].inbound.put_nowait(None)
        await eventually(lambda: bridge.ready and bridge.stream_id != old)
        assert (
            gate.frame(
                [0.9], bot_speaking=False, t=clock[0], asd_stream_id=bridge.stream_id
            )
            == Verdict.REJECT
        )
        # Overflow exposes a packet sequence gap; fake provider loses its window.
        for _ in range(13):
            clock[0] += 0.08
            bridge.offer(bytes(2560), 16000, 1)
        await asyncio.sleep(0.02)
        assert (
            faces.active_speaker(
                1, clock[0] - 0.08, clock[0], stream_id=bridge.stream_id, now=clock[0]
            )
            is None
        )
        assert client.events.empty()
    finally:
        await bridge.close()
        await client.stop()
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
