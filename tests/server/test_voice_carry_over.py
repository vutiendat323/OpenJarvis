"""An interrupted request is carried into the next turn; a finished one is not."""

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
from openjarvis.server.voice.llm import (
    CARRY_OVER_LIMIT,
    OpenJarvisLLMService,
    carried_prompt,
    remember_unfinished,
)

STEP, TURN = 2.0, 10.0


def test_nothing_unfinished_leaves_the_prompt_alone():
    assert carried_prompt([], "cho xem menu") == "cho xem menu"


def test_carried_prompt_labels_earlier_and_latest():
    prompt = carried_prompt(["thêm hai cà phê sữa"], "cho xem menu")
    assert prompt.index("<earlier_unfinished_request>") < prompt.index(
        "thêm hai cà phê sữa"
    )
    assert prompt.index("<latest_request>") < prompt.index("cho xem menu")
    assert "background_actions" in prompt


def test_carried_prompt_skips_text_already_merged():
    assert carried_prompt(["thêm hai"], "thêm hai cà phê sữa") == "thêm hai cà phê sữa"


def test_remember_keeps_the_three_newest_in_order():
    kept: list[str] = []
    for text in ["a", "b", "c", "d"]:
        kept = remember_unfinished(kept, text)
    assert kept == ["b", "c", "d"] and CARRY_OVER_LIMIT == 3


def test_remember_drops_entries_the_latest_repeats_or_extends():
    same = remember_unfinished(["thêm hai cà phê sữa"], "thêm hai cà phê sữa")
    assert same == ["thêm hai cà phê sữa"]
    extended = remember_unfinished(["thêm hai"], "thêm hai cà phê sữa")
    assert extended == ["thêm hai cà phê sữa"]


class _ScriptBinding:
    """Turn 1 blocks until cancelled; later turns record their prompt and finish."""

    model = "test-model"

    def __init__(self, block_turns=(0,)):
        self.prompts: list[str] = []
        self.blocking = asyncio.Event()
        self.done = asyncio.Event()
        self._block_turns = set(block_turns)

    async def run_stream(self, prompt, context):
        del context
        index = len(self.prompts)
        self.prompts.append(prompt)
        if index in self._block_turns:
            self.blocking.set()
            await asyncio.Event().wait()
        yield AgentTextDelta("Dạ.")
        yield AgentRunCompleted(AgentResult(content="Dạ.", turns=1))
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
async def test_interrupted_request_is_carried_and_a_finished_one_is_not():
    binding = _ScriptBinding()
    service = OpenJarvisLLMService(binding)

    async def steps(worker):
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(binding.blocking.wait(), STEP)
        await worker.queue_frame(InterruptionFrame())
        await worker.queue_frame(_ctx(U1, A1, U2))
        await asyncio.wait_for(binding.done.wait(), STEP)
        binding.done.clear()
        await worker.queue_frame(_ctx(U1, A1, U2, A2, U3))
        await asyncio.wait_for(binding.done.wait(), STEP)

    await _drive(service, steps)

    assert binding.prompts[0] == "thêm hai cà phê sữa"
    assert "<earlier_unfinished_request>" in binding.prompts[1]
    assert "thêm hai cà phê sữa" in binding.prompts[1]
    assert "cho xem menu" in binding.prompts[1]
    assert binding.prompts[2] == "xem giỏ"


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
async def test_a_repeated_interrupted_request_is_carried_once():
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

    assert "<earlier_unfinished_request>" in binding.prompts[2]
    assert binding.prompts[2].count("thêm hai cà phê sữa") == 1


async def _eventually(read, timeout=STEP):
    """Poll ``read`` until it returns something truthy, or return its last value."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not (value := read()) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    return value


@pytest.mark.anyio
async def test_a_carried_request_still_sees_its_write_after_the_reporter_told_it():
    """Spec §5.5: actions the earlier request started are in background_actions."""
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
        seen["carried"] = await _eventually(
            lambda: ACTION_LEDGER.background_for(conv, "r3")
        )
        await worker.queue_frame(_ctx(U1, A1, U3))
        await asyncio.wait_for(binding.done.wait(), STEP)
        await _eventually(lambda: not ACTION_LEDGER.background_for(conv, "r4"))
        seen["settled"] = ACTION_LEDGER.background_for(conv, "r4")

    try:
        with conversation_scope(conv):
            await _drive(service, steps)
    finally:
        ACTION_LEDGER.clear(conv)

    assert spoken == [told]
    assert seen["carried"] == [
        {
            "tool": "skill_add",
            "status": "succeeded",
            "message": told,
            "already_told": "true",
        }
    ]
    assert "<earlier_unfinished_request>" in binding.prompts[1]
    # A customer turn that completed settles everything already told.
    assert seen["settled"] == []
