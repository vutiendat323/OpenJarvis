"""One remote camera: a WebRTC video-only peer feeding Vision.

A kiosk page opened with ``?camera=remote`` offers its camera as soon as it
loads -- before any voice session -- so Vision can use it for presence and the
kiosk FSM runs as it does with the C920. Each decoded frame is stamped with
this server's clock (the clock audio spans use) and relayed to Vision through
``VisionVideoBridge``. When the peer connection fails, closes or disconnects,
the bridge closes and Vision falls straight back to the C920.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from loguru import logger

from openjarvis.server.voice.vision_video import VisionVideoBridge

# The relay to Vision is loopback; keep it close to lossless for face models.
_JPEG_QUALITY = 90
_ENDED = {"failed", "closed", "disconnected"}


def _jpeg(frame: Any) -> bytes | None:
    import cv2

    ok, buf = cv2.imencode(
        ".jpg",
        frame.to_ndarray(format="bgr24"),
        [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY],
    )
    return buf.tobytes() if ok else None


class RemoteCameraSession:
    def __init__(self, vision_url: str) -> None:
        self._bridge = VisionVideoBridge(vision_url, f"page-{uuid.uuid4()}")
        self._pc: Any = None
        self._pump: asyncio.Task | None = None
        self._closed = False

    async def answer(self, sdp: str, kind: str) -> Any:
        """Answer a video-only offer; raise ValueError for anything else."""
        from aiortc import RTCPeerConnection, RTCSessionDescription

        if kind != "offer" or "m=video" not in sdp:
            await self.close()
            raise ValueError("remote_camera_offer_must_carry_video")
        self._pc = pc = RTCPeerConnection()
        await self._bridge.start()

        @pc.on("track")
        def on_track(track: Any) -> None:
            if track.kind == "video" and self._pump is None:
                self._pump = asyncio.create_task(self._relay(track))

        @pc.on("connectionstatechange")
        async def on_state() -> None:
            if pc.connectionState in _ENDED:
                await self.close()

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=kind))
            await pc.setLocalDescription(await pc.createAnswer())
        except Exception:
            await self.close()
            raise
        return pc.localDescription

    async def _relay(self, track: Any) -> None:
        from aiortc.mediastreams import MediaStreamError

        try:
            while not self._closed:
                frame = await track.recv()
                received = time.time()
                jpeg = await asyncio.to_thread(_jpeg, frame)
                if jpeg is not None:
                    self._bridge.offer(jpeg, received)
        except MediaStreamError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad frame ends this camera, not the server
            logger.warning("remote camera relay stopped: {}", exc)
        finally:
            if not self._closed:
                asyncio.create_task(self.close())

    async def close(self) -> None:
        """Idempotent: end the peer and the Vision socket (Vision -> C920)."""
        if self._closed:
            return
        self._closed = True
        pump, self._pump = self._pump, None
        if pump is not None and pump is not asyncio.current_task():
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        if self._pc is not None:
            await self._pc.close()
        await self._bridge.close()
