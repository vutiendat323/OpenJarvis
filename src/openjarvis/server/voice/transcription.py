"""Gemini Live transcription settings for OpenJarvis Voice."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib
from google.genai.types import (
    AudioTranscriptionConfig,
    AudioTranscriptionConfigMode,
)
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    SystemFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.google.gemini_live.stt import GeminiSTTService

DEFAULT_LANGUAGE_CODES = ("vi-VN", "en-US")
DEFAULT_TRANSCRIPTION_MODE = AudioTranscriptionConfigMode.VERBATIM
MAX_CUSTOM_VOCABULARY_TERMS = 1_000


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


class OpenJarvisGeminiSTTService(GeminiSTTService):
    """Use current Gemini transcription fields missing from Pipecat 1.8.1."""

    def __init__(self, *, profile: GeminiSTTProfile, **kwargs: Any) -> None:
        self._openjarvis_profile = profile
        self._masked_delay = 0.0
        self._finalize_task: asyncio.Task | None = None
        super().__init__(**kwargs)

    def enable_masked_feed(self, delay_secs: float) -> None:
        """Transcribe only the delayed, speaker-gated copy of the mic.

        Live audio still passes straight through to the VAD, so barge-in and
        endpointing keep their timing; Gemini hears the ``SttAudioFrame``s the
        speaker processor releases ``delay_secs`` later.
        """
        self._masked_delay = delay_secs
        # The final transcript now lands delay_secs later; the turn-stop
        # safety timer downstream must wait that much longer for it.
        self._ttfs_p99_latency = (self._ttfs_p99_latency or 0.0) + delay_secs

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
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

    async def _send_finalization_signal(self):
        if not self._masked_delay:
            await super()._send_finalization_signal()
            return
        # The utterance's last audio is still in the delay line: flush it first.
        if self._finalize_task is not None:
            self._finalize_task.cancel()
        self._finalize_task = asyncio.create_task(self._finalize_after_delay())

    async def _finalize_after_delay(self) -> None:
        await asyncio.sleep(self._masked_delay + 0.05)
        await super()._send_finalization_signal()

    async def cleanup(self) -> None:
        if self._finalize_task is not None:
            self._finalize_task.cancel()
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
