"""Voice-only projection of native Agent stream events."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents._stubs import (
    AgentContext,
    AgentResult,
    AgentRunCompleted,
    AgentTextDelta,
    AgentToolFinished,
    AgentToolStarted,
)
from openjarvis.server.voice.audio import RealtimeTtsChunk
from openjarvis.server.voice.llm import OpenJarvisLLMService, agent_input
from openjarvis.server.voice.tts import VieNeuTTSService


class _Binding:
    model = "test-model"

    def __init__(self, events, *, first_event_after: float = 0.0):
        self._events = events
        self._first_event_after = first_event_after
        self.closed = False

    async def run_stream(self, prompt, context):
        del prompt, context
        try:
            if self._first_event_after:
                await asyncio.sleep(self._first_event_after)
            for event in self._events:
                yield event
        finally:
            self.closed = True


class _BlockingBinding:
    model = "test-model"

    def __init__(self):
        self.waiting = asyncio.Event()
        self.closed = False

    async def run_stream(self, prompt, context):
        del context
        if prompt != "turn cũ":
            yield AgentTextDelta("fresh response.")
            return
        try:
            yield AgentTextDelta("stale partial ")
            self.waiting.set()
            await asyncio.Event().wait()
        finally:
            self.closed = True


class _ErrorThenSuccessBinding:
    model = "test-model"

    def __init__(self):
        self.failed = asyncio.Event()

    async def run_stream(self, prompt, context):
        del context
        if prompt == "turn lỗi":
            yield AgentTextDelta("stale partial ")
            self.failed.set()
            raise RuntimeError("expected test failure")
        yield AgentTextDelta("fresh response.")


class _RecordingRenderer:
    def __init__(self):
        self.requests = []
        self.called = asyncio.Event()

    async def stream_tts(self, text):
        self.requests.append(text)
        self.called.set()
        yield RealtimeTtsChunk(
            audio=b"\x00\x00" * 480,
            provider="local",
            encoding="pcm_s16le",
            sample_rate_hz=48_000,
            channels=1,
        )


class _PipecatContext:
    def __init__(self, messages):
        self._messages = messages

    def get_messages(self):
        return self._messages


async def _speech(events, *, first_event_after: float = 0.0) -> str:
    service = OpenJarvisLLMService(
        _Binding(events, first_event_after=first_event_after)
    )
    frames = [
        frame
        async for frame in service.stream_agent("đặt món", AgentContext())
        if isinstance(frame, LLMTextFrame)
    ]
    return "".join(frame.text for frame in frames)


def test_agent_input_preserves_consecutive_user_turns():
    prompt, context = agent_input(
        _PipecatContext(
            [
                {"role": "user", "content": "xin chào"},
                {"role": "assistant", "content": "xin chào bạn"},
                {"role": "user", "content": "quay lại menu"},
                {"role": "user", "content": "bạn có nghe không"},
                {"role": "user", "content": "tìm món liên quan tới trứng"},
            ]
        )
    )

    assert prompt == "tìm món liên quan tới trứng"
    assert [
        (message.role.value, message.text)
        for message in context.conversation.messages[1:]
    ] == [
        ("user", "xin chào"),
        ("assistant", "xin chào bạn"),
        ("user", "quay lại menu"),
        ("user", "bạn có nghe không"),
    ]


@pytest.mark.anyio
async def test_interruption_does_not_flush_cancelled_response_into_tts():
    binding = _BlockingBinding()
    renderer = _RecordingRenderer()
    llm = OpenJarvisLLMService(binding)
    pipeline = Pipeline([llm, VieNeuTTSService(renderer, sample_rate=48_000)])
    worker = PipelineWorker(
        pipeline,
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive_turn():
        await asyncio.sleep(0.01)
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "turn cũ"}])
            )
        )
        await asyncio.wait_for(binding.waiting.wait(), timeout=0.5)
        await worker.queue_frame(InterruptionFrame())
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "turn mới"}])
            )
        )
        await asyncio.wait_for(renderer.called.wait(), timeout=0.5)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(
        asyncio.gather(runner.run(), drive_turn()),
        timeout=2.0,
    )

    assert binding.closed
    assert renderer.requests == ["fresh response."]


@pytest.mark.anyio
async def test_completed_response_still_flushes_into_tts():
    renderer = _RecordingRenderer()
    llm = OpenJarvisLLMService(_Binding([AgentTextDelta("fresh response.")]))
    pipeline = Pipeline([llm, VieNeuTTSService(renderer, sample_rate=48_000)])
    worker = PipelineWorker(
        pipeline,
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive_turn():
        await asyncio.sleep(0.01)
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "turn mới"}])
            )
        )
        await asyncio.wait_for(renderer.called.wait(), timeout=0.5)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(
        asyncio.gather(runner.run(), drive_turn()),
        timeout=2.0,
    )

    assert renderer.requests == ["fresh response."]


@pytest.mark.anyio
async def test_failed_response_discards_partial_text_before_the_next_turn():
    binding = _ErrorThenSuccessBinding()
    renderer = _RecordingRenderer()
    llm = OpenJarvisLLMService(binding)
    pipeline = Pipeline([llm, VieNeuTTSService(renderer, sample_rate=48_000)])
    worker = PipelineWorker(
        pipeline,
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive_turns():
        await asyncio.sleep(0.01)
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "turn lỗi"}])
            )
        )
        await asyncio.wait_for(binding.failed.wait(), timeout=0.5)
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "turn mới"}])
            )
        )
        await asyncio.wait_for(renderer.called.wait(), timeout=0.5)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(
        asyncio.gather(runner.run(), drive_turns()),
        timeout=2.0,
    )

    assert renderer.requests == ["fresh response."]


@pytest.mark.anyio
async def test_voice_speaks_the_first_tool_round_and_final_answer_only():
    speech = await _speech(
        [
            AgentTextDelta("Dạ, em đang tạo đơn. "),
            AgentToolStarted("http_request"),
            AgentToolFinished("http_request", ok=True),
            AgentTextDelta("Đơn đã tạo xong. "),
            AgentToolStarted("http_request"),
            AgentToolFinished("http_request", ok=True),
            AgentTextDelta("Đơn đã được xác minh. "),
            AgentToolStarted("display_bill"),
            AgentToolFinished("display_bill", ok=True),
            AgentTextDelta("Đơn của bạn đã sẵn sàng."),
            AgentRunCompleted(
                AgentResult(content="Đơn của bạn đã sẵn sàng.", turns=4)
            ),
        ]
    )

    assert speech == "Dạ, em đang tạo đơn. Đơn của bạn đã sẵn sàng."


@pytest.mark.anyio
async def test_voice_keeps_streaming_a_turn_that_does_not_use_tools():
    speech = await _speech(
        [
            AgentTextDelta("Xin chào "),
            AgentTextDelta("bạn."),
            AgentRunCompleted(AgentResult(content="Xin chào bạn.", turns=1)),
        ]
    )

    assert speech == "Xin chào bạn."


@pytest.mark.anyio
async def test_voice_does_not_repeat_a_terminal_display_round():
    speech = await _speech(
        [
            AgentTextDelta("Menu đang ở trên màn hình."),
            AgentToolStarted("display_menu"),
            AgentToolFinished("display_menu", ok=True),
            AgentRunCompleted(
                AgentResult(content="Menu đang ở trên màn hình.", turns=1)
            ),
        ]
    )

    assert speech.strip() == "Menu đang ở trên màn hình."


@pytest.mark.anyio
async def test_voice_does_not_inject_a_hardcoded_reply_into_a_slow_turn():
    speech = await _speech(
        [
            AgentToolStarted("http_request"),
            AgentToolFinished("http_request", ok=True),
            AgentTextDelta("Dạ, em nghe bạn nói rõ ạ."),
            AgentRunCompleted(
                AgentResult(content="Dạ, em nghe bạn nói rõ ạ.", turns=2)
            ),
        ],
        first_event_after=1.6,
    )

    assert speech == "Dạ, em nghe bạn nói rõ ạ."


@pytest.mark.anyio
async def test_voice_stays_quiet_when_the_turn_speaks_straight_away():
    speech = await _speech(
        [
            AgentTextDelta("Xin chào bạn."),
            AgentRunCompleted(AgentResult(content="Xin chào bạn.", turns=1)),
        ]
    )

    assert speech == "Xin chào bạn."


@pytest.mark.anyio
async def test_voice_barge_in_mid_round_releases_the_agent_stream():
    """Interruption cancels the consumer; the parked round must not be left running."""
    binding = _Binding([AgentTextDelta("quá muộn")], first_event_after=30.0)
    service = OpenJarvisLLMService(binding)
    stream = service.stream_agent("dừng lại", AgentContext())

    async def consume():
        async for _ in stream:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    await asyncio.sleep(0)
    assert binding.closed
