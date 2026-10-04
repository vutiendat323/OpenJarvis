"""Loopback relay of remote camera frames to Vision.

The kiosk's C920 stays Vision's camera. A kiosk page opened with
``?camera=remote`` sends its camera over WebRTC (``kiosk.remote_camera``);
each decoded frame is handed here and relayed to Vision on its own socket.
Vision publishes the frames in place of the C920 only while they keep
arriving, and falls straight back when this socket closes.

Wire format to Vision, one binary message per frame: an 8-byte little-endian
float64 capture time (this server's wall clock, the clock audio spans use)
and the frame as a JPEG.
"""

from __future__ import annotations

import asyncio
import json
import struct
import uuid
from typing import Any, Callable

from loguru import logger

from openjarvis.kiosk.vision_client import RECONNECT_DELAYS

_HEADER = struct.Struct("<d")


class VisionVideoBridge:
    """Page-owned sender: newest frame only, reconnecting until closed."""

    def __init__(
        self,
        url: str,
        session_id: str,
        *,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self._url = url
        self._session_id = session_id
        self._connect = connect
        self._task: asyncio.Task | None = None
        self._closed = False
        self._connected = False
        self._latest: bytes | None = None
        self._ready = asyncio.Event()

    async def start(self) -> None:
        if self._task is None and not self._closed:
            self._task = asyncio.create_task(self._run(), name="vision_video")

    def offer(self, jpeg: bytes, timestamp: float) -> None:
        """Keep only the newest frame; with Vision unreachable, drop it."""
        if not self._connected or self._closed:
            return
        self._latest = _HEADER.pack(timestamp) + jpeg
        self._ready.set()

    async def close(self) -> None:
        self._closed = True
        self._connected = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _open(self):
        if self._connect is not None:
            return self._connect(self._url)
        import websockets

        return websockets.connect(self._url, ping_interval=30, close_timeout=2)

    async def _send_frames(self, ws) -> None:
        while not self._closed:
            await self._ready.wait()
            self._ready.clear()
            payload, self._latest = self._latest, None
            if payload is not None:
                await ws.send(payload)

    async def _run(self) -> None:
        delay = 0
        while not self._closed:
            try:
                async with self._open() as ws:
                    await ws.send(
                        json.dumps(
                            {
                                "cmd": "video_start",
                                "session_id": self._session_id,
                                "stream_id": str(uuid.uuid4()),
                            }
                        )
                    )
                    self._connected, delay = True, 0
                    sender = asyncio.create_task(self._send_frames(ws))
                    try:
                        # Vision's replies are not needed; reading keeps the
                        # socket serviced and ends when Vision goes away.
                        async for _ in ws:
                            pass
                    finally:
                        sender.cancel()
                        await asyncio.gather(sender, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect while the page lives
                logger.warning("Vision remote video connection failed: {}", exc)
            finally:
                self._connected, self._latest = False, None
            if self._closed:
                return
            await asyncio.sleep(RECONNECT_DELAYS[min(delay, len(RECONNECT_DELAYS) - 1)])
            delay += 1


__all__ = ["VisionVideoBridge"]
