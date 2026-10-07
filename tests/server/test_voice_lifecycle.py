"""Deterministic voice-session lifecycle: greeting, idle nudge, time limit and
absence goodbye -- every message goes straight from the event to TTS.

Durations are shrunk for the test; the defaults are the product values
(15 s idle, warning at 9 min, end at 10 min, 10 s absence).
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndWorkerFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from openjarvis.server.voice import lifecycle as lc
from openjarvis.server.voice.lifecycle import VoiceSessionLifecycle

IDLE, WARN, LIMIT, ABSENCE, POLL = 0.15, 0.6, 0.9, 0.2, 0.02


class _Presence:
    def __init__(self):
        self.present: bool | None = True

    def __call__(self):
        return self.present


async def _noop(self, frame, direction):
    pass


@pytest.fixture
async def harness(monkeypatch):
    monkeypatch.setattr(FrameProcessor, "process_frame", _noop)
    presence = _Presence()
    proc = VoiceSessionLifecycle(
        presence=presence,
        idle_secs=IDLE,
        warning_after_secs=WARN,
        limit_secs=LIMIT,
        absence_secs=ABSENCE,
        poll_secs=POLL,
    )
    pushed: list[tuple[object, FrameDirection]] = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    monkeypatch.setattr(proc, "push_frame", push)
    yield proc, pushed, presence
    proc.stop_timers()


def _spoken(pushed):
    return [f.text for f, _ in pushed if isinstance(f, TTSSpeakFrame)]


def _ended(pushed):
    return [f for f, d in pushed if isinstance(f, EndWorkerFrame)]


async def _say(proc, kind="bot"):
    """One finished utterance: the bot's TTS, or accepted customer speech."""
    if kind == "bot":
        await proc.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    else:
        await proc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)


@pytest.mark.anyio
async def test_accept_greets_with_hard_tts(harness):
    proc, pushed, _ = harness

    await proc.start_session()

    assert (
        _spoken(pushed)
        == [lc.GREETING]
        == [
            "Chào bạn, mình sẵn sàng hỗ trợ gọi món. "
            "Bạn muốn xem menu hay chọn món yêu thích trước ạ?"
        ]
    )
    assert 15 <= len(lc.GREETING.split()) <= 20
    assert pushed[0][1] is FrameDirection.DOWNSTREAM


@pytest.mark.anyio
async def test_idle_after_last_output_nudges_once(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await _say(proc, "bot")  # the greeting finished playing

    await asyncio.sleep(IDLE + 0.1)
    assert _spoken(pushed)[-1] == lc.IDLE_PROMPT == "Bạn muốn đặt món hay xem menu ạ?"

    await _say(proc, "bot")  # the nudge itself finished playing
    await asyncio.sleep(IDLE + 0.1)
    assert _spoken(pushed).count(lc.IDLE_PROMPT) == 1


@pytest.mark.anyio
async def test_accepted_customer_speech_resets_the_idle_timer(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await _say(proc, "bot")

    await asyncio.sleep(IDLE * 0.6)
    await proc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(IDLE * 0.6)  # past the original deadline, still talking
    assert lc.IDLE_PROMPT not in _spoken(pushed)

    await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(IDLE * 0.6)
    assert lc.IDLE_PROMPT not in _spoken(pushed)  # restarted, not resumed
    await asyncio.sleep(IDLE * 0.6)
    assert lc.IDLE_PROMPT in _spoken(pushed)


@pytest.mark.anyio
async def test_a_completed_assistant_turn_rearms_the_nudge(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await _say(proc, "bot")
    await asyncio.sleep(IDLE + 0.1)
    await _say(proc, "bot")  # nudge played
    assert _spoken(pushed).count(lc.IDLE_PROMPT) == 1

    await _say(proc, "user")
    await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    await _say(proc, "bot")  # the agent's reply finished
    await asyncio.sleep(IDLE + 0.1)

    assert _spoken(pushed).count(lc.IDLE_PROMPT) == 2


@pytest.mark.anyio
async def test_warning_waits_for_active_speech_and_never_interrupts(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await proc.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)

    await asyncio.sleep(WARN + 0.05)
    assert lc.WARNING not in _spoken(pushed)  # bot still talking: queued

    await proc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert lc.WARNING not in _spoken(pushed)  # customer talking: still queued

    await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert _spoken(pushed).count(lc.WARNING) == 1


@pytest.mark.anyio
async def test_interrupted_response_does_not_hold_warning_forever(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await proc._warn()
    assert lc.WARNING not in _spoken(pushed)
    await proc.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert _spoken(pushed).count(lc.WARNING) == 1


@pytest.mark.anyio
async def test_limit_timer_can_yield_while_sending_goodbye(harness, monkeypatch):
    proc, pushed, _ = harness

    async def yielding_push(frame, direction=FrameDirection.DOWNSTREAM):
        await asyncio.sleep(0)
        pushed.append((frame, direction))

    monkeypatch.setattr(proc, "push_frame", yielding_push)
    timer = asyncio.create_task(proc._after(0, proc._end))
    proc._timers = [timer]
    results = await asyncio.gather(timer, return_exceptions=True)
    assert results == [None]
    assert _spoken(pushed) == [lc.GOODBYE]
    assert len(_ended(pushed)) == 1


@pytest.mark.anyio
async def test_empty_agent_response_rearms_idle_nudge(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await proc.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    await asyncio.sleep(IDLE + 0.05)
    assert _spoken(pushed).count(lc.IDLE_PROMPT) == 1


@pytest.mark.anyio
async def test_fsm_warning_and_voice_timer_speak_once(harness):
    proc, pushed, _ = harness
    await proc.start_session()
    await proc.request_warning()
    await asyncio.sleep(WARN + 0.05)
    assert _spoken(pushed).count(lc.WARNING) == 1


@pytest.mark.anyio
async def test_time_limit_says_goodbye_and_ends_the_session(harness):
    proc, pushed, _ = harness
    await proc.start_session()

    await asyncio.sleep(LIMIT + 0.1)

    assert _spoken(pushed)[-1] == lc.GOODBYE
    [end] = _ended(pushed)
    assert [d for f, d in pushed if f is end] == [FrameDirection.UPSTREAM]
    goodbye_at = next(
        i
        for i, (f, _) in enumerate(pushed)
        if isinstance(f, TTSSpeakFrame) and f.text == lc.GOODBYE
    )
    assert goodbye_at < next(i for i, (f, _) in enumerate(pushed) if f is end)


@pytest.mark.anyio
async def test_continuous_absence_says_goodbye_and_ends(harness):
    proc, pushed, presence = harness
    await proc.start_session()

    presence.present = False
    await asyncio.sleep(ABSENCE + 0.1)

    assert _spoken(pushed)[-1] == lc.GOODBYE
    assert len(_ended(pushed)) == 1


@pytest.mark.anyio
async def test_brief_absence_does_not_end_the_session(harness):
    proc, pushed, presence = harness
    await proc.start_session()

    for _ in range(3):
        presence.present = False
        await asyncio.sleep(ABSENCE * 0.6)
        presence.present = True
        await asyncio.sleep(POLL * 3)

    assert lc.GOODBYE not in _spoken(pushed) and _ended(pushed) == []


@pytest.mark.anyio
async def test_unknown_presence_is_not_absence(harness):
    proc, pushed, presence = harness
    await proc.start_session()

    presence.present = None
    await asyncio.sleep(ABSENCE + 0.1)

    assert _ended(pushed) == []


@pytest.mark.anyio
async def test_absence_and_timeout_together_say_goodbye_once(harness):
    proc, pushed, presence = harness
    await proc.start_session()
    await asyncio.sleep(LIMIT - ABSENCE)
    presence.present = False  # absence and the limit land together

    await asyncio.sleep(ABSENCE + 0.3)

    assert _spoken(pushed).count(lc.GOODBYE) == 1
    assert len(_ended(pushed)) == 1
    await _say(proc, "bot")
    await asyncio.sleep(IDLE + 0.1)
    assert lc.IDLE_PROMPT not in _spoken(pushed)  # ended: no nudge after goodbye


@pytest.mark.anyio
async def test_lifecycle_never_asks_the_llm(harness):
    proc, pushed, presence = harness
    await proc.start_session()
    await _say(proc, "bot")
    await asyncio.sleep(IDLE + 0.1)
    presence.present = False
    await asyncio.sleep(ABSENCE + 0.1)

    fed = (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)  # passed through
    made = {type(f) for f, _ in pushed if not isinstance(f, fed)}
    assert made <= {TTSSpeakFrame, EndWorkerFrame}


@pytest.mark.anyio
async def test_other_frames_pass_through_unchanged(harness):
    proc, pushed, _ = harness
    frame = LLMFullResponseStartFrame()

    await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert pushed == [(frame, FrameDirection.DOWNSTREAM)]


def test_pipeline_puts_the_lifecycle_between_the_agent_and_tts(monkeypatch):
    from unittest.mock import MagicMock

    from openjarvis.kiosk.config import KioskConfig
    from openjarvis.server.voice.llm import OpenJarvisLLMService
    from openjarvis.server.voice.pipeline import build_voice_pipeline
    from openjarvis.server.voice.tts import VieNeuTTSService

    monkeypatch.setattr(
        "openjarvis.kiosk.evaluate.get_config",
        lambda: KioskConfig(
            session_max_seconds=180,
            session_warning_seconds=120,
            leave_sustain_seconds_active=4,
        ),
    )

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(), binding=MagicMock(), renderer=MagicMock(), stt=stt
    )
    chain, node = [], stt
    while node is not None:
        chain.append(node)
        node = getattr(node, "_next", None)
    kinds = [type(n) for n in chain]
    i = kinds.index(VoiceSessionLifecycle)
    assert kinds[i - 1] is OpenJarvisLLMService and kinds[i + 1] is VieNeuTTSService
    assert chain[i]._limit == 180
    assert chain[i]._warning_after == 120
    assert chain[i]._absence_secs == 4


def test_kiosk_runtime_reports_customer_presence_from_vision():
    from openjarvis.kiosk import runtime

    runtime._set_presence_from_event("no_person")
    assert runtime.customer_present() is False
    runtime._set_presence_from_event("person_near")
    assert runtime.customer_present() is True
    runtime._set_presence_from_event("person_unknown")
    assert runtime.customer_present() is True
