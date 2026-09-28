"""The optional microphone enhancer preserves raw PCM when unavailable."""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import sys
import wave
from pathlib import Path

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import EndFrame, InputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.server.voice.speaker import SpeakerSettings
from openjarvis.server.voice.speaker_enhancement import (
    OptionalRNNoiseFilter,
    build_audio_enhancer,
)


class _Capture(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        await self.push_frame(frame, direction)


async def _forward_through_metadata_processor(frame):
    from openjarvis.server.voice.speaker_enhancement import AudioFrameMetadataProcessor

    capture = _Capture()
    worker = PipelineWorker(
        Pipeline([AudioFrameMetadataProcessor(), capture]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.05)
        await worker.queue_frame(frame)
        for _ in range(200):
            if frame in capture.frames:
                break
            await asyncio.sleep(0.01)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=10)
    return capture.frames


@pytest.mark.asyncio
async def test_post_filter_duration_uses_current_pcm_length():
    frame = InputAudioRawFrame(
        audio=b"\x00\x00" * 320, sample_rate=16000, num_channels=1
    )
    frame.audio = b"\x01\x00" * 480
    frame.transport_source = "microphone"
    forwarded = await _forward_through_metadata_processor(frame)
    out = next(item for item in forwarded if isinstance(item, InputAudioRawFrame))
    assert out is frame
    assert out.audio == b"\x01\x00" * 480
    assert out.num_frames == 480
    assert out.sample_rate == 16000 and out.num_channels == 1
    assert out.transport_source == "microphone"


@pytest.mark.asyncio
async def test_post_filter_metadata_preserves_non_audio_frame():
    from pipecat.frames.frames import TextFrame

    marker = TextFrame(text="unchanged")
    assert marker in await _forward_through_metadata_processor(marker)


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


def _write_wav(path: Path, samples: bytes, *, rate=16000, channels=1, width=2):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(samples)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rate", "channels", "width", "message"),
    [(8000, 1, 2, "16000"), (16000, 2, 2, "mono"), (16000, 1, 1, "PCM16")],
)
async def test_replay_rejects_wrong_wav_format(
    tmp_path, rate, channels, width, message
):
    from scripts.voice_enhancement_replay import replay

    source = tmp_path / "source.wav"
    _write_wav(source, b"\x00" * 640, rate=rate, channels=channels, width=width)
    with pytest.raises(ValueError, match=message):
        await replay(source, tmp_path / "output.wav")


@pytest.mark.asyncio
async def test_replay_refuses_input_output_collision(tmp_path):
    from scripts.voice_enhancement_replay import replay

    source = tmp_path / "source.wav"
    _write_wav(source, b"\x00\x00" * 320)
    with pytest.raises(ValueError, match="same|input"):
        await replay(source, source)


class _ReplayFilter:
    def __init__(self, *, buffer_first=False, bypass=False):
        self.effective_name = "rnnoise"
        self.buffer_first = buffer_first
        self.bypass = bypass
        self.calls = []

    async def start(self, sample_rate):
        assert sample_rate == 16000
        if self.bypass:
            self.effective_name = "none"

    async def filter(self, audio):
        self.calls.append(audio)
        if self.buffer_first and len(self.calls) == 1:
            return b""
        return audio

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_replay_writes_pcm16_mono_and_processes_partial_final_chunk(
    tmp_path, monkeypatch
):
    from scripts import voice_enhancement_replay as replay_module

    source = tmp_path / "source.wav"
    output = tmp_path / "output.wav"
    pcm = b"\x01\x00" * 330
    _write_wav(source, pcm)
    fake = _ReplayFilter()
    monkeypatch.setattr(replay_module, "build_audio_enhancer", lambda _: fake)
    report = await replay_module.replay(source, output)
    with wave.open(str(output), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
            16000,
            1,
            2,
        )
        assert wav.readframes(wav.getnframes()) == pcm
    assert [len(chunk) // 2 for chunk in fake.calls] == [320, 10]
    assert (
        report["input_samples"],
        report["output_samples"],
        report["sample_delta"],
    ) == (330, 330, 0)
    assert report["effective"] == "rnnoise"
    assert "si_snr" not in report


@pytest.mark.asyncio
async def test_replay_reports_native_buffering_without_padding(tmp_path, monkeypatch):
    from scripts import voice_enhancement_replay as replay_module

    source = tmp_path / "source.wav"
    output = tmp_path / "output.wav"
    _write_wav(source, b"\x01\x00" * 640)
    fake = _ReplayFilter(buffer_first=True)
    monkeypatch.setattr(replay_module, "build_audio_enhancer", lambda _: fake)
    report = await replay_module.replay(source, output)
    with wave.open(str(output), "rb") as wav:
        assert wav.getnframes() == 320
    assert (
        report["input_samples"],
        report["output_samples"],
        report["sample_delta"],
    ) == (640, 320, -320)
    assert report["first_output_ms"] is not None


@pytest.mark.asyncio
async def test_replay_reports_effective_bypass(tmp_path, monkeypatch):
    from scripts import voice_enhancement_replay as replay_module

    source = tmp_path / "source.wav"
    _write_wav(source, b"\x01\x00" * 320)
    monkeypatch.setattr(
        replay_module, "build_audio_enhancer", lambda _: _ReplayFilter(bypass=True)
    )
    report = await replay_module.replay(source, tmp_path / "output.wav")
    assert report["requested"] == "rnnoise"
    assert report["effective"] == "none"


@pytest.mark.parametrize("report_target", ["input", "input_symlink", "output"])
def test_replay_cli_refuses_report_wav_path_collision(
    tmp_path, monkeypatch, report_target
):
    from scripts.voice_enhancement_replay import main

    source = tmp_path / "source.wav"
    output = tmp_path / "output.wav"
    _write_wav(source, b"\x01\x00" * 320)
    _write_wav(output, b"\x02\x00" * 320)
    original_source = source.read_bytes()
    original_output = output.read_bytes()
    alias = tmp_path / "source-link.wav"
    alias.symlink_to(source)
    report = {
        "input": source,
        "input_symlink": alias,
        "output": output,
    }[report_target]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "voice_enhancement_replay.py",
            str(source),
            str(output),
            "--report",
            str(report),
        ],
    )
    with pytest.raises(ValueError, match="report"):
        main()
    assert source.read_bytes() == original_source
    assert output.read_bytes() == original_output
