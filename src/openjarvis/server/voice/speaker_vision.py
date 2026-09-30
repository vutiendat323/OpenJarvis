"""Bounded, session-owned PCM mirror for Vision's active-speaker model."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass

from loguru import logger

from openjarvis.kiosk.vision_client import RECONNECT_DELAYS
from openjarvis.server.voice.speaker import FaceTrackBuffer

_RATE = 16_000
_PACKET_BYTES = 1_280 * 2
_MAX_PACKETS = 8
_PROTOCOL_VERSION = 2  # asd_track pins and pushed asd events; 1 is the fallback
_MAX_AGES = 512


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class AudioSpan:
    start: float
    end: float
    stream_id: str | None


class VisionAudioBridge:
    def __init__(self, url: str, session_id: str, faces: FaceTrackBuffer) -> None:
        self._url = url
        self._session_id = session_id
        self._faces = faces
        self._task: asyncio.Task | None = None
        self._closed = False
        self._terminal = False
        self._ready = False
        self._stream_id: str | None = None
        # Sample clock: one anchor plus an integer sample count, so times do
        # not drift when many small durations are summed at Unix magnitudes.
        self._start: float | None = None
        self._samples = 0
        self._pending = bytearray()
        self._queue: deque[tuple[int, float, bytes]] = deque(maxlen=_MAX_PACKETS)
        self._queued = asyncio.Event()
        self._seq = 0
        self.asd_late = 0
        self.last_wait_timed_out = False
        self._version = _PROTOCOL_VERSION
        self._pin: int | None = None
        self._pin_dirty = False
        # Arrival age of pushed ASD windows (ms past the window's end), for
        # the session summary.
        self.asd_age_ms: deque[float] = deque(maxlen=_MAX_AGES)

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def stream_id(self) -> str | None:
        return self._stream_id if self._ready else None

    @property
    def audio_seconds(self) -> float:
        if not self._ready or self._start is None:
            return 0.0
        return self._samples / _RATE

    @property
    def protocol_version(self) -> int:
        return self._version

    def pin(self, track_id: int | None) -> None:
        """Hold Vision's ASD on the locked customer (v2); None lets it pick
        the nearest face. Remembered across reconnects."""
        if track_id == self._pin:
            return
        self._pin = track_id
        if self._ready and self._version == 2:
            self._pin_dirty = True
            self._queued.set()

    def _on_asd(self, event: dict, stream_id: str) -> None:
        if event.get("stream_id") != stream_id or not self._ready:
            return
        t0 = event.get("t0")
        if (
            not isinstance(t0, bool)
            and isinstance(t0, (int, float))
            and math.isfinite(t0)
        ):
            self.asd_age_ms.append((_now() - (t0 + 1.0)) * 1000.0)
        self._faces.add_asd(event)

    async def wait_for_evidence(
        self, faces: FaceTrackBuffer, t0: float, t1: float, *, timeout: float
    ) -> None:
        """Give a current ASD window one bounded chance to reach this chunk."""
        self.last_wait_timed_out = False
        stream_id = self.stream_id
        if (
            timeout <= 0
            or stream_id is None
            or self.audio_seconds < 1.0
            or not faces.fresh(t1)
        ):
            return
        track_id = faces.anchor(t1, float("inf"))
        if track_id is None:
            return
        deadline = time.monotonic() + min(timeout, 0.12)
        while self.stream_id == stream_id:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.asd_late += 1
                self.last_wait_timed_out = True
                return
            probability = faces.active_speaker(
                track_id, t0, t1, stream_id=stream_id, now=time.time()
            )
            if probability is not None and time.monotonic() < deadline:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.asd_late += 1
                self.last_wait_timed_out = True
                return
            await asyncio.sleep(min(0.01, remaining))

    async def start(self) -> None:
        if self._task is None and not self._closed and not self._terminal:
            self._task = asyncio.create_task(self._run(), name="vision_audio")

    def offer(self, audio: bytes, sample_rate: int, num_channels: int) -> AudioSpan:
        now = _now()
        if sample_rate != _RATE or num_channels != 1 or len(audio) % 2:
            return AudioSpan(now, now, self.stream_id)
        duration = len(audio) / (_RATE * 2)
        if not self._ready or self._closed:
            return AudioSpan(now - duration, now, None)
        if self._start is not None and (
            abs(now - (self._start + self._samples / _RATE + duration)) > 0.25
        ):
            # Media arrival moved away from the contiguous sample clock.
            self._restart()
            return AudioSpan(now - duration, now, None)
        if self._start is None:
            self._start, self._samples = now - duration, 0
        start = self._start + self._samples / _RATE
        self._samples += len(audio) // 2
        end = self._start + self._samples / _RATE
        self._pending.extend(audio)
        while len(self._pending) >= _PACKET_BYTES:
            packet = bytes(self._pending[:_PACKET_BYTES])
            del self._pending[:_PACKET_BYTES]
            # This packet starts at the end of all offered samples except
            # those still pending and those in this packet.
            first = self._samples - (len(self._pending) + _PACKET_BYTES) // 2
            t0 = self._start + first / _RATE
            self._queue.append((self._seq, t0, packet))
            self._seq += 1
            self._queued.set()
        return AudioSpan(start, end, self._stream_id)

    def _restart(self) -> None:
        old = self._task
        self._finish(self._stream_id)
        if old is not None:
            old.cancel()
        self._task = asyncio.create_task(self._run(), name="vision_audio")

    def _finish(self, stream_id: str | None) -> None:
        if stream_id != self._stream_id:
            return
        self._ready = False
        self._stream_id = None
        self._faces.set_asd_stream(None)
        self._pending.clear()
        self._queue.clear()
        self._queued.clear()
        self._start, self._samples = None, 0
        self._seq = 0

    async def _send(self, ws, stream_id: str) -> None:
        try:
            while self._stream_id == stream_id:
                await self._queued.wait()
                self._queued.clear()
                if self._pin_dirty:
                    self._pin_dirty = False
                    await ws.send(
                        json.dumps(
                            {
                                "cmd": "asd_track",
                                "stream_id": stream_id,
                                "track_id": self._pin,
                            }
                        )
                    )
                while self._queue and self._stream_id == stream_id:
                    seq, t0, pcm = self._queue.popleft()
                    await ws.send(
                        json.dumps(
                            {
                                "cmd": "asd_audio",
                                "stream_id": stream_id,
                                "seq": seq,
                                "t0": t0,
                                "pcm16_b64": base64.b64encode(pcm).decode("ascii"),
                            }
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect after send failure
            logger.warning("Vision ASD audio send failed: {}", exc)
            await ws.close()

    async def _run(self) -> None:
        import websockets

        delay_idx = 0
        while not self._closed and not self._terminal:
            stream_id = str(uuid.uuid4())
            sender = None
            retry_now = False
            try:
                async with websockets.connect(
                    self._url, ping_interval=30, close_timeout=2
                ) as ws:
                    self._stream_id = stream_id
                    try:
                        await ws.send(
                            json.dumps(
                                {
                                    "cmd": "asd_audio_start",
                                    "version": self._version,
                                    "session_id": self._session_id,
                                    "stream_id": stream_id,
                                    "sample_rate": _RATE,
                                    "channels": 1,
                                }
                            )
                        )
                        async for raw in ws:
                            try:
                                event = json.loads(raw)
                            except (TypeError, ValueError):
                                continue
                            if not isinstance(event, dict):
                                continue
                            if event.get("event") == "asd":
                                self._on_asd(event, stream_id)
                                continue
                            if (
                                event.get("event") != "asd_audio_status"
                                or event.get("stream_id") != stream_id
                            ):
                                continue
                            status = event.get("status")
                            if status == "ready" and not self._ready:
                                self._ready = True
                                self._faces.set_asd_stream(stream_id)
                                self._faces.use_pushed_asd(self._version == 2)
                                delay_idx = 0
                                if self._version == 2 and self._pin is not None:
                                    self._pin_dirty = True
                                    self._queued.set()
                                sender = asyncio.create_task(self._send(ws, stream_id))
                            elif status == "invalid" and self._version == 2:
                                # A Vision older than v2: no pins, ASD via faces.
                                self._version = 1
                                logger.warning(
                                    "Vision ASD asd_protocol=v1 (Vision predates v2)"
                                )
                                retry_now = True
                                break
                            elif status in ("disabled", "unavailable", "invalid"):
                                self._terminal = True
                                break
                            elif status == "busy":
                                break
                    finally:
                        if sender is not None:
                            sender.cancel()
                            await asyncio.gather(sender, return_exceptions=True)
                        try:
                            await ws.send(
                                json.dumps(
                                    {"cmd": "asd_audio_stop", "stream_id": stream_id}
                                )
                            )
                        except Exception:  # noqa: BLE001 - socket may already be gone
                            pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - optional local service
                logger.warning("Vision ASD audio disconnected: {}", exc)
            finally:
                self._finish(stream_id)
            if self._closed or self._terminal:
                break
            if retry_now:
                continue
            delay = RECONNECT_DELAYS[min(delay_idx, len(RECONNECT_DELAYS) - 1)]
            delay_idx += 1
            await asyncio.sleep(delay)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._finish(self._stream_id)
