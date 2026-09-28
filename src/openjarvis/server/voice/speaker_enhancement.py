"""Optional microphone enhancement with a session-long raw-PCM fallback."""

from __future__ import annotations

import importlib.util
from typing import Any

from loguru import logger
from pipecat.audio.filters.base_audio_filter import BaseAudioFilter

from openjarvis.server.voice.speaker import SpeakerSettings


class OptionalRNNoiseFilter(BaseAudioFilter):
    """Keep the original audio flowing if RNNoise cannot serve this session."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.effective_name = "none"

    async def start(self, sample_rate: int) -> None:
        try:
            await self._delegate.start(sample_rate)
            # Pipecat catches initialization failures without raising.
            if not self._delegate._rnnoise_ready:
                raise RuntimeError("RNNoise initialization failed")
        except Exception as exc:
            logger.warning("audio enhancer requested=rnnoise effective=none: {}", exc)
            return
        self.effective_name = "rnnoise"

    async def filter(self, audio: bytes) -> bytes:
        if self.effective_name == "none":
            return audio
        try:
            return await self._delegate.filter(audio)
        except Exception as exc:
            self.effective_name = "none"
            logger.warning("audio enhancer requested=rnnoise effective=none: {}", exc)
            return audio

    async def process_frame(self, frame: Any) -> None:
        await self._delegate.process_frame(frame)

    async def stop(self) -> None:
        await self._delegate.stop()
        self.effective_name = "none"


def build_audio_enhancer(settings: SpeakerSettings) -> OptionalRNNoiseFilter | None:
    """Build RNNoise only for a selected, enabled speaker pipeline."""
    if not settings.enabled or settings.enhancer == "none":
        return None
    try:
        if importlib.util.find_spec("pyrnnoise") is None:
            raise ImportError("pyrnnoise is not installed")
        from pipecat.audio.filters.rnnoise_filter import RNNoise, RNNoiseFilter

        if RNNoise is None:
            raise ImportError("pyrnnoise is not installed")
        return OptionalRNNoiseFilter(RNNoiseFilter())
    except Exception as exc:
        logger.warning("audio enhancer requested=rnnoise effective=none: {}", exc)
        return None
