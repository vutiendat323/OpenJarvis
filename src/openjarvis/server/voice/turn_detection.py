"""Turn detection policies for cascade Voice sessions."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    SystemFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_start.base_user_turn_start_strategy import (
    BaseUserTurnStartStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)

from openjarvis.server.voice.speaker import SpeakerTracker, Verdict


@dataclass
class SpeakerVerdictFrame(SystemFrame):
    """The gate's verdict for one diarizer frame (80 ms) of speech."""

    verdict: Verdict = Verdict.UNCERTAIN


class TargetSpeakerTurnStartStrategy(BaseUserTurnStartStrategy):
    """Open turns, and barge in, only for speech the speaker gate allows.

    Until a diarizer feeds verdicts, every decision is today's: VAD onset
    starts the turn and interrupts the bot.
    """

    def __init__(
        self, *, tracker: SpeakerTracker, bargein_accept_frames: int, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self._tracker = tracker
        self._bargein_accept_frames = bargein_accept_frames
        self._bot_speaking = False
        self._user_speaking = False
        self._turn_open = False
        self._accept_run = 0

    async def handle_user_turn_started(self) -> None:
        self._turn_open = True
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self) -> None:
        self._turn_open = False
        await super().handle_user_turn_stopped()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._user_speaking = True
            self._accept_run = 0
            if not self._turn_open:
                self._tracker.begin_span()
            await self._maybe_start()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._user_speaking = False
        elif isinstance(frame, SpeakerVerdictFrame):
            self._tracker.record(frame.verdict)
            self._accept_run = (
                self._accept_run + 1 if frame.verdict is Verdict.ACCEPT else 0
            )
            await self._maybe_start()
        return ProcessFrameResult.CONTINUE

    async def _maybe_start(self) -> None:
        if self._turn_open or not self._user_speaking:
            return
        if self._bot_speaking:
            if (
                self._tracker.has_evidence
                and self._accept_run < self._bargein_accept_frames
            ):
                return
        elif self._tracker.span_verdict() is Verdict.REJECT:
            return
        # Anything aggregated while no turn was open is speech the gate
        # declined; it must not ride along into this customer's turn.
        # ponytail: a declined span's transcript that arrives after this
        # reset still lands in the new turn; tag transcripts by span if the
        # bench shows it.
        await self.trigger_reset_aggregation()
        self._tracker.begin_turn()
        await self.trigger_user_turn_started()

    async def close_turn(self) -> Verdict:
        """Fix the turn's verdict; drop a rejected turn's words."""
        verdict = self._tracker.close_turn()
        logger.info(
            f"{self}: speaker turn verdict={verdict.value} "
            f"frames={self._tracker.frame_counts()}"
        )
        if verdict is Verdict.REJECT:
            await self.trigger_reset_aggregation()
        return verdict


class ConfirmedTurnAnalyzerUserTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    """Require sustained silence before accepting a COMPLETE prediction."""

    def __init__(
        self,
        *,
        minimum_silence_secs: float,
        speaker_gate: TargetSpeakerTurnStartStrategy | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._minimum_silence_secs = minimum_silence_secs
        self._speaker_gate = speaker_gate
        self._silence_started_at: float | None = None
        self._confirmation_task: asyncio.Task[None] | None = None

    async def handle_user_turn_started(self) -> None:
        await self._cancel_confirmation()
        self._silence_started_at = None
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self) -> None:
        await self._cancel_confirmation()
        self._silence_started_at = None
        await super().handle_user_turn_stopped()

    async def cleanup(self) -> None:
        await self._cancel_confirmation()
        await super().cleanup()

    async def _handle_vad_user_started_speaking(
        self, frame: VADUserStartedSpeakingFrame
    ) -> None:
        await self._cancel_confirmation()
        self._silence_started_at = None
        await super()._handle_vad_user_started_speaking(frame)

    async def _handle_vad_user_stopped_speaking(
        self, frame: VADUserStoppedSpeakingFrame
    ) -> None:
        self._silence_started_at = time.monotonic() - frame.stop_secs
        await super()._handle_vad_user_stopped_speaking(frame)

    async def trigger_user_turn_stopped(
        self, *, enable_user_speaking_frames: bool | None = None
    ) -> None:
        silence_elapsed = (
            time.monotonic() - self._silence_started_at
            if self._silence_started_at is not None
            else 0.0
        )
        remaining = max(0.0, self._minimum_silence_secs - silence_elapsed)
        if remaining == 0:
            await self._finish_turn(enable_user_speaking_frames)
        elif self._confirmation_task is None:
            self._confirmation_task = self.task_manager.create_task(
                self._confirm_after(remaining, enable_user_speaking_frames),
                f"{self}::_confirm_after",
            )

    async def _confirm_after(
        self, delay: float, enable_user_speaking_frames: bool | None
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        self._confirmation_task = None
        await self._finish_turn(enable_user_speaking_frames)

    async def _finish_turn(self, enable_user_speaking_frames: bool | None) -> None:
        if self._speaker_gate is not None:
            await self._speaker_gate.close_turn()
        await super().trigger_user_turn_stopped(
            enable_user_speaking_frames=enable_user_speaking_frames
        )

    async def _cancel_confirmation(self) -> None:
        if self._confirmation_task is not None:
            await self.task_manager.cancel_task(self._confirmation_task)
            self._confirmation_task = None
