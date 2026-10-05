"""The speaker processor passes audio on and turns diarizer output into verdicts."""

from __future__ import annotations

import asyncio
import time as _time
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

from openjarvis.server.voice.speaker import (
    AudioOnlyGate,
    FaceTrackBuffer,
    SpeakerSettings,
    Verdict,
)
from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor
from openjarvis.server.voice.speaker_identity import (
    EmbeddingJob,
    FusionGate,
    SlotIdentity,
)
from openjarvis.server.voice.speaker_vision import AudioSpan
from openjarvis.server.voice.transcription import SttAudioFrame
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
        self.audio_bytes = []
        self.verdicts = []
        self.stt = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.audio += 1
            self.audio_bytes.append(frame.audio)
        if isinstance(frame, SpeakerVerdictFrame):
            self.verdicts.append(frame.verdict)
        if isinstance(frame, SttAudioFrame):
            self.stt.append(frame)
        await self.push_frame(frame, direction)


def _audio(samples=CHUNK, rate=16_000, channels=1):
    return InputAudioRawFrame(
        audio=np.zeros(samples * channels, np.int16).tobytes(),
        sample_rate=rate,
        num_channels=channels,
    )


async def _run(processor, frames, *, until=lambda: True, collector=None):
    collector = collector or _Collector()
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


def _queued_chunk_ends(processor, spans):
    ends = []
    for span in spans:
        processor._enqueue(_audio(), span)
        while not processor._queue.empty():
            ends.append(processor._queue.get_nowait()[2])
    return ends


def test_chunk_end_times_follow_the_sample_clock_at_unix_time():
    processor, _, _ = _processor([])
    base = 1790000000.0
    spans = [AudioSpan(base + i * 0.02, base + (i + 1) * 0.02, "s1") for i in range(3000)]
    ends = _queued_chunk_ends(processor, spans)
    assert len(ends) == 3000
    assert max(abs(e - (base + (i + 1) * 0.02)) for i, e in enumerate(ends)) < 1e-6


def test_arrival_jitter_without_a_bridge_stream_keeps_diarizer_audio():
    # Spans without a stream are arrival-timed (bridge not ready, Vision busy
    # or down): network jitter there must not discard pending speech.
    processor, _, _ = _processor([])
    starts = [100.0 + i * 0.02 + (0.03 if i >= 3 else 0.0) for i in range(6)]
    ends = []
    for s in starts:
        processor._enqueue(_audio(CHUNK // 2), AudioSpan(s, s + 0.01, None))
        while not processor._queue.empty():
            ends.append(processor._queue.get_nowait()[2])
    assert len(ends) == 3


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


class _FixedGate:
    target = None
    last_evidence = None
    overlap = False

    def __init__(self, verdict):
        self.verdict = verdict

    def frame(self, probs, *, bot_speaking, t=None):
        return self.verdict


def _loud(samples=CHUNK):
    return InputAudioRawFrame(
        audio=(np.ones(samples, np.int16) * 1000).tobytes(),
        sample_rate=16_000,
        num_channels=1,
    )


def _masking_processor(verdict, delay):
    diarizer = _FakeDiarizer([(0.9, 0.0, 0.0, 0.0)] * 4)
    processor = SpeakerAudioProcessor(
        diarizer=diarizer,
        gate=_FixedGate(verdict),
        executor=ThreadPoolExecutor(max_workers=1),
        stt_delay_secs=delay,
    )
    return processor, diarizer


@pytest.mark.anyio
async def test_rejected_speech_reaches_gemini_as_silence():
    processor, diarizer = _masking_processor(Verdict.REJECT, 0.05)

    collector = await _run(
        processor, [_loud(), _loud()], until=lambda: diarizer.pushed == 2
    )

    assert collector.audio == 2  # the live mic still reaches the VAD
    assert len(collector.stt) == 2
    assert all(not any(frame.audio) for frame in collector.stt)


@pytest.mark.anyio
async def test_sound_with_no_diarized_voice_reaches_gemini_as_silence():
    """A quiet phone video trips the VAD, not the diarizer: Gemini hears nothing."""
    processor, diarizer = _masking_processor(None, 0.05)

    collector = await _run(
        processor, [_loud(), _loud()], until=lambda: diarizer.pushed == 2
    )

    assert collector.audio == 2
    assert len(collector.stt) == 2
    assert all(not any(frame.audio) for frame in collector.stt)


@pytest.mark.anyio
async def test_accepted_speech_reaches_gemini_unchanged():
    processor, diarizer = _masking_processor(Verdict.ACCEPT, 0.05)

    collector = await _run(
        processor, [_loud(), _loud()], until=lambda: diarizer.pushed == 2
    )

    assert [frame.audio for frame in collector.stt] == [_loud().audio] * 2


@pytest.mark.anyio
async def test_asd_bridge_receives_original_audio_before_stt_masking():
    import time

    class FakeBridge:
        def __init__(self):
            self.offered = []
            self.started = self.closed = 0
            self.stream_id = "stream-1"

        async def start(self):
            self.started += 1

        def offer(self, audio, sample_rate, num_channels):
            self.offered.append((audio, sample_rate, num_channels))
            end = time.time()
            return AudioSpan(end - len(audio) / 32000, end, self.stream_id)

        async def close(self):
            self.closed += 1

    bridge = FakeBridge()
    diarizer = _FakeDiarizer([(0.9, 0, 0, 0)] * 2)
    processor = SpeakerAudioProcessor(
        diarizer=diarizer,
        gate=_FixedGate(Verdict.REJECT),
        executor=ThreadPoolExecutor(max_workers=1),
        stt_delay_secs=0.05,
        vision_audio=bridge,
    )
    collector = await _run(
        processor, [_loud(), _loud()], until=lambda: diarizer.pushed == 2
    )
    assert bridge.started == 1
    assert [item[0] for item in bridge.offered] == [_loud().audio] * 2
    assert all(item[1:] == (16000, 1) for item in bridge.offered)
    assert collector.audio_bytes == [_loud().audio] * 2
    assert all(not any(frame.audio) for frame in collector.stt)
    assert bridge.closed >= 1


@pytest.mark.anyio
async def test_asd_diarizer_uses_sample_timeline():
    seen = []

    class Gate(_FixedGate):
        def frame(self, probs, *, bot_speaking, t=None):
            seen.append(t)
            return super().frame(probs, bot_speaking=bot_speaking, t=t)

    class Bridge:
        stream_id = "stream-1"

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            return AudioSpan(100.0, 100.02, self.stream_id)

        async def close(self):
            pass

    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([(0.9, 0, 0, 0)]),
        gate=Gate(Verdict.ACCEPT),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=Bridge(),
    )
    await _run(processor, [_audio()], until=lambda: bool(seen))
    assert seen == [pytest.approx(100.02)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("timed_out", "expected"),
    [(False, Verdict.ACCEPT), (True, Verdict.REJECT)],
)
async def test_asd_result_at_wait_return_respects_deadline(timed_out, expected):
    import time

    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    base_track = {
        "track_id": 2,
        "distance_m": 0.7,
        "mouth_activity": 0.05,
        "active_speaker_probability": None,
    }
    faces.add({"event": "faces", "ts": now, "tracks": [base_track]})

    class Bridge:
        stream_id = "current"
        last_wait_timed_out = False

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            return AudioSpan(now - 0.02, now, self.stream_id)

        async def wait_for_evidence(self, faces, t0, t1, *, timeout):
            await asyncio.sleep(timeout if timed_out else 0.02)
            track = dict(base_track)
            track["asd"] = {
                "stream_id": "current",
                "t0": now - 0.8,
                "frame_secs": 0.04,
                "probabilities": [0.8] * 25,
            }
            faces.add({"event": "faces", "ts": now, "tracks": [track]})
            self.last_wait_timed_out = timed_out

        async def close(self):
            pass

    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([(0, 0.9, 0, 0)]),
        gate=AudioOnlyGate(
            SpeakerSettings(enabled=True, vision_faces=True, vision_asd=True), faces
        ),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=Bridge(),
    )
    collector = _Collector()
    await _run(
        processor,
        [_audio()],
        collector=collector,
        until=lambda: bool(collector.verdicts),
    )
    assert collector.audio == 1
    assert collector.verdicts == [expected]


@pytest.mark.anyio
async def test_asd_wait_is_skipped_without_eligible_anchor():
    import time

    now = time.time()
    faces = FaceTrackBuffer()
    faces.add(
        {
            "event": "faces",
            "ts": now,
            "tracks": [
                {
                    "track_id": 2,
                    "distance_m": 2.0,
                    "mouth_activity": 0.9,
                }
            ],
        }
    )

    class Bridge:
        stream_id = "current"
        waits = 0

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            return AudioSpan(now - 0.02, now, self.stream_id)

        async def wait_for_evidence(self, faces, t0, t1, *, timeout):
            self.waits += 1

        async def close(self):
            pass

    bridge = Bridge()
    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([(0, 0.9, 0, 0)]),
        gate=AudioOnlyGate(
            SpeakerSettings(enabled=True, vision_faces=True, vision_asd=True), faces
        ),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=bridge,
    )
    collector = await _run(processor, [_audio()])
    assert collector.verdicts == [Verdict.UNCERTAIN]
    assert bridge.waits == 0


@pytest.mark.anyio
async def test_late_asd_keeps_mar_verdict_and_does_not_hold_live_audio():
    import time

    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    track = {"track_id": 2, "distance_m": 0.7, "mouth_activity": 0.05}
    faces.add({"event": "faces", "ts": now, "tracks": [track]})
    collector = _Collector()

    class Bridge:
        stream_id = "current"

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            return AudioSpan(now - 0.02, now, self.stream_id)

        async def wait_for_evidence(self, faces, t0, t1, *, timeout):
            assert collector.audio == 1
            await asyncio.sleep(timeout)

            async def publish_late():
                await asyncio.sleep(0.02)
                scored = dict(track)
                scored["asd"] = {
                    "stream_id": "current",
                    "t0": now - 0.8,
                    "frame_secs": 0.04,
                    "probabilities": [0.9] * 25,
                }
                faces.add({"event": "faces", "ts": now, "tracks": [scored]})

            asyncio.create_task(publish_late())

        async def close(self):
            pass

    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([(0, 0.9, 0, 0)]),
        gate=AudioOnlyGate(
            SpeakerSettings(enabled=True, vision_faces=True, vision_asd=True), faces
        ),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=Bridge(),
    )
    await _run(
        processor,
        [_audio()],
        collector=collector,
        until=lambda: bool(collector.verdicts),
    )
    assert faces.active_speaker(
        2, now - 0.08, now, stream_id="current", now=now
    ) == pytest.approx(0.9)
    assert collector.verdicts == [Verdict.REJECT]


@pytest.mark.anyio
async def test_asd_arriving_between_row_deliveries_cannot_change_this_chunk():
    import time

    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    track = {"track_id": 2, "distance_m": 0.7, "mouth_activity": 0.05}
    faces.add({"event": "faces", "ts": now, "tracks": [track]})

    class TwoRows(_FakeDiarizer):
        def push(self, pcm):
            self.pushed += 1
            return np.array([(0, 0.9, 0, 0), (0, 0.9, 0, 0)])

    class Bridge:
        stream_id = "current"

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            return AudioSpan(now - 0.02, now, self.stream_id)

        async def wait_for_evidence(self, faces, t0, t1, *, timeout):
            pass

        async def close(self):
            pass

    class PublishingProcessor(SpeakerAudioProcessor):
        published = False

        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            if isinstance(frame, SpeakerVerdictFrame) and not self.published:
                self.published = True
                scored = dict(track)
                scored["asd"] = {
                    "stream_id": "current",
                    "t0": now - 0.8,
                    "frame_secs": 0.04,
                    "probabilities": [0.9] * 25,
                }
                faces.add({"event": "faces", "ts": now, "tracks": [scored]})
            await super().push_frame(frame, direction)

    processor = PublishingProcessor(
        diarizer=TwoRows([]),
        gate=AudioOnlyGate(
            SpeakerSettings(enabled=True, vision_faces=True, vision_asd=True), faces
        ),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=Bridge(),
    )
    collector = _Collector()
    await _run(
        processor,
        [_audio()],
        collector=collector,
        until=lambda: len(collector.verdicts) == 2,
    )
    assert collector.verdicts == [Verdict.REJECT, Verdict.REJECT]


@pytest.mark.anyio
async def test_later_row_rechecks_asd_age_after_slow_downstream_delivery():
    import time

    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")

    class TwoRows(_FakeDiarizer):
        def push(self, pcm):
            self.pushed += 1
            return np.array([(0, 0.9, 0, 0), (0, 0.9, 0, 0)])

    class Bridge:
        stream_id = "current"

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            end = time.time() - 0.2
            faces.add(
                {
                    "event": "faces",
                    "ts": end,
                    "tracks": [
                        {
                            "track_id": 2,
                            "distance_m": 0.7,
                            "mouth_activity": 0.05,
                            "asd": {
                                "stream_id": "current",
                                "t0": end - 0.8,
                                "frame_secs": 0.04,
                                "probabilities": [0.9] * 25,
                            },
                        }
                    ],
                }
            )
            return AudioSpan(end - 0.02, end, self.stream_id)

        async def wait_for_evidence(self, faces, t0, t1, *, timeout):
            pass

        async def close(self):
            pass

    class SlowProcessor(SpeakerAudioProcessor):
        delayed = False

        async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
            await super().push_frame(frame, direction)
            if isinstance(frame, SpeakerVerdictFrame) and not self.delayed:
                self.delayed = True
                await asyncio.sleep(1.15)

    processor = SlowProcessor(
        diarizer=TwoRows([]),
        gate=AudioOnlyGate(
            SpeakerSettings(enabled=True, vision_faces=True, vision_asd=True), faces
        ),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=Bridge(),
    )
    collector = _Collector()
    await _run(
        processor,
        [_audio()],
        collector=collector,
        until=lambda: len(collector.verdicts) == 2,
    )
    assert collector.verdicts == [Verdict.ACCEPT, Verdict.REJECT]


@pytest.mark.anyio
async def test_asd_gap_reanchors_partial_diarizer_pcm_with_same_stream_tag():
    rows = []
    chunks = []

    class Gate(_FixedGate):
        def frame(self, probs, *, bot_speaking, t=None):
            rows.append(t)
            return super().frame(probs, bot_speaking=bot_speaking, t=t)

    class Diarizer(_FakeDiarizer):
        def push(self, pcm):
            chunks.append(pcm.copy())
            return super().push(pcm)

    class Bridge:
        stream_id = None

        def __init__(self):
            self.spans = iter(
                (
                    AudioSpan(100.0, 100.01, None),
                    AudioSpan(101.0, 101.01, None),
                    AudioSpan(101.01, 101.02, None),
                )
            )

        async def start(self):
            pass

        def offer(self, audio, sample_rate, num_channels):
            return next(self.spans)

        async def close(self):
            pass

    processor = SpeakerAudioProcessor(
        diarizer=Diarizer([(0.9, 0, 0, 0)]),
        gate=Gate(Verdict.ACCEPT),
        executor=ThreadPoolExecutor(max_workers=1),
        vision_audio=Bridge(),
    )
    frames = [
        InputAudioRawFrame(
            audio=(np.ones(CHUNK // 2, np.int16) * level).tobytes(),
            sample_rate=16_000,
            num_channels=1,
        )
        for level in (1, 2, 3)
    ]
    await _run(processor, frames, until=lambda: bool(rows))
    assert rows == [pytest.approx(101.02)]
    assert chunks[0].tolist() == [2] * (CHUNK // 2) + [3] * (CHUNK // 2)


@pytest.mark.anyio
async def test_gemini_copy_waits_out_the_delay():
    processor, diarizer = _masking_processor(Verdict.ACCEPT, 0.4)
    collector = _Collector()
    early = []

    def until():
        if diarizer.pushed == 1 and not early:
            early.append(len(collector.stt))
        return diarizer.pushed == 1 and bool(collector.stt)

    await _run(processor, [_loud()], until=until, collector=collector)

    assert early == [0]
    assert len(collector.stt) == 1


@pytest.mark.anyio
async def test_without_a_delay_no_gemini_copy_is_made():
    processor, diarizer, _ = _processor([(0.9, 0.0, 0.0, 0.0)])

    collector = await _run(processor, [_audio()], until=lambda: diarizer.pushed == 1)

    assert collector.stt == []


FRAME = 1280  # one 80 ms diarizer frame


class _FakeSeparator:
    window_samples = FRAME * 20
    enroll_samples = FRAME * 2

    def __init__(self):
        self.calls = []

    def separate(self, mix, enroll):
        self.calls.append((mix.copy(), enroll.copy()))
        return np.full(len(mix), 0.5, np.float32)  # "the customer's voice"


def _separating_processor():
    separator = _FakeSeparator()
    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([]),
        gate=_FixedGate(Verdict.ACCEPT),
        executor=ThreadPoolExecutor(max_workers=1),
        stt_delay_secs=0.5,
        separator=separator,
        separator_executor=ThreadPoolExecutor(max_workers=1),
    )
    pushed = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, SttAudioFrame):
            pushed.append(np.frombuffer(frame.audio, np.int16))

    processor.push_frame = push
    return processor, separator, pushed


def _line(processor, frames):
    """Queue 80 ms mic frames for STT with their (verdict, overlap) marks."""
    for i, (level, verdict, overlap) in enumerate(frames):
        start = 100.0 + i * 0.08
        processor._remember_verdict(start, verdict, overlap)
        frame = InputAudioRawFrame(
            audio=(np.ones(FRAME, np.int16) * level).tobytes(),
            sample_rate=16_000,
            num_channels=1,
        )
        processor._stt_line.append((start + 0.08, frame))


def _enroll(processor):
    processor._add_enrollment(np.ones(FRAME * 2, np.int16) * 300)


@pytest.mark.anyio
async def test_overlap_is_held_and_replaced_by_the_customers_voice():
    processor, separator, pushed = _separating_processor()
    _enroll(processor)
    alone, both = (1000, Verdict.ACCEPT, False), (2000, Verdict.UNCERTAIN, True)
    _line(processor, [alone, alone, both, both, alone])

    await processor._release_stt(200.0)

    assert [int(p[0]) for p in pushed] == [1000, 1000, 16383, 16383, 1000]
    assert processor._verdict_values[2:4] == [Verdict.UNCERTAIN] * 2
    ((mix, enroll),) = separator.calls
    # The separator sees the clean lead-in as context, then the held overlap.
    assert len(mix) == FRAME * 4 and mix[-1] == pytest.approx(2000 / 32768)
    assert len(enroll) == FRAME * 2


@pytest.mark.anyio
async def test_rejected_overlap_reaches_gemini_as_silence_not_separated():
    processor, separator, pushed = _separating_processor()
    _enroll(processor)
    both, video = (2000, Verdict.UNCERTAIN, True), (2000, Verdict.REJECT, True)
    _line(processor, [both, video, video, (1000, Verdict.ACCEPT, False)])

    await processor._release_stt(200.0)

    # TSE output still carried the video live; a REJECTed frame must not.
    assert [int(p[0]) for p in pushed] == [16383, 0, 0, 1000]


@pytest.mark.anyio
async def test_overlap_before_enrollment_goes_to_gemini_as_the_mix():
    processor, separator, pushed = _separating_processor()
    _line(processor, [(2000, Verdict.UNCERTAIN, True)] * 2)

    await processor._release_stt(200.0)

    assert [int(p[0]) for p in pushed] == [2000, 2000] and separator.calls == []


@pytest.mark.anyio
async def test_drain_separates_overlap_still_held_at_end_of_utterance():
    processor, separator, pushed = _separating_processor()
    _enroll(processor)
    _line(processor, [(2000, Verdict.UNCERTAIN, True)] * 2)

    await processor._release_stt(200.0)
    assert pushed == []  # held: the overlap has not ended yet

    await processor.drain()
    assert [int(p[0]) for p in pushed] == [16383, 16383]


@pytest.mark.anyio
async def test_teardown_sends_held_overlap_without_separating():
    import math

    processor, separator, pushed = _separating_processor()
    _enroll(processor)
    _line(processor, [(2000, Verdict.UNCERTAIN, True)] * 2)

    await processor._release_stt(math.inf, separate=False)

    assert [int(p[0]) for p in pushed] == [2000, 2000] and separator.calls == []


class _AsdEvidenceGate(_FixedGate):
    vision_asd = True

    def __init__(self, source):
        super().__init__(Verdict.ACCEPT)
        self.last_evidence_detail = {"source": source}


async def _enrolled_after_accept(source):
    diarizer = _FakeDiarizer([(0.9, 0.0, 0.0, 0.0)] * 2)
    processor = SpeakerAudioProcessor(
        diarizer=diarizer,
        gate=_AsdEvidenceGate(source),
        executor=ThreadPoolExecutor(max_workers=1),
        stt_delay_secs=0.05,
        separator=_FakeSeparator(),
        separator_executor=ThreadPoolExecutor(max_workers=1),
    )
    await _run(processor, [_loud(), _loud()], until=lambda: diarizer.pushed == 2)
    return processor._enrolled


@pytest.mark.anyio
async def test_with_asd_only_asd_confirmed_speech_enrolls_the_customer():
    # Live 2026-09-29: with ASD late, MAR-only ACCEPTs during a playing video
    # filled up to 34 of 37 enrollment frames, so TSE extracted the video.
    assert await _enrolled_after_accept("mar") == 0
    assert await _enrolled_after_accept("asd") > 0


def test_enrollment_keeps_the_latest_clean_speech():
    processor, _, _ = _separating_processor()
    for level in (1, 2, 3):
        processor._add_enrollment(np.ones(FRAME, np.int16) * level * 1000)

    enroll = np.concatenate(processor._enroll)
    assert len(enroll) == FRAME * 2 and enroll[0] == pytest.approx(2000 / 32768)


# ---------------------------------------------------------------------------
# Fusion: locked-customer masking, segments for embeddings, ASD pin sync.
# ---------------------------------------------------------------------------

_FUSION = SpeakerSettings(enabled=True, diarizer="sortformer", vision_faces=True,
                          vision_asd=True, identity="fusion")
_ROW = 1280  # one 80 ms diarizer row


def _fusion_gate(locked=True, faces=None, now=100.0):
    gate = FusionGate(_FUSION, faces or FaceTrackBuffer(), fsm_state=lambda: "active")
    if locked:
        gate.lock.observe_fsm("active", now - 1.0)
        for i in range(6):
            gate.lock.observe_row(now - 0.45 + i * 0.08, [0], anchor=7,
                                  anchor_asd_accept=True)
    return gate


def _fusion_processor(gate, *, stt_delay_secs=0.5, **kwargs):
    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([]), gate=gate,
        executor=ThreadPoolExecutor(max_workers=1),
        stt_delay_secs=stt_delay_secs, **kwargs,
    )
    pushed = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, SttAudioFrame):
            pushed.append(np.frombuffer(frame.audio, np.int16))

    processor.push_frame = push
    return processor, pushed


def _unverdicted(processor, arrival, level=1000):
    processor._stt_line.append((arrival, InputAudioRawFrame(
        audio=(np.ones(FRAME, np.int16) * level).tobytes(),
        sample_rate=16_000, num_channels=1)))


@pytest.mark.anyio
@pytest.mark.parametrize("verdict, masked, expected", [
    (Verdict.ACCEPT, False, 1000),
    (Verdict.REJECT, False, 0),
    (None, False, 0),
    (Verdict.UNCERTAIN, True, 0),
])
async def test_fusion_releases_decided_audio_before_the_stt_deadline(
    verdict, masked, expected,
):
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    processor._remember_verdict(100.0, verdict, mask=masked)
    processor._remember_verdict(100.08, None)
    processor._remember_verdict(100.16, None)
    _unverdicted(processor, 100.08)

    # Now is 100.42: the row is decided after 340 ms, before its 700 ms cap.
    await processor._release_stt(100.42 - processor._stt_delay)

    assert [int(p[0]) for p in pushed] == [expected]
    assert not processor._stt_line
    assert processor.stt_masked_no_verdict == 0


@pytest.mark.anyio
async def test_fusion_waits_for_a_late_verdict_then_releases_without_extra_delay():
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    _unverdicted(processor, 100.08)
    await processor._release_stt(99.9)
    assert pushed == []
    assert len(processor._stt_line) == 1
    assert processor.stt_masked_no_verdict == 0

    processor._remember_verdict(100.0, Verdict.ACCEPT)
    await processor._release_stt(99.9)
    assert [int(p[0]) for p in pushed] == [1000]


@pytest.mark.anyio
async def test_fusion_keeps_the_deadline_and_audio_order_when_a_verdict_is_missing():
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    _unverdicted(processor, 100.08)
    _unverdicted(processor, 100.16, level=2000)
    processor._remember_verdict(100.08, Verdict.ACCEPT)
    await processor._release_stt(100.07)
    assert pushed == []  # A decided later frame must not overtake the first.

    await processor._release_stt(100.08)
    assert [int(p[0]) for p in pushed] == [0, 2000]
    assert processor.stt_masked_no_verdict == 1


@pytest.mark.anyio
async def test_fusion_does_not_release_a_frame_with_only_partial_verdict_coverage():
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    # One 80 ms audio frame spans two rows: only its midpoint/first row is ready.
    _unverdicted(processor, 100.10)
    processor._remember_verdict(100.0, Verdict.ACCEPT)
    await processor._release_stt(99.9)
    assert pushed == []

    processor._remember_verdict(100.08, Verdict.ACCEPT)
    await processor._release_stt(99.9)
    assert [int(p[0]) for p in pushed] == [1000]


@pytest.mark.anyio
async def test_the_voiceless_onset_of_accepted_speech_reaches_gemini():
    """Live 2026-10-05: Sortformer scores the first 80-160 ms of an utterance
    below threshold, so the first syllable ("Thêm", "Trà") was silenced and
    Gemini misheard the rest ("mang đi" -> "Mandy")."""
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    processor._remember_verdict(100.0, None)
    processor._remember_verdict(100.08, None)
    processor._remember_verdict(100.16, Verdict.ACCEPT)
    _unverdicted(processor, 100.08)
    _unverdicted(processor, 100.16, level=2000)
    _unverdicted(processor, 100.24, level=3000)

    await processor._release_stt(99.9)

    assert [int(p[0]) for p in pushed] == [1000, 2000, 3000]


@pytest.mark.anyio
async def test_a_voiceless_row_waits_for_what_follows_before_it_is_silenced():
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    processor._remember_verdict(100.0, None)
    _unverdicted(processor, 100.08)

    await processor._release_stt(99.9)
    assert pushed == []  # Could still be the onset of the customer's speech.

    processor._remember_verdict(100.08, Verdict.ACCEPT)
    await processor._release_stt(99.9)
    assert [int(p[0]) for p in pushed] == [1000]


@pytest.mark.anyio
async def test_voiceless_sound_never_followed_by_accepted_speech_stays_silenced():
    """A quiet phone video: no ACCEPT follows, so Gemini still hears nothing,
    including at the deadline when the following rows never arrived."""
    processor, pushed = _fusion_processor(_fusion_gate(), stt_delay_secs=0.7)
    processor._remember_verdict(100.0, None)
    processor._remember_verdict(100.08, None)
    processor._remember_verdict(100.16, Verdict.REJECT)
    processor._remember_verdict(100.24, None)
    _unverdicted(processor, 100.08)
    _unverdicted(processor, 100.32, level=2000)

    await processor._release_stt(100.32)

    assert [int(p[0]) for p in pushed] == [0, 0]


@pytest.mark.anyio
async def test_after_the_lock_audio_without_a_verdict_is_silenced():
    processor, pushed = _fusion_processor(_fusion_gate())
    _line(processor, [(1000, Verdict.ACCEPT, False)])
    _unverdicted(processor, 100.5)
    await processor._release_stt(200.0)
    assert [int(p[0]) for p in pushed] == [1000, 0]
    assert processor.stt_masked_no_verdict == 1


@pytest.mark.anyio
async def test_before_the_lock_audio_without_a_verdict_still_passes():
    processor, pushed = _fusion_processor(_fusion_gate(locked=False))
    _line(processor, [(1000, Verdict.ACCEPT, False)])
    _unverdicted(processor, 100.5)
    await processor._release_stt(200.0)
    assert [int(p[0]) for p in pushed] == [1000, 1000]


@pytest.mark.anyio
async def test_masked_rows_reach_stt_as_silence():
    processor, pushed = _fusion_processor(_fusion_gate())
    processor._remember_verdict(100.0, Verdict.UNCERTAIN, False, mask=True)
    _unverdicted(processor, 100.08)
    await processor._release_stt(200.0)
    assert [int(p[0]) for p in pushed] == [0]


@pytest.mark.parametrize("locked, overlap, visible, separator, masked", [
    (True, False, False, False, True),    # unconfirmed after the lock
    (True, True, True, False, False),     # target overlap, visibly speaking: the mix
    (True, True, False, True, False),     # target overlap, TSE enrolled
    (True, True, False, False, True),     # target overlap, no TSE, face still
    (False, False, False, False, False),  # before the lock: unchanged
])
def test_mask_row_policy(locked, overlap, visible, separator, masked):
    gate = _fusion_gate(locked=locked)
    kwargs = {}
    if separator:
        kwargs = {"separator": _FakeSeparator(),
                  "separator_executor": ThreadPoolExecutor(max_workers=1)}
    processor, _ = _fusion_processor(gate, **kwargs)
    if separator:
        processor._add_enrollment(np.ones(3 * 16000, np.int16))
    gate.target_overlap, gate.target_speaking_visibly = overlap, visible
    assert processor._mask_row(Verdict.UNCERTAIN) is masked
    assert processor._mask_row(Verdict.ACCEPT) is False


def test_after_the_lock_only_asd_confirmed_target_rows_enroll_tse():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(
        gate, separator=_FakeSeparator(),
        separator_executor=ThreadPoolExecutor(max_workers=1),
    )
    gate.last_source, gate.row_is_target = "asd", True
    assert processor._confirmed_customer()
    gate.last_source = "mar"
    assert not processor._confirmed_customer()
    gate.last_source, gate.row_is_target = "asd", False
    assert not processor._confirmed_customer()


class _Recorder:
    def __init__(self):
        self.jobs = []
        self.cleared = 0

    def submit(self, job):
        self.jobs.append(job)

    def clear(self):
        self.cleared += 1


def _row_pcm():
    return np.ones(_ROW, np.int16)


def test_solo_rows_become_one_target_confirmed_segment():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    for i in range(13):
        gate.row_voices, gate.row_asd_track = [0], 7
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    (job,) = recorder.jobs
    assert job.slot == 0 and job.target_confirmed and job.seconds >= 1.0
    assert job.epoch == gate.lock.epoch and job.segment_end == pytest.approx(101.96)


def test_bot_audio_and_overlap_rows_are_never_collected():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    for i in range(20):
        gate.row_voices = [0]
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, True)
        gate.row_voices = [0, 1]
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    assert recorder.jobs == []


def test_mostly_unconfirmed_rows_do_not_enroll():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    for i in range(13):
        gate.row_voices, gate.row_asd_track = [0], (7 if i < 5 else None)
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    assert recorder.jobs[0].target_confirmed is False


def test_a_new_epoch_drops_collected_audio():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    gate.row_voices = [0]
    processor._after_fusion_row(_row_pcm(), 101.0, False)
    gate.lock.observe_fsm("cleanup", 101.1)
    processor._after_fusion_row(_row_pcm(), 101.2, False)
    assert recorder.cleared == 1
    assert processor._segments[0].seconds == pytest.approx(0.08)


def test_pin_follows_the_lock_once_per_change():
    class Bridge:
        def __init__(self):
            self.pins = []

        def pin(self, track):
            self.pins.append(track)

    gate = _fusion_gate()
    bridge = Bridge()
    processor, _ = _fusion_processor(gate, vision_audio=bridge)
    processor._after_fusion_row(_row_pcm(), 100.0, False)
    processor._after_fusion_row(_row_pcm(), 100.1, False)
    processor._after_fusion_row(_row_pcm(), 101.5, False)  # target unseen > 0.5 s
    assert bridge.pins == [7, None]


def test_embedding_results_update_the_lock_and_stale_ones_do_not():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    job = EmbeddingJob(epoch=gate.lock.epoch, slot=0, segment_end=101.0, seconds=2.0,
                       pcm=np.zeros(16000, np.int16), target_confirmed=True)
    processor._on_embedding(job, np.ones(4, np.float32), 12.0)
    assert gate.lock.voice_ready
    gate.lock.observe_fsm("cleanup", 102.0)
    processor._on_embedding(job, np.ones(4, np.float32), 12.0)
    assert gate.lock.voiceprint.seconds == 0.0


@pytest.mark.anyio
async def test_without_an_embedder_rows_still_flow_and_nothing_is_submitted():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    assert processor._embeddings is None
    gate.row_voices = [0]
    for i in range(20):
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    assert processor._segments == {}


class _OneChunkDiarizer:
    """Returns all its rows for one chunk of that many 80 ms frames' audio."""

    frame_secs = 0.08

    def __init__(self, rows):
        self.rows = list(rows)
        self.chunk_samples = CHUNK * len(self.rows)
        self.pushed = 0
        self.resets = 0

    def reset(self):
        self.resets += 1

    def push(self, pcm):
        assert pcm.dtype == np.int16 and len(pcm) == self.chunk_samples
        self.pushed += 1
        return np.array(self.rows)


@pytest.mark.anyio
async def test_locked_customer_marks_an_unknown_voice_for_masking():
    # All 12 rows arrive as one diarizer chunk, so the bounded chunk queue
    # (_MAX_QUEUED_CHUNKS) never drops any of them. Per-frame audio levels
    # cannot be matched to rows here; the mask decision per row is what this
    # pins. The explicit-time tests above cover mask -> silent STT audio.
    now = _time.time()
    faces = FaceTrackBuffer()
    for k in range(-2, 25):
        faces.add({"event": "faces", "ts": now + k * 0.1, "tracks": [
            {"track_id": 7, "distance_m": 0.8, "mouth_activity": 0.0}]})
    gate = _fusion_gate(faces=faces, now=now)
    gate.lock.voiceprint.enroll(np.ones(4, np.float32), 2.0)
    gate.lock.slots[0] = SlotIdentity(voice_sim=0.9)
    rows = [(0.9, 0.0, 0.0, 0.0)] * 6 + [(0.0, 0.0, 0.0, 0.9)] * 6
    diarizer = _OneChunkDiarizer(rows)
    assert diarizer.chunk_samples == CHUNK * 12
    processor = SpeakerAudioProcessor(diarizer=diarizer, gate=gate,
                                      executor=ThreadPoolExecutor(max_workers=1),
                                      stt_delay_secs=0.05)
    collector = await _run(processor, [_loud() for _ in range(12)],
                           until=lambda: diarizer.pushed == 1)
    assert collector.verdicts == [Verdict.ACCEPT] * 6 + [Verdict.UNCERTAIN] * 6
    assert processor._masked_values == [False] * 6 + [True] * 6


class _FrameCollector(_Collector):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def process_frame(self, frame, direction):
        if isinstance(frame, SpeakerVerdictFrame):
            self.frames.append(frame)
        await super().process_frame(frame, direction)


def _voice_locked_gate():
    """Locked on track 7 / slot 0, whose voice already matches the customer."""
    now = _time.time()
    faces = FaceTrackBuffer()
    for k in range(-2, 25):
        faces.add({"event": "faces", "ts": now + k * 0.1, "tracks": [
            {"track_id": 7, "distance_m": 0.8, "mouth_activity": 0.0}]})
    gate = _fusion_gate(faces=faces, now=now)
    gate.lock.voiceprint.enroll(np.ones(4, np.float32), 2.0)
    gate.lock.slots[0] = SlotIdentity(voice_sim=0.9)
    return gate


@pytest.mark.anyio
async def test_verdict_frames_carry_the_lock_and_their_source():
    gate = _voice_locked_gate()
    diarizer = _FakeDiarizer([(0.9, 0.0, 0.0, 0.0)] * 2)
    processor = SpeakerAudioProcessor(diarizer=diarizer, gate=gate,
                                      executor=ThreadPoolExecutor(max_workers=1))
    collector = _FrameCollector()
    await _run(processor, [_loud(), _loud()], collector=collector,
               until=lambda: len(collector.frames) == 2)
    assert [(f.verdict, f.locked, f.overlap_target, f.source)
            for f in collector.frames] == [(Verdict.ACCEPT, True, False, "voice")] * 2


@pytest.mark.anyio
async def test_a_fusion_bookkeeping_bug_does_not_stop_verdicts():
    gate = _voice_locked_gate()

    def broken():
        raise RuntimeError("bookkeeping bug")

    gate.lock.drain_events = broken
    diarizer = _FakeDiarizer([(0.9, 0.0, 0.0, 0.0)] * 2)
    processor = SpeakerAudioProcessor(diarizer=diarizer, gate=gate,
                                      executor=ThreadPoolExecutor(max_workers=1))
    collector = await _run(processor, [_loud(), _loud()],
                           until=lambda: diarizer.pushed == 2)
    assert collector.verdicts == [Verdict.ACCEPT] * 2
    assert processor._fusion_row_errors == 2


@pytest.mark.anyio
async def test_segments_reach_the_embedder_and_enroll_the_voiceprint():
    class Embedder:
        def __init__(self):
            self.calls = []

        def embed(self, pcm):
            self.calls.append(len(pcm))
            return np.ones(4, np.float32)

    gate = _fusion_gate()
    embedder = Embedder()
    processor, _ = _fusion_processor(gate, embedder=embedder)
    for i in range(13):
        gate.row_voices, gate.row_asd_track = [0], 7
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    for _ in range(200):
        if gate.lock.voiceprint.seconds:
            break
        await asyncio.sleep(0.01)
    await processor._embeddings.close()
    assert embedder.calls == [13 * _ROW]
    assert gate.lock.voiceprint.seconds == pytest.approx(13 * 0.08)
    assert gate.lock.slots[0].segment_end == pytest.approx(101.96)


@pytest.mark.anyio
async def test_the_asd_wait_holds_for_the_locked_customer_not_a_nearer_face():
    # Vision is pinned to the customer (track 7, 1.2 m) and pushes ASD for
    # them only. Waiting on the nearer bystander (9) timed out every chunk and
    # threw the customer's fresh ASD away; the row fell back to MAR.
    from unittest.mock import AsyncMock

    from openjarvis.server.voice.speaker_vision import VisionAudioBridge

    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    for k in range(-2, 25):
        faces.add({"event": "faces", "ts": now + k * 0.1, "tracks": [
            {"track_id": 7, "distance_m": 1.2, "mouth_activity": 0.6},
            {"track_id": 9, "distance_m": 0.8, "mouth_activity": 0.1}]})
    faces.add_asd({"stream_id": "s", "track_id": 7, "t0": now - 0.5,
                   "frame_secs": 0.04, "probabilities": [0.95] * 25})
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    bridge._ready, bridge._stream_id = True, "s"
    bridge._start, bridge._samples = now - 2.0, 32000
    bridge.start, bridge.close = AsyncMock(), AsyncMock()
    bridge.offer = lambda audio, rate, channels: AudioSpan(now - 0.02, now, "s")
    gate = _fusion_gate(faces=faces, now=now)
    diarizer = _FakeDiarizer([(0.9, 0.0, 0.0, 0.0)])
    processor = SpeakerAudioProcessor(diarizer=diarizer, gate=gate,
                                      executor=ThreadPoolExecutor(max_workers=1),
                                      vision_audio=bridge)
    collector = _FrameCollector()
    await _run(processor, [_loud()], collector=collector,
               until=lambda: bool(collector.frames))
    assert bridge.last_wait_timed_out is False and bridge.asd_late == 0
    assert [(f.verdict, f.source) for f in collector.frames] == [
        (Verdict.ACCEPT, "asd")
    ]


def test_a_new_epoch_drops_the_previous_customers_tse_enrollment():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(
        gate, separator=_FakeSeparator(),
        separator_executor=ThreadPoolExecutor(max_workers=1),
    )
    processor._add_enrollment(np.ones(FRAME * 2, np.int16) * 300)
    assert processor._enrolled == FRAME * 2
    gate.lock.observe_fsm("cleanup", 101.1)  # the customer left: relock next
    processor._after_fusion_row(_row_pcm(), 101.2, False)
    assert processor._enrolled == 0 and len(processor._enroll) == 0


@pytest.mark.anyio
async def test_the_fusion_session_summary_counts_bookkeeping_errors():
    import openjarvis.server.voice.speaker_audio as speaker_audio

    processor, _ = _fusion_processor(_fusion_gate())
    processor._fusion_row_errors = 3
    messages = []
    sink = speaker_audio.logger.add(
        lambda message: messages.append(str(message)), level="INFO"
    )
    try:
        await processor._stop()
    finally:
        speaker_audio.logger.remove(sink)
    (line,) = [m for m in messages if "speaker_fusion session" in m]
    assert "fusion_row_errors=3" in line
