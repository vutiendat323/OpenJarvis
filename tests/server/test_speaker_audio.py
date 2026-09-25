"""The speaker processor passes audio on and turns diarizer output into verdicts."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    InputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.server.voice.speaker import AudioOnlyGate, SpeakerSettings, Verdict
from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor
from openjarvis.server.voice.turn_detection import SpeakerVerdictFrame

CHUNK = 320  # 20 ms at 16 kHz


class _FakeDiarizer:
    frame_secs = 0.08
    chunk_samples = CHUNK

    def __init__(self, rows):
        self.rows = list(rows)
        self.pushed = 0
        self.resets = 0

    def reset(self):
        self.resets += 1

    def push(self, pcm):
        assert pcm.dtype == np.int16 and len(pcm) == CHUNK
        self.pushed += 1
        return np.array([self.rows.pop(0) if self.rows else (0.0, 0.0, 0.0, 0.0)])


class _Collector(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.audio = 0
        self.verdicts = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.audio += 1
        if isinstance(frame, SpeakerVerdictFrame):
            self.verdicts.append(frame.verdict)
        await self.push_frame(frame, direction)


def _audio(samples=CHUNK, rate=16_000, channels=1):
    return InputAudioRawFrame(
        audio=np.zeros(samples * channels, np.int16).tobytes(),
        sample_rate=rate,
        num_channels=channels,
    )


async def _run(processor, frames, *, until=lambda: True):
    collector = _Collector()
    worker = PipelineWorker(
        Pipeline([processor, collector]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.05)
        for frame in frames:
            if isinstance(frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)):
                await processor.queue_frame(frame, FrameDirection.UPSTREAM)
            else:
                await worker.queue_frame(frame)
        # Wait on the outcome, not a fixed sleep: a cold pipeline can take
        # longer to start than any fixed settle time.
        for _ in range(200):
            if until():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=10)
    return collector


def _processor(rows):
    diarizer = _FakeDiarizer(rows)
    gate = AudioOnlyGate(SpeakerSettings(enabled=True))
    processor = SpeakerAudioProcessor(
        diarizer=diarizer, gate=gate, executor=ThreadPoolExecutor(max_workers=1)
    )
    return processor, diarizer, gate


@pytest.mark.anyio
async def test_audio_passes_through_and_speech_becomes_verdicts():
    processor, diarizer, _ = _processor([(0.9, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0)])

    collector = await _run(
        processor, [_audio(), _audio()], until=lambda: diarizer.pushed == 2
    )

    assert collector.audio == 2
    assert diarizer.pushed == 2
    assert collector.verdicts == [Verdict.ACCEPT]
    # The end-of-session reset is queued behind in-flight work; drain it.
    processor._executor.submit(lambda: None).result(timeout=2)
    assert diarizer.resets >= 2  # session start and end


@pytest.mark.anyio
async def test_partial_frames_are_joined_into_whole_chunks():
    processor, diarizer, _ = _processor([])

    await _run(processor, [_audio(CHUNK // 2)] * 5, until=lambda: diarizer.pushed == 2)

    assert diarizer.pushed == 2


@pytest.mark.anyio
async def test_bot_playback_marks_its_chunks_until_the_tail_passes():
    processor, diarizer, gate = _processor([(0.9, 0.0, 0.0, 0.0)] * 2)

    await _run(
        processor,
        [BotStartedSpeakingFrame(), _audio(), BotStoppedSpeakingFrame(), _audio()],
        until=lambda: diarizer.pushed == 2,
    )

    # Heard only during playback (and its reverb tail): never the target.
    assert diarizer.pushed == 2
    assert gate.target is None


@pytest.mark.anyio
async def test_unsupported_audio_is_skipped_not_fed_to_the_model():
    processor, diarizer, _ = _processor([])

    collector = await _run(processor, [_audio(rate=48_000), _audio(channels=2)])

    assert collector.audio == 2
    assert diarizer.pushed == 0
    assert collector.verdicts == []


@pytest.mark.anyio
async def test_frames_carry_wall_clock_times_for_vision_fusion():
    import time

    seen = []

    class _RecordingGate(AudioOnlyGate):
        def frame(self, probs, *, bot_speaking, t=None):
            seen.append(t)
            return super().frame(probs, bot_speaking=bot_speaking, t=t)

    diarizer = _FakeDiarizer([(0.9, 0.0, 0.0, 0.0)])
    processor = SpeakerAudioProcessor(
        diarizer=diarizer,
        gate=_RecordingGate(SpeakerSettings(enabled=True)),
        executor=ThreadPoolExecutor(max_workers=1),
    )
    before = time.time()
    await _run(processor, [_audio()], until=lambda: diarizer.pushed == 1)

    assert seen and before - 1 <= seen[0] <= time.time()
