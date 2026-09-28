"""The optional microphone enhancer preserves raw PCM when unavailable."""

from __future__ import annotations

import builtins
import importlib.util

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from openjarvis.server.voice.speaker import SpeakerSettings
from openjarvis.server.voice.speaker_enhancement import (
    OptionalRNNoiseFilter,
    build_audio_enhancer,
)


class FakeRNNoise:
    def __init__(
        self, *, ready: bool, fail_filter: bool = False, fail_start: bool = False
    ):
        self._rnnoise_ready = ready
        self.fail_filter = fail_filter
        self.fail_start = fail_start
        self.filter_calls = 0
        self.stopped = False
        self.frames = []

    async def start(self, sample_rate: int):
        if self.fail_start:
            raise RuntimeError("start failed")

    async def filter(self, audio: bytes) -> bytes:
        self.filter_calls += 1
        if self.fail_filter:
            raise RuntimeError("inference failed")
        return b"filtered"

    async def process_frame(self, frame):
        self.frames.append(frame)

    async def stop(self):
        self.stopped = True


def test_disabled_enhancer_never_imports_optional_provider(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if "rnnoise" in name.lower():
            raise AssertionError(f"unexpected optional import: {name}")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert (
        build_audio_enhancer(SpeakerSettings(enabled=False, enhancer="rnnoise")) is None
    )
    assert build_audio_enhancer(SpeakerSettings(enabled=True, enhancer="none")) is None


@pytest.mark.asyncio
async def test_failed_start_bypasses():
    adapter = OptionalRNNoiseFilter(FakeRNNoise(ready=False))
    pcm = b"\x01\x00" * 320
    await adapter.start(16000)
    assert adapter.effective_name == "none"
    assert await adapter.filter(pcm) == pcm


@pytest.mark.asyncio
async def test_pipecat_initialization_failure_logs_only_once(monkeypatch):
    from pipecat.audio.filters import rnnoise_filter

    class BrokenRNNoise:
        def __init__(self, *, sample_rate):
            raise RuntimeError("model init failed")

    monkeypatch.setattr(rnnoise_filter, "RNNoise", BrokenRNNoise)
    messages = []
    sink = rnnoise_filter.logger.add(
        lambda message: messages.append(str(message)), level="WARNING"
    )
    adapter = OptionalRNNoiseFilter(rnnoise_filter.RNNoiseFilter())
    pcm = b"\x01\x00" * 320
    try:
        await adapter.start(16000)
        assert adapter.effective_name == "none"
        assert await adapter.filter(pcm) == pcm
    finally:
        rnnoise_filter.logger.remove(sink)
    assert len(messages) == 1
    assert "model init failed" in messages[0]


@pytest.mark.asyncio
async def test_start_exception_keeps_pcm_and_releases_delegate():
    delegate = FakeRNNoise(ready=False, fail_start=True)
    adapter = OptionalRNNoiseFilter(delegate)
    await adapter.start(16000)
    assert adapter.effective_name == "none"
    assert await adapter.filter(b"pcm") == b"pcm"
    await adapter.stop()
    assert delegate.stopped


@pytest.mark.asyncio
async def test_inference_failure_is_bypassed_for_the_session():
    import openjarvis.server.voice.speaker_enhancement as enhancement

    messages = []
    sink = enhancement.logger.add(
        lambda message: messages.append(str(message)), level="WARNING"
    )
    delegate = FakeRNNoise(ready=True, fail_filter=True)
    adapter = OptionalRNNoiseFilter(delegate)
    pcm = b"\x01\x00" * 320
    try:
        await adapter.start(16000)
        assert await adapter.filter(pcm) == pcm
        assert await adapter.filter(pcm) == pcm
    finally:
        enhancement.logger.remove(sink)
    assert delegate.filter_calls == 1
    assert adapter.effective_name == "none"
    assert len(messages) == 1
    await adapter.stop()
    assert delegate.stopped


@pytest.mark.asyncio
async def test_ready_delegate_filters_and_receives_control_frames():
    delegate = FakeRNNoise(ready=True)
    adapter = OptionalRNNoiseFilter(delegate)
    await adapter.start(16000)
    assert adapter.effective_name == "rnnoise"
    assert await adapter.filter(b"pcm") == b"filtered"
    frame = object()
    await adapter.process_frame(frame)
    assert delegate.frames == [frame]


def test_missing_provider_falls_back_with_one_diagnostic(monkeypatch):
    import openjarvis.server.voice.speaker_enhancement as enhancement

    original = importlib.util.find_spec

    def missing(name, *args, **kwargs):
        if name == "pyrnnoise":
            return None
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", missing)
    messages = []
    sink = enhancement.logger.add(lambda message: messages.append(str(message)))
    try:
        assert (
            build_audio_enhancer(SpeakerSettings(enabled=True, enhancer="rnnoise"))
            is None
        )
    finally:
        enhancement.logger.remove(sink)
    assert len(messages) == 1
