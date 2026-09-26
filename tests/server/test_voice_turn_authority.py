"""The target-speaker gate decides turn starts, barge-in and dropped speech."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from openjarvis.server.voice.speaker import SpeakerSettings, SpeakerTracker, Verdict
from openjarvis.server.voice.turn_detection import (
    SpeakerVerdictFrame,
    TargetSpeakerTurnStartStrategy,
)


async def _strategy(frames: int = 3):
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    strategy = TargetSpeakerTurnStartStrategy(
        tracker=tracker, bargein_accept_frames=frames
    )
    await strategy.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=TaskManager(),
            pipeline_worker=MagicMock(),
        )
    )
    events: list[str] = []

    @strategy.event_handler("on_user_turn_started")
    async def _started(_strategy, params):
        events.append(f"start:{params.enable_interruptions}")
        await strategy.handle_user_turn_started()

    @strategy.event_handler("on_reset_aggregation")
    async def _reset(_strategy):
        events.append("reset")

    return strategy, tracker, events


@pytest.mark.anyio
async def test_speech_while_bot_is_silent_opens_a_clean_turn():
    strategy, _, events = await _strategy()

    await strategy.process_frame(VADUserStartedSpeakingFrame())

    assert events == ["reset", "start:True"]


@pytest.mark.anyio
async def test_without_evidence_barge_in_is_unchanged():
    strategy, _, events = await _strategy()

    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())

    assert events == ["reset", "start:True"]


@pytest.mark.anyio
async def test_uncertain_or_rejected_speech_never_interrupts_the_bot():
    strategy, tracker, events = await _strategy()
    tracker.record(Verdict.UNCERTAIN)  # the diarizer is live this session

    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    for verdict in (
        Verdict.UNCERTAIN,
        Verdict.REJECT,
        Verdict.UNCERTAIN,
        Verdict.REJECT,
    ):
        await strategy.process_frame(SpeakerVerdictFrame(verdict=verdict))

    assert events == []


@pytest.mark.anyio
async def test_sustained_accept_barges_in():
    strategy, tracker, events = await _strategy(frames=3)
    tracker.record(Verdict.UNCERTAIN)

    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.ACCEPT))
    await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.ACCEPT))
    assert events == []
    await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.ACCEPT))

    assert events == ["reset", "start:True"]


@pytest.mark.anyio
async def test_a_reject_breaks_the_accept_run():
    strategy, tracker, events = await _strategy(frames=2)
    tracker.record(Verdict.UNCERTAIN)

    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    for verdict in (Verdict.ACCEPT, Verdict.REJECT, Verdict.ACCEPT):
        await strategy.process_frame(SpeakerVerdictFrame(verdict=verdict))

    assert events == []


@pytest.mark.anyio
async def test_rejected_span_does_not_open_a_turn_after_the_bot_stops():
    strategy, tracker, events = await _strategy()
    tracker.record(Verdict.REJECT)

    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    for _ in range(5):
        await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.REJECT))
    await strategy.process_frame(BotStoppedSpeakingFrame())
    await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.REJECT))
    await strategy.process_frame(VADUserStoppedSpeakingFrame())

    assert events == []


@pytest.mark.anyio
async def test_closing_a_rejected_turn_drops_its_aggregation():
    strategy, tracker, events = await _strategy()

    await strategy.process_frame(VADUserStartedSpeakingFrame())
    for _ in range(9):
        await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.REJECT))
    verdict = await strategy.close_turn()

    assert verdict is Verdict.REJECT
    assert events == ["reset", "start:True", "reset"]
    assert tracker.take_turn_verdict() is Verdict.REJECT


@pytest.mark.anyio
async def test_stop_strategy_closes_the_turn_through_the_gate(monkeypatch):
    from openjarvis.server.voice.turn_detection import (
        ConfirmedTurnAnalyzerUserTurnStopStrategy,
    )

    gate, tracker, events = await _strategy()
    stop = ConfirmedTurnAnalyzerUserTurnStopStrategy(
        turn_analyzer=MagicMock(),
        minimum_silence_secs=0.0,
        speaker_gate=gate,
    )
    await gate.process_frame(VADUserStartedSpeakingFrame())
    for _ in range(9):
        await gate.process_frame(SpeakerVerdictFrame(verdict=Verdict.REJECT))

    await stop._finish_turn(None)

    assert events[-1] == "reset"
    assert tracker.take_turn_verdict() is Verdict.REJECT


def _built_aggregator(speaker):
    from pipecat.processors.frame_processor import FrameProcessor

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
        speaker=speaker,
    )
    aggregator = stt._next
    return aggregator, aggregator._next


def test_disabled_gate_keeps_todays_pipeline():
    from pipecat.turns.user_turn_strategies import default_user_turn_start_strategies

    aggregator, llm = _built_aggregator(SpeakerSettings())
    strategies = aggregator._params.user_turn_strategies

    assert [type(s) for s in strategies.start] == [
        type(s) for s in default_user_turn_start_strategies()
    ]
    assert strategies.stop[0]._speaker_gate is None
    assert llm._speaker_tracker is None


def test_enabled_gate_shares_one_tracker():
    aggregator, llm = _built_aggregator(
        SpeakerSettings(enabled=True, uncertain_allowed_tools=("display_menu",))
    )
    strategies = aggregator._params.user_turn_strategies
    gate = strategies.start[0]

    assert isinstance(gate, TargetSpeakerTurnStartStrategy)
    assert len(strategies.start) == 1
    assert strategies.stop[0]._speaker_gate is gate
    assert llm._speaker_tracker is gate._tracker
    assert llm._uncertain_allowed_tools == ("display_menu",)


@pytest.mark.anyio
async def test_opening_a_turn_clears_an_untaken_verdict():
    strategy, tracker, _ = await _strategy()
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    await strategy.process_frame(SpeakerVerdictFrame(verdict=Verdict.UNCERTAIN))
    await strategy.close_turn()
    await strategy.handle_user_turn_stopped()
    await strategy.process_frame(VADUserStoppedSpeakingFrame())

    await strategy.process_frame(VADUserStartedSpeakingFrame())

    assert tracker.take_turn_verdict() is Verdict.ACCEPT


def _built_processors(speaker, diarizer):
    from pipecat.processors.frame_processor import FrameProcessor

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
        speaker=speaker,
        diarizer=diarizer,
    )
    return stt._prev


def test_enabled_gate_with_a_diarizer_feeds_it_before_stt():
    from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor

    diarizer = MagicMock(chunk_samples=3840, frame_secs=0.08)
    before_stt = _built_processors(
        SpeakerSettings(enabled=True, diarizer="sortformer"), diarizer
    )

    assert isinstance(before_stt, SpeakerAudioProcessor)
    assert before_stt._diarizer is diarizer


def test_no_diarizer_keeps_milestone_one_wiring():
    from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor

    for speaker, diarizer in (
        (SpeakerSettings(enabled=True, diarizer="sortformer"), None),
        (SpeakerSettings(enabled=False, diarizer="sortformer"), MagicMock()),
    ):
        assert not isinstance(
            _built_processors(speaker, diarizer), SpeakerAudioProcessor
        )


@pytest.mark.anyio
async def test_diarizer_load_failure_leaves_voice_working(monkeypatch):
    from openjarvis.server.voice import routes

    monkeypatch.setattr(routes, "_DIARIZER", None)
    monkeypatch.setattr(routes, "_DIARIZER_FAILED", False)
    monkeypatch.setattr(
        routes,
        "load_speaker_settings",
        lambda: SpeakerSettings(enabled=True, diarizer="sortformer"),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("no CUDA")

    monkeypatch.setattr(routes, "SortformerDiarizer", boom)

    assert await routes._diarizer() is None
    assert await routes._diarizer() is None  # failure is remembered, not retried


@pytest.mark.anyio
async def test_separator_load_failure_leaves_voice_on_the_mix(monkeypatch):
    from openjarvis.server.voice import routes

    monkeypatch.setattr(routes, "_DIARIZER", object())
    monkeypatch.setattr(routes, "_SEPARATOR", None)
    monkeypatch.setattr(routes, "_SEPARATOR_FAILED", False)
    monkeypatch.setattr(
        routes,
        "load_speaker_settings",
        lambda: SpeakerSettings(
            enabled=True, diarizer="sortformer", stt_mask=True, separator="tse"
        ),
    )
    loads = []

    def boom(path):
        loads.append(path)
        raise FileNotFoundError(path)

    monkeypatch.setattr(routes, "TseSeparator", boom)

    assert await routes._separator() is None
    assert await routes._separator() is None
    assert len(loads) == 1  # failure is remembered, not retried


def _built_processors_with(speaker, diarizer, faces):
    from pipecat.processors.frame_processor import FrameProcessor

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
        speaker=speaker,
        diarizer=diarizer,
        faces=faces,
    )
    return stt._prev


def test_vision_faces_reach_the_gate_only_when_enabled():
    from openjarvis.server.voice.speaker import FaceTrackBuffer

    faces = FaceTrackBuffer()
    diarizer = MagicMock(chunk_samples=3840, frame_secs=0.08)
    on = _built_processors_with(
        SpeakerSettings(enabled=True, diarizer="sortformer", vision_faces=True),
        diarizer,
        faces,
    )
    off = _built_processors_with(
        SpeakerSettings(enabled=True, diarizer="sortformer"), diarizer, faces
    )

    assert on._gate._faces is faces
    assert off._gate._faces is None


@pytest.mark.anyio
async def test_a_diarized_session_never_barges_in_without_evidence():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True), diarized=True)
    strategy = TargetSpeakerTurnStartStrategy(tracker=tracker, bargein_accept_frames=3)
    await strategy.setup(
        FrameProcessorSetup(
            clock=SystemClock(), task_manager=TaskManager(), pipeline_worker=MagicMock()
        )
    )
    events = []

    @strategy.event_handler("on_user_turn_started")
    async def _started(_strategy, params):
        events.append("start")

    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())

    assert events == []


@pytest.mark.anyio
async def test_stopping_without_an_open_turn_does_not_close_one():
    from openjarvis.server.voice.turn_detection import (
        ConfirmedTurnAnalyzerUserTurnStopStrategy,
    )

    gate, tracker, events = await _strategy()
    stop = ConfirmedTurnAnalyzerUserTurnStopStrategy(
        turn_analyzer=MagicMock(), minimum_silence_secs=0.0, speaker_gate=gate
    )

    await stop._finish_turn(None)

    assert events == []
    assert gate.turn_open is False


def test_a_loaded_diarizer_marks_the_tracker_diarized():
    diarizer = MagicMock(chunk_samples=3840, frame_secs=0.08)
    processor = _built_processors(
        SpeakerSettings(enabled=True, diarizer="sortformer"), diarizer
    )

    assert processor._gate is not None
    aggregator = processor._next._next
    gate = aggregator._params.user_turn_strategies.start[0]
    assert gate._tracker.has_evidence is True


def test_stt_mask_wires_the_delayed_gemini_feed():
    from pipecat.processors.frame_processor import FrameProcessor

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    class _MaskableStt(FrameProcessor):
        delay = None
        drain = None

        def enable_masked_feed(self, delay_secs, drain=None):
            self.delay = delay_secs
            self.drain = drain

    diarizer = MagicMock(chunk_samples=3840, frame_secs=0.08)
    for mask, expected in ((True, 0.5), (False, None)):
        stt = _MaskableStt()
        build_voice_pipeline(
            connection=MagicMock(),
            binding=MagicMock(),
            renderer=MagicMock(),
            stt=stt,
            speaker=SpeakerSettings(enabled=True, diarizer="sortformer", stt_mask=mask),
            diarizer=diarizer,
        )
        assert stt.delay == expected
        assert stt._prev._stt_delay == (expected or 0.0)
        assert stt.drain is None

    # A separator holds overlap back, so finalization must drain it first.
    stt = _MaskableStt()
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
        speaker=SpeakerSettings(
            enabled=True, diarizer="sortformer", stt_mask=True, separator="tse"
        ),
        diarizer=diarizer,
        separator=MagicMock(),
    )
    assert stt.drain == stt._prev.drain
