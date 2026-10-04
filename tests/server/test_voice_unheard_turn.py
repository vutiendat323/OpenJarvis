"""A customer turn that Gemini never transcribes must not leave the kiosk hung.

Live 2026-10-03: four turns spoken right after a bot reply got no transcript.
Pipecat's stop timeout then closed each turn with nothing to answer, and the
kiosk showed "Processing..." until the session was torn down.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from loguru import logger
from pipecat.services.google.gemini_live.stt import GeminiSTTService

from openjarvis.server.voice.llm import voice_activity_frame
from openjarvis.server.voice.pipeline import return_to_listening_if_unheard
from openjarvis.server.voice.routes import _transcriber


def _phase(frame):
    return frame.message["data"]["data"]["phase"]


class _Aggregator:
    def __init__(self, text: str):
        self._text = text
        self.pushed = []

    def aggregation_string(self) -> str:
        return self._text

    async def push_frame(self, frame, direction=None):
        self.pushed.append(frame)


def test_listening_is_a_voice_activity_phase():
    assert _phase(voice_activity_frame("listening")) == "listening"


@pytest.mark.anyio
async def test_an_unheard_turn_returns_the_kiosk_to_listening():
    aggregator = _Aggregator("")

    await return_to_listening_if_unheard(aggregator)

    assert [_phase(frame) for frame in aggregator.pushed] == ["listening"]


@pytest.mark.anyio
async def test_a_turn_with_words_is_left_to_be_answered():
    aggregator = _Aggregator("cho mình xem menu")

    await return_to_listening_if_unheard(aggregator)

    assert aggregator.pushed == []


@pytest.mark.anyio
async def test_finalization_logs_how_much_audio_gemini_heard(monkeypatch):
    """Tells a silenced (masked) utterance apart from one Gemini dropped."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stt = _transcriber()
    logged = []

    async def sent(self, audio):
        pass

    async def finalized(self):
        pass

    monkeypatch.setattr(GeminiSTTService, "_send_audio", sent)
    monkeypatch.setattr(GeminiSTTService, "_send_finalization_signal", finalized)
    speech = (np.full(16_000, 1200, dtype=np.int16)).tobytes()
    sink = logger.add(lambda message: logged.append(str(message)), level="INFO")
    try:
        async for _ in stt.run_stt(speech):
            pass
        await stt._send_finalization_signal()
        await stt._send_finalization_signal()
    finally:
        logger.remove(sink)

    heard = [line for line in logged if "utterance audio" in line]
    assert "1.00s" in heard[0] and "peak=1200" in heard[0]
    assert "0.00s" in heard[1]
