"""An interrupted request keeps running; nothing is carried into the next turn."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import EndFrame, InterruptionFrame, LLMContextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents._stubs import AgentResult, AgentRunCompleted, AgentTextDelta
from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import ToolResult
from openjarvis.server.voice.background import BackgroundReporter
from openjarvis.server.voice.llm import OpenJarvisLLMService, inflight_prompt

STEP, TURN = 2.0, 10.0


class _ScriptBinding:
    """Blocking turns wait for ``release``; the others record and finish."""

    model = "test-model"

    def __init__(self, block_turns=(0,)):
        self.prompts: list[str] = []
        self.blocking = asyncio.Event()
        self.release = asyncio.Event()
        self.done = asyncio.Event()
        self.finished: list[int] = []
        self.cancelled: list[int] = []
        self._block_turns = set(block_turns)

    async def run_stream(self, prompt, context):
        del context
        index = len(self.prompts)
        self.prompts.append(prompt)
        try:
            if index in self._block_turns:
                self.blocking.set()
                await self.release.wait()
            yield AgentTextDelta("Dạ.")
            yield AgentRunCompleted(AgentResult(content="Dạ.", turns=1))
        except asyncio.CancelledError:
            self.cancelled.append(index)
            raise
        self.finished.append(index)
        self.done.set()


def _ctx(*messages):
    return LLMContextFrame(LLMContext(messages=[dict(m) for m in messages]))


async def _drive(service, steps):
    worker = PipelineWorker(
        Pipeline([service]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.01)
        await steps(worker)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=TURN)


U1 = {"role": "user", "content": "thêm hai cà phê sữa"}
A1 = {"role": "assistant", "content": "Đang thêm."}
U2 = {"role": "user", "content": "cho xem menu"}
A2 = {"role": "assistant", "content": "Dạ."}
U3 = {"role": "user", "content": "xem giỏ"}


@pytest.mark.anyio
async def test_an_interrupted_request_keeps_running_and_is_not_carried():
    binding = _ScriptBinding()
    service = OpenJarvisLLMService(binding)

    async def steps(worker):
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(binding.blocking.wait(), STEP)
        await worker.queue_frame(InterruptionFrame())
        await worker.queue_frame(_ctx(U1, A1, U2))
        await asyncio.wait_for(binding.done.wait(), STEP)
        binding.done.clear()
        # The interrupted request was never cancelled: it finishes now.
        binding.release.set()
        await asyncio.wait_for(binding.done.wait(), STEP)
        binding.done.clear()
        await worker.queue_frame(_ctx(U1, A1, U2, A2, U3))
        await asyncio.wait_for(binding.done.wait(), STEP)

    await _drive(service, steps)

    assert binding.prompts == ["thêm hai cà phê sữa", "cho xem menu", "xem giỏ"]
    assert binding.finished == [1, 0, 2]
    assert binding.cancelled == []


@pytest.mark.anyio
async def test_a_rejected_speakers_request_is_never_carried():
    from openjarvis.server.voice.speaker import SpeakerSettings, SpeakerTracker, Verdict

    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    binding = _ScriptBinding()
    service = OpenJarvisLLMService(
        binding, speaker_tracker=tracker, uncertain_allowed_tools=("display_menu",)
    )

    def verdict(value):
        tracker.begin_span()
        for _ in range(10):
            tracker.record(value)

    async def steps(worker):
        verdict(Verdict.REJECT)
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(binding.blocking.wait(), STEP)
        await worker.queue_frame(InterruptionFrame())
        verdict(Verdict.ACCEPT)
        await worker.queue_frame(_ctx(U1, A1, U2))
        await asyncio.wait_for(binding.done.wait(), STEP)

    await _drive(service, steps)

    assert binding.prompts[1] == "cho xem menu"


@pytest.mark.anyio
async def test_a_repeated_interrupted_request_runs_again_and_nothing_is_carried():
    binding = _ScriptBinding(block_turns=(0, 1))
    service = OpenJarvisLLMService(binding)

    async def steps(worker):
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(binding.blocking.wait(), STEP)
        binding.blocking.clear()
        await worker.queue_frame(InterruptionFrame())
        await worker.queue_frame(_ctx(U1, A1, U1))
        await asyncio.wait_for(binding.blocking.wait(), STEP)
        await worker.queue_frame(InterruptionFrame())
        await worker.queue_frame(_ctx(U1, A1, U1, A1, U3))
        await asyncio.wait_for(binding.done.wait(), STEP)

    await _drive(service, steps)

    assert binding.prompts == ["thêm hai cà phê sữa", "thêm hai cà phê sữa", "xem giỏ"]


async def _eventually(read, timeout=STEP):
    """Poll ``read`` until it returns something truthy, or return its last value."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not (value := read()) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    return value


@pytest.mark.anyio
async def test_a_told_write_is_not_listed_again_while_its_run_keeps_working():
    """Nothing is carried: a write the reporter told is not re-listed (spec §7.2)."""
    conv = "voice-carry-told"
    told = "Đã thêm hai cà phê sữa."
    binding = _ScriptBinding()
    service = OpenJarvisLLMService(binding)
    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)

    async def flush():
        pass

    reporter = BackgroundReporter(
        ACTION_LEDGER,
        conv,
        speak=speak,
        flush=flush,
        run_event_turn=service.run_event_turn,
    )
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(binding.blocking.wait(), STEP)
        # The turn had dispatched its add when a cough cut it; no new turn yet.
        ACTION_LEDGER.start(conv, call_id="c1", tool="skill_add", run_id="r1")
        await worker.queue_frame(InterruptionFrame())
        ACTION_LEDGER.finish(
            conv,
            call_id="c1",
            run_id="r1",
            result=ToolResult(
                tool_name="skill_add",
                content="ok",
                success=True,
                metadata={"customer_message": told},
            ),
        )
        await reporter.on_idle()
        await _eventually(lambda: spoken)
        seen["listed"] = ACTION_LEDGER.background_for(conv, "r3")
        await worker.queue_frame(_ctx(U1, A1, U3))
        await asyncio.wait_for(binding.done.wait(), STEP)
        seen["cancelled"] = list(binding.cancelled)

    try:
        with conversation_scope(conv):
            await _drive(service, steps)
    finally:
        ACTION_LEDGER.clear(conv)

    assert spoken == [told]
    assert seen["listed"] == []
    # The prompt names the interrupted request as still in progress (the run
    # was working), and carries nothing else: not the told write, no carried
    # block.
    assert binding.prompts[1] == inflight_prompt([U1["content"]], U3["content"])
    assert told not in binding.prompts[1]
    assert "<earlier_unfinished_request>" not in binding.prompts[1]
    # The interrupted request was still working when the next one ran.
    assert seen["cancelled"] == []
