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
