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

from openjarvis.server.voice.speaker import (
    AudioOnlyGate,
    FaceTrackBuffer,
    SpeakerSettings,
    Verdict,
)
from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor
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


def test_enrollment_keeps_the_latest_clean_speech():
    processor, _, _ = _separating_processor()
    for level in (1, 2, 3):
        processor._add_enrollment(np.ones(FRAME, np.int16) * level * 1000)

    enroll = np.concatenate(processor._enroll)
    assert len(enroll) == FRAME * 2 and enroll[0] == pytest.approx(2000 / 32768)
