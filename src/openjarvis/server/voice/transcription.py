"""Gemini Live transcription settings for OpenJarvis Voice."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib
from google.genai.types import (
    AudioTranscriptionConfig,
    AudioTranscriptionConfigMode,
)
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    SystemFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.google.gemini_live.stt import GeminiSTTService

from openjarvis.server.voice.stt_capture import capture_from_environment
from openjarvis.server.voice.target_audio_monitor import LocalTargetAudioMonitor

DEFAULT_LANGUAGE_CODES = ("vi-VN", "en-US")
DEFAULT_TRANSCRIPTION_MODE = AudioTranscriptionConfigMode.VERBATIM
MAX_CUSTOM_VOCABULARY_TERMS = 1_000
# Longest the final transcript waits for held-back overlap to be separated.
DRAIN_TIMEOUT_SECS = 1.0
# Silence held after the VAD stop (0.2 s) before Gemini closes the utterance.
# Finalizing at every VAD stop cut one sentence at each breath (live
# 2026-10-06: pauses inside a sentence p50 0.3 s, p90 1.0 s; between sentences
# at least 1.19 s), and Gemini transcribed the pieces without context. A turn
# still needs 1.5 s of silence, so this costs little turn latency.
FINALIZE_HOLD_SECS = 0.6


@dataclass(frozen=True)
class GeminiSTTProfile:
    """The part of a Gemini Live request that improves recognition accuracy."""

    language_codes: tuple[str, ...] = DEFAULT_LANGUAGE_CODES
    custom_vocabulary: tuple[str, ...] = ()
    mode: AudioTranscriptionConfigMode = DEFAULT_TRANSCRIPTION_MODE


def _string_list(
    section: Mapping[str, Any], key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"voice_stt_{key}_must_be_a_string_list")

    cleaned = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if key == "custom_vocabulary" and len(cleaned) > MAX_CUSTOM_VOCABULARY_TERMS:
        raise ValueError("voice_stt_custom_vocabulary_too_large")
    return cleaned


def load_gemini_stt_profile() -> GeminiSTTProfile:
    """Load optional ``[voice.stt]`` settings from the active preset."""
    config_value = os.environ.get("OPENJARVIS_CONFIG", "").strip()
    if not config_value:
        return GeminiSTTProfile()

    config_path = Path(config_value).expanduser()
    if not config_path.exists():
        return GeminiSTTProfile()
    with config_path.open("rb") as config_file:
        data = tomllib.load(config_file)

    voice = data.get("voice", {})
    if not isinstance(voice, Mapping):
        raise ValueError("voice_config_must_be_a_table")
    section = voice.get("stt", {})
    if not isinstance(section, Mapping):
        raise ValueError("voice_stt_config_must_be_a_table")

    language_codes = _string_list(section, "language_codes", DEFAULT_LANGUAGE_CODES)
    custom_vocabulary = _string_list(section, "custom_vocabulary", ())
    raw_mode = section.get("mode", DEFAULT_TRANSCRIPTION_MODE.value)
    if not isinstance(raw_mode, str):
        raise ValueError("voice_stt_mode_must_be_a_string")
    try:
        mode = AudioTranscriptionConfigMode(raw_mode.strip().upper())
    except ValueError as exc:
        raise ValueError("voice_stt_mode_must_be_verbatim_or_smart") from exc
    if mode is AudioTranscriptionConfigMode.MODE_UNSPECIFIED:
        raise ValueError("voice_stt_mode_must_be_verbatim_or_smart")

    return GeminiSTTProfile(
        language_codes=language_codes,
        custom_vocabulary=custom_vocabulary,
        mode=mode,
    )


@dataclass
class SttAudioFrame(SystemFrame):
    """Delayed mic audio for Gemini only, with rejected speech silenced.

    A SystemFrame so an interruption does not drop it: the customer's
    barge-in words are exactly the audio that must still be transcribed.
    """

    audio: bytes = b""
    sample_rate: int = 16_000
    num_channels: int = 1
    # The speaker gate's verdict for this audio and why it was kept or
    # silenced, for the opt-in STT capture.
    verdict: str = ""
    reason: str = ""


class OpenJarvisGeminiSTTService(GeminiSTTService):
    """Use current Gemini transcription fields missing from Pipecat 1.8.1."""

    def __init__(self, *, profile: GeminiSTTProfile, **kwargs: Any) -> None:
        self._openjarvis_profile = profile
        self._masked_delay = 0.0
        self._drain: Callable[[], Awaitable[None]] | None = None
        self._finalize_task: asyncio.Task | None = None
        # True while the finalize task is still inside its silence hold.
        self._finalize_held = False
        self._capture = capture_from_environment()
        self._heard_bytes = 0
        self._heard_peak = 0
        self.target_audio_monitor = LocalTargetAudioMonitor()
        super().__init__(**kwargs)
        # The final transcript lands FINALIZE_HOLD_SECS later: the turn-stop
        # safety timer downstream must wait that much longer.
        self._ttfs_p99_latency = (self._ttfs_p99_latency or 0.0) + FINALIZE_HOLD_SECS

    def enable_masked_feed(
        self,
        delay_secs: float,
        drain: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Transcribe only the delayed, speaker-gated copy of the mic.

        Live audio still passes straight through to the VAD, so barge-in and
        endpointing keep their timing; Gemini hears the ``SttAudioFrame``s the
        speaker processor releases ``delay_secs`` later. ``drain`` releases
        audio the processor is holding back for speaker separation; it is
        awaited before each utterance is finalized.
        """
        self._masked_delay = delay_secs
        self._drain = drain
        # The final transcript now lands delay_secs later (plus separation);
        # the turn-stop safety timer downstream must wait that much longer.
        extra = delay_secs + (DRAIN_TIMEOUT_SECS if drain is not None else 0.0)
        self._ttfs_p99_latency = (self._ttfs_p99_latency or 0.0) + extra

    def _record(self, name: str, audio: bytes, sample_rate: int, **meta: str) -> None:
        """Review recording must never be able to break a live session."""
        if self._capture is None:
            return
        try:
            getattr(self._capture, name)(audio, sample_rate, **meta)
        except Exception:
            logger.exception(f"{self}: STT capture failed; recording stopped")
            self._capture = None

    async def run_stt(self, audio: bytes):
        # This is the final (possibly masked/separated) PCM sent to Gemini.
        # Playback only offers bytes; device work belongs to its own worker.
        if self.target_audio_monitor.enabled:
            try:
                self.target_audio_monitor.offer(audio, self.sample_rate)
            except Exception:
                self.target_audio_monitor.fail("Monitor enqueue failed")
        # Per-utterance level of what Gemini is sent: a turn that comes back
        # with no transcript is then either silenced upstream (peak ~0) or
        # real speech Gemini dropped.
        self._heard_bytes += len(audio)
        if audio:
            samples = memoryview(audio).cast("h")
            self._heard_peak = max(self._heard_peak, max(map(abs, samples)))
        async for frame in super().run_stt(audio):
            yield frame

    def _log_heard(self) -> None:
        logger.info(
            f"{self}: utterance audio sent to Gemini "
            f"{self._heard_bytes / (self.sample_rate * 2 or 32_000):.2f}s "
            f"peak={self._heard_peak}"
        )
        self._heard_bytes = 0
        self._heard_peak = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._cancel_held_finalize()
        if isinstance(frame, InputAudioRawFrame):
            self._record("raw", frame.audio, frame.sample_rate)
            if not self._masked_delay:
                self._record("heard", frame.audio, frame.sample_rate)
        elif isinstance(frame, SttAudioFrame):
            self._record(
                "heard",
                frame.audio,
                frame.sample_rate,
                verdict=frame.verdict,
                reason=frame.reason,
            )
        if self._masked_delay:
            if isinstance(frame, SttAudioFrame):
                await self.process_audio_frame(
                    InputAudioRawFrame(
                        audio=frame.audio,
                        sample_rate=frame.sample_rate,
                        num_channels=frame.num_channels,
                    ),
                    direction,
                )
                return
            if isinstance(frame, InputAudioRawFrame):
                await self.push_frame(frame, direction)
                return
        await super().process_frame(frame, direction)

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        if isinstance(frame, TranscriptionFrame):
            # What Gemini actually heard, for checking the speaker gate live.
            logger.info(f"{self}: transcript {frame.text!r}")
            if self._capture is not None:
                try:
                    self._capture.transcript(frame.text)
                except Exception:
                    logger.exception(f"{self}: STT capture failed; recording stopped")
                    self._capture = None
        await super().push_frame(frame, direction)

    async def _send_finalization_signal(self):
        hold = FINALIZE_HOLD_SECS
        if not hold and not self._masked_delay:
            self._log_heard()
            await super()._send_finalization_signal()
            return
        if self._finalize_task is not None:
            self._finalize_task.cancel()
        self._finalize_held = hold > 0
        self._finalize_task = asyncio.create_task(self._finalize_after_delay(hold))

    def _cancel_held_finalize(self) -> None:
        """Speech resumed inside the hold: the utterance is still going."""
        if self._finalize_task is not None and self._finalize_held:
            self._finalize_task.cancel()
            self._finalize_task = None
            self._finalize_held = False

    async def _finalize_after_delay(self, hold: float = 0.0) -> None:
        if hold:
            await asyncio.sleep(hold)
        # Past the hold the utterance is over; new speech no longer cancels it.
        self._finalize_held = False
        if self._masked_delay:
            # The utterance's last audio is still in the delay line: flush it.
            await asyncio.sleep(self._masked_delay + 0.05)
        if self._drain is not None:
            try:
                await asyncio.wait_for(self._drain(), DRAIN_TIMEOUT_SECS)
            except TimeoutError:
                logger.warning(f"{self}: held audio not drained; finalizing")
        self._log_heard()
        await super()._send_finalization_signal()

    async def cleanup(self) -> None:
        if self._finalize_task is not None:
            self._finalize_task.cancel()
        await asyncio.to_thread(self.target_audio_monitor.close)
        if self._capture is not None:
            self._capture.close()
            self._capture = None
        await super().cleanup()

    def _build_live_config(self):
        config = super()._build_live_config()
        profile = self._openjarvis_profile
        config.input_audio_transcription = AudioTranscriptionConfig(
            language_codes=list(profile.language_codes),
            custom_vocabulary=list(profile.custom_vocabulary) or None,
            mode=profile.mode,
        )
        return config


__all__ = [
    "GeminiSTTProfile",
    "OpenJarvisGeminiSTTService",
    "SttAudioFrame",
    "load_gemini_stt_profile",
]
