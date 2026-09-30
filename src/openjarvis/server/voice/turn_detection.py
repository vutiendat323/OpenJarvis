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

# After a turn closes, the Agent takes seconds before the bot speaks. Until it
# does, unconfirmed speech must not open a turn: that interruption cancels
# the reply being prepared (live trial 2026-09-28: 1.7-4 s gaps, 3 of 4 replies
# lost; with a 6 s window the Agent still took 6.1-7.8 s on 4 of 26 turns and
# lost them). The start strategy never sees the Agent finish silently, so the
# window also ends on its own.
# ponytail: fixed window; end it on an explicit "reply done" signal if one
# reaches the user aggregator.
REPLY_PENDING_SECS = 12.0


@dataclass
class SpeakerVerdictFrame(SystemFrame):
    """The gate's verdict for one diarizer frame (80 ms) of speech."""

    verdict: Verdict = Verdict.UNCERTAIN
    locked: bool = False  # a customer is locked (fusion)
    overlap_target: bool = False  # the customer talks over another voice
    source: str | None = None  # asd | mar | voice | audio


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
        self._reply_pending_until = 0.0

    @property
    def turn_open(self) -> bool:
        return self._turn_open

    @property
    def has_evidence(self) -> bool:
        """A diarizer is feeding verdicts, so speech can be told apart."""
        return self._tracker.has_evidence

    async def handle_user_turn_started(self) -> None:
        self._turn_open = True
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self) -> None:
        self._turn_open = False
        await super().handle_user_turn_stopped()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            # The reply is being spoken. Not on BotStopped: the previous reply
            # can end just after a turn closes (live 2026-09-29), and that
            # cleared the window before the new reply was spoken.
            self._reply_pending_until = 0.0
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
        if self._bot_speaking or time.monotonic() < self._reply_pending_until:
            if (
                self._tracker.has_evidence
                and self._accept_run < self._bargein_accept_frames
            ):
                return
        elif self._tracker.span_verdict() is Verdict.REJECT:
            return
        # Without stt_mask, anything aggregated while no turn was open is
        # speech the gate declined; it must not ride along into this turn.
        # With stt_mask, REJECTed audio reached STT as silence, so what was
        # aggregated is speech the gate did not reject (e.g. words said while
        # the bot spoke): keep it, or the customer is never answered.
        # ponytail: a declined span's transcript that arrives after this
        # reset still lands in the new turn; tag transcripts by span if the
        # bench shows it.
        if not self._tracker.masks_stt:
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
        else:
            self._reply_pending_until = time.monotonic() + REPLY_PENDING_SECS
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
        # A VAD onset while the turn waits to close, held until the gate says
        # whose voice it is (see _handle_vad_user_started_speaking).
        self._contested_onset: VADUserStartedSpeakingFrame | None = None

    async def handle_user_turn_started(self) -> None:
        await self._cancel_confirmation()
        self._silence_started_at = None
        self._contested_onset = None
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self) -> None:
        await self._cancel_confirmation()
        self._silence_started_at = None
        self._contested_onset = None
        await super().handle_user_turn_stopped()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if (
            isinstance(frame, SpeakerVerdictFrame)
            and self._contested_onset is not None
            and frame.verdict is Verdict.ACCEPT
        ):
            # The customer is still talking: the turn is not over after all.
            onset, self._contested_onset = self._contested_onset, None
            await self._reopen(onset)
        return await super().process_frame(frame)

    async def cleanup(self) -> None:
        await self._cancel_confirmation()
        await super().cleanup()

    async def _handle_vad_user_started_speaking(
        self, frame: VADUserStartedSpeakingFrame
    ) -> None:
        # VAD hears anyone. In a crowd, other voices kept cancelling the
        # closing of the customer's finished turn (live 2026-09-28: one turn
        # open 50 s, never answered). With diarizer evidence, only a voice
        # the gate ACCEPTs reopens a turn that is waiting to close.
        if (
            self._confirmation_task is not None
            and self._speaker_gate is not None
            and self._speaker_gate.has_evidence
        ):
            self._contested_onset = frame
            return
        await self._reopen(frame)

    async def _reopen(self, frame: VADUserStartedSpeakingFrame) -> None:
        await self._cancel_confirmation()
        self._silence_started_at = None
        await super()._handle_vad_user_started_speaking(frame)

    async def _handle_vad_user_stopped_speaking(
        self, frame: VADUserStoppedSpeakingFrame
    ) -> None:
        if self._contested_onset is not None:
            # Another voice came and went; the customer's silence continues.
            self._contested_onset = None
            return
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
        # Smart Turn also fires with no turn open (Pipecat ignores that stop);
        # only a real turn gets a verdict.
        if self._speaker_gate is not None and self._speaker_gate.turn_open:
            await self._speaker_gate.close_turn()
        await super().trigger_user_turn_stopped(
            enable_user_speaking_frames=enable_user_speaking_frames
        )

    async def _cancel_confirmation(self) -> None:
        if self._confirmation_task is not None:
            await self.task_manager.cancel_task(self._confirmation_task)
            self._confirmation_task = None
