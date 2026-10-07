"""Deterministic voice-session lifecycle, spoken straight from events to TTS.

None of these messages involve the Agent or an LLM: each is a fixed
``TTSSpeakFrame`` pushed into TTS when its event fires.

* session accepted (client connected): greet;
* 15 s after the last output finished with no accepted customer speech: nudge
  once, re-armed only by new customer speech or a new assistant turn;
* 9 min: a one-minute warning, queued until nobody is speaking;
* 10 min, or Vision reporting the customer absent for 10 s in a row: goodbye,
  then end the session. One guard makes the goodbye and the end happen once.

The session ends through ``EndWorkerFrame`` sent upstream: the pipeline then
ends gracefully after the goodbye audio, and the route's normal teardown
releases the lease and saves the conversation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

GREETING = (
    "Chào bạn, mình sẵn sàng hỗ trợ gọi món. "
    "Bạn muốn xem menu hay chọn món yêu thích trước ạ?"
)
IDLE_PROMPT = "Bạn muốn đặt món hay xem menu ạ?"
WARNING = "Phiên gọi món còn một phút nữa ạ."
GOODBYE = "Cảm ơn bạn đã ghé quán, hẹn gặp lại nhé!"


class VoiceSessionLifecycle(FrameProcessor):
    def __init__(
        self,
        *,
        presence: Callable[[], bool | None] | None = None,
        idle_secs: float = 15.0,
        warning_after_secs: float = 540.0,
        limit_secs: float = 600.0,
        absence_secs: float = 10.0,
        poll_secs: float = 0.5,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._presence = presence
        self._idle_secs = idle_secs
        self._warning_after = warning_after_secs
        self._limit = limit_secs
        self._absence_secs = absence_secs
        self._poll = poll_secs
        self._bot_speaking = False
        self._user_speaking = False
        self._responding = False
        self._nudged = False
        self._ending = False
        self._warned = False
        self._started = False
        self._pending: list[str] = []
        self._idle: asyncio.Task | None = None
        self._timers: list[asyncio.Task] = []

    # -- session events -------------------------------------------------------

    async def start_session(self) -> None:
        """The kiosk accepted a customer and the voice session is connected."""
        if self._started:
            return
        self._started = True
        await self._speak(GREETING)
        self._timers = [
            asyncio.create_task(self._after(self._warning_after, self._warn)),
            asyncio.create_task(self._after(self._limit, self._end)),
        ]
        if self._presence is not None:
            self._timers.append(asyncio.create_task(self._watch_absence()))

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._cancel_idle()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            await self._flush()
            self._arm_idle()
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking, self._nudged = True, False
            self._cancel_idle()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            await self._flush()
            self._arm_idle()
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._responding, self._nudged = True, False
            self._cancel_idle()
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._responding = False
            await self.push_frame(frame, direction)
            await self._flush()
            self._arm_idle()
            return
        elif isinstance(frame, InterruptionFrame):
            # Cancelled Agent responses deliberately have no response-end frame.
            self._responding = False
            self._bot_speaking = False
            self._arm_idle()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self.stop_timers()
        await self.push_frame(frame, direction)

    def stop_timers(self) -> None:
        self._cancel_idle()
        for task in self._timers:
            if task is not asyncio.current_task():
                task.cancel()
        self._timers = []

    # -- timers ---------------------------------------------------------------

    def _arm_idle(self) -> None:
        self._cancel_idle()
        if self._ending or self._nudged or not self._timers:
            return
        self._idle = asyncio.create_task(self._after(self._idle_secs, self._nudge))

    def _cancel_idle(self) -> None:
        if self._idle is not None and self._idle is not asyncio.current_task():
            self._idle.cancel()
        self._idle = None

    @staticmethod
    async def _after(delay: float, action: Callable) -> None:
        await asyncio.sleep(delay)
        await action()

    async def _nudge(self) -> None:
        if (
            self._ending
            or self._bot_speaking
            or self._user_speaking
            or self._responding
        ):
            return
        self._nudged = True
        await self._speak(IDLE_PROMPT)

    async def _warn(self) -> None:
        if self._warned or self._ending:
            return
        self._warned = True
        logger.info("Kiosk voice: warning boundary reached")
        self._pending.append(WARNING)
        await self._flush()

    async def _watch_absence(self) -> None:
        absent_since: float | None = None
        while not self._ending:
            if self._presence() is False:
                now = time.monotonic()
                absent_since = absent_since or now
                if now - absent_since >= self._absence_secs:
                    await self._end()
                    return
            else:
                absent_since = None
            await asyncio.sleep(self._poll)

    # -- speech ---------------------------------------------------------------

    async def _flush(self) -> None:
        """Speak queued messages only once nobody is speaking or replying."""
        while (
            self._pending
            and not self._ending
            and not (self._bot_speaking or self._user_speaking or self._responding)
        ):
            await self._speak(self._pending.pop(0))

    async def _speak(self, text: str) -> None:
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

    async def _end(self) -> None:
        if self._ending:
            return
        self._ending = True
        logger.info("Kiosk voice: end boundary reached; queuing goodbye")
        self._pending.clear()
        self.stop_timers()
        await self._speak(GOODBYE)
        await self.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)

    async def request_warning(self) -> None:
        await self._warn()

    async def end_session(self) -> None:
        await self._end()

    async def cleanup(self) -> None:
        self.stop_timers()
        await super().cleanup()


__all__ = [
    "GOODBYE",
    "GREETING",
    "IDLE_PROMPT",
    "WARNING",
    "VoiceSessionLifecycle",
]
