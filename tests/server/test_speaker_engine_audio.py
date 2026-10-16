"""engine = "audio": OpenJarvis runs speaker fusion through the audio library.

The equivalence tests drive OpenJarvis's SpeakerAudioProcessor and the
AudioSpeakerProcessor adapter through real Pipecat pipelines on the same seeded
input and compare every verdict and STT frame. Tests marked ``needs_audio``
skip without the ``audio`` package (``uv pip install --no-deps -e ../Audio``,
or ``PYTHONPATH=../Audio/src``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest
import tomllib
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

from openjarvis.server.voice import speaker as oj_speaker
from openjarvis.server.voice import speaker_identity as oj_identity
from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor
from openjarvis.server.voice.transcription import SttAudioFrame
from openjarvis.server.voice.turn_detection import SpeakerVerdictFrame

needs_audio = pytest.mark.skipif(
    importlib.util.find_spec("audio") is None,
    reason="the audio package is not installed",
)

FRAME_SAMPLES = 320  # 20 ms microphone frames
ROW_SAMPLES = 1280  # one 80 ms diarizer row per fake diarizer chunk
STT_DELAY = 0.3
FUSION = {
    "enabled": True,
    "diarizer": "sortformer",
    "vision_faces": True,
    "vision_asd": True,
    "identity": "fusion",
}
OLD = SimpleNamespace(
    settings=oj_speaker.SpeakerSettings,
    faces=oj_speaker.FaceTrackBuffer,
    gate=oj_speaker.AudioOnlyGate,
    fusion=oj_identity.FusionGate,
    slot_identity=oj_identity.SlotIdentity,
)


def _new():
    from audio.domain.evidence import FaceTrackBuffer
    from audio.domain.fusion import FusionGate
    from audio.domain.gate import AudioOnlyGate
    from audio.domain.identity import SlotIdentity
    from audio.domain.settings import SpeakerSettings

    return SimpleNamespace(
        settings=SpeakerSettings,
        faces=FaceTrackBuffer,
        gate=AudioOnlyGate,
        fusion=FusionGate,
        slot_identity=SlotIdentity,
    )


class RowDiarizer:
    frame_secs = 0.08
    chunk_samples = ROW_SAMPLES

    def __init__(self, rows):
        self.rows = list(rows)

    def reset(self):
        pass

    def push(self, pcm):
        return np.array([self.rows.pop(0) if self.rows else (0.0, 0.0, 0.0, 0.0)])


class HalfSeparator:
    window_samples = ROW_SAMPLES * 20
    enroll_samples = ROW_SAMPLES * 2

    def separate(self, mix, enroll):
        return (mix * 0.5).astype(np.float32)


def _scenario():
    """Seed 1000 of the parity check: solo, silence, overlap and bystander rows."""
    rng = random.Random(1000)
    patterns = [
        (0.9, 0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 0.0),
        (0.9, 0.9, 0.0, 0.0),
        (0.0, 0.9, 0.0, 0.0),
    ]
    rows, frames, bot = [], [], {}
    for _ in range(6):
        rows += [rng.choice(patterns)] * 4
    for _ in range(len(rows) * 4):
        if rng.random() < 0.05:
            bot[len(frames)] = rng.choice(["start", "stop"])
        level = rng.randrange(200, 4000)
        frames.append(np.full(FRAME_SAMPLES, level, np.int16).tobytes())
    return rows, frames, bot


def _audio_only_gate(impl):
    return impl.gate(impl.settings(enabled=True))


def _voice_locked_gate(impl):
    """Locked on track 7 / slot 0, whose voice already matches the customer."""
    now = time.time()
    faces = impl.faces()
    for k in range(-2, 60):
        faces.add(
            {
                "event": "faces",
                "ts": now + k * 0.1,
                "tracks": [{"track_id": 7, "distance_m": 0.8, "mouth_activity": 0.0}],
            }
        )
    gate = impl.fusion(impl.settings(**FUSION), faces, fsm_state=lambda: "active")
    gate.lock.observe_fsm("active", now - 1.0)
    for i in range(6):
        gate.lock.observe_row(
            now - 0.45 + i * 0.08, [0], anchor=7, anchor_asd_accept=True
        )
    gate.lock.voiceprint.enroll(np.ones(4, np.float32), 2.0)
    gate.lock.slots[0] = impl.slot_identity(voice_sim=0.9)
    return gate


async def _run(processor, started, frames, bot):
    """Feed one processor through a real Pipecat pipeline; collect its output."""
    verdicts, stt, types = [], [], set()

    class Collector(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, SpeakerVerdictFrame):
                types.add(type(frame.verdict))
                verdicts.append(
                    (frame.verdict, frame.locked, frame.overlap_target, frame.source)
                )
            if isinstance(frame, SttAudioFrame):
                stt.append((frame.audio, frame.verdict, frame.reason))
            await self.push_frame(frame, direction)

    worker = PipelineWorker(
        Pipeline([processor, Collector()]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        # Pipecat starts the pipeline asynchronously: feed audio once the
        # processor has handled StartFrame, or early frames burst in together.
        while not started():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        for i, audio in enumerate(frames):
            event = bot.get(i)
            if event == "start":
                await processor.queue_frame(
                    BotStartedSpeakingFrame(), FrameDirection.UPSTREAM
                )
                await asyncio.sleep(0.01)
            elif event == "stop":
                await processor.queue_frame(
                    BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM
                )
                await asyncio.sleep(0.01)
            await worker.queue_frame(
                InputAudioRawFrame(audio=audio, sample_rate=16_000, num_channels=1)
            )
            await asyncio.sleep(0.02)
        await asyncio.sleep(STT_DELAY + 0.2)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=60)
    return verdicts, stt, types


# name: (gate builder, TSE, STT delay, an STT reason the run must show)
CONFIGS = {
    "audio-only+tse": (_audio_only_gate, True, STT_DELAY, "separated"),
    "fusion-locked": (_voice_locked_gate, False, STT_DELAY, "silenced_masked"),
    "audio-only no-delay": (_audio_only_gate, False, 0.0, None),
}


@needs_audio
@pytest.mark.parametrize("name", list(CONFIGS))
def test_audio_engine_emits_the_same_frames_as_the_openjarvis_engine(name):
    from openjarvis.server.voice.speaker_engine_audio import AudioSpeakerProcessor

    build, tse, delay, reason = CONFIGS[name]
    rows, frames, bot = _scenario()

    def args(impl):
        return {
            "diarizer": RowDiarizer(rows),
            "gate": build(impl),
            "executor": ThreadPoolExecutor(max_workers=1),
            "stt_delay_secs": delay,
            "separator": HalfSeparator() if tse else None,
            "separator_executor": ThreadPoolExecutor(max_workers=1),
        }

    old_processor = SpeakerAudioProcessor(**args(OLD))
    old = asyncio.run(
        _run(old_processor, lambda: old_processor._worker is not None, frames, bot)
    )
    new_processor = AudioSpeakerProcessor(**args(_new()))
    new = asyncio.run(
        _run(
            new_processor,
            lambda: new_processor._stream._worker is not None,
            frames,
            bot,
        )
    )
    assert old[0], "the scenario produced no verdicts"
    assert new[2] == {oj_speaker.Verdict}
    assert new[0] == old[0]
    assert new[1] == old[1]
    if reason is None:
        assert new[1] == []
    else:
        assert reason in {r for _, _, r in new[1]}


@needs_audio
def test_audio_engine_shares_the_openjarvis_model_threads():
    from openjarvis.server.voice.speaker_audio import (
        DIARIZER_EXECUTOR,
        SEPARATOR_EXECUTOR,
    )
    from openjarvis.server.voice.speaker_embedding import EMBEDDING_EXECUTOR
    from openjarvis.server.voice.speaker_engine_audio import AudioSpeakerProcessor

    impl = _new()
    processor = AudioSpeakerProcessor(
        diarizer=RowDiarizer([]),
        gate=impl.fusion(
            impl.settings(**FUSION), impl.faces(), fsm_state=lambda: "active"
        ),
        stt_delay_secs=STT_DELAY,
        separator=HalfSeparator(),
        embedder=SimpleNamespace(disabled=False),
    )
    stream = processor._stream
    assert stream._executor is DIARIZER_EXECUTOR
    assert stream._separator_executor is SEPARATOR_EXECUTOR
    assert stream._embeddings._executor is EMBEDDING_EXECUTOR


@needs_audio
@pytest.mark.asyncio
async def test_audio_engine_console_matches_the_old_one_and_saves_the_same_preset(
    tmp_path, monkeypatch
):
    from openjarvis.kiosk.evaluate import get_config, set_config
    from openjarvis.server.voice.speaker_engine_audio import (
        build_audio_speaker_processor,
    )
    from openjarvis.server.voice.speaker_vision import VisionAudioBridge

    settings = oj_speaker.SpeakerSettings(
        enabled=True, diarizer="sortformer", engine="audio"
    )
    diarizer = SimpleNamespace(chunk_samples=320, frame_secs=0.08)
    old_bridge = VisionAudioBridge("ws://localhost:9876", "old", OLD.faces())
    SpeakerAudioProcessor(
        diarizer=diarizer,
        gate=OLD.gate(settings),
        speaker_settings=settings,
        vision_audio=old_bridge,
    )
    new_bridge = VisionAudioBridge("ws://localhost:9876", "new", OLD.faces())
    build_audio_speaker_processor(
        settings,
        fusion=False,
        faces=None,
        bot_voiceprint=None,
        diarizer=diarizer,
        vision_audio=new_bridge,
    )
    old, new = old_bridge.console, new_bridge.console
    assert list(new.values()) == list(old.values())
    assert list(new.snapshot()) == list(old.snapshot())
    assert new.snapshot()["bounds"] == old.snapshot()["bounds"]

    preset = tmp_path / "preset.toml"
    preset.write_text(
        "[kiosk]\napproach_threshold_m = 1.0\n[voice.speaker]\nvoice_match = 0.65\n"
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    new_bridge._stream_id = "stream"
    sent = []

    async def send(payload):
        sent.append(json.loads(payload))

    before = get_config()
    try:
        assert new.tune("approach_threshold_m", 1.4)["value"] == 1.4
        assert get_config().approach_threshold_m == 1.4
        await new_bridge._console_command(
            SimpleNamespace(send=send),
            {"request_id": "save", "action": "save"},
            "stream",
        )
    finally:
        set_config(before)
    assert sent[-1]["ok"] is True
    saved = tomllib.loads(preset.read_text())
    assert saved["kiosk"]["approach_threshold_m"] == 1.4
    assert saved["voice"]["speaker"]["voice_match"] == 0.65


def _build_pipeline(speaker):
    from unittest.mock import MagicMock

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
        speaker=speaker,
        diarizer=SimpleNamespace(chunk_samples=320, frame_secs=0.08),
    )
    return stt._prev  # the speaker slot sits right before the STT


def test_default_engine_keeps_the_openjarvis_processor():
    slot = _build_pipeline(
        oj_speaker.SpeakerSettings(enabled=True, diarizer="sortformer")
    )
    assert type(slot) is SpeakerAudioProcessor


@needs_audio
def test_engine_audio_puts_the_adapter_in_the_speaker_slot():
    from openjarvis.server.voice.speaker_engine_audio import AudioSpeakerProcessor

    slot = _build_pipeline(
        oj_speaker.SpeakerSettings(enabled=True, diarizer="sortformer", engine="audio")
    )
    assert isinstance(slot, AudioSpeakerProcessor)


def test_engine_audio_without_the_package_falls_back_to_the_openjarvis_engine(
    monkeypatch,
):
    import sys

    from loguru import logger

    monkeypatch.setitem(sys.modules, "audio", None)  # import audio now fails
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        slot = _build_pipeline(
            oj_speaker.SpeakerSettings(
                enabled=True, diarizer="sortformer", engine="audio"
            )
        )
    finally:
        logger.remove(sink)
    assert type(slot) is SpeakerAudioProcessor
    assert any("engine=audio" in message for message in messages)
