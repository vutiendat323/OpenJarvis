"""A barge-in fences a voice run's speech; the run keeps working (spec 2026-10-17)."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents._stubs import (
    CUSTOMER_REQUEST,
    RUN_SPEAKING,
    AgentResult,
    AgentRunCompleted,
    AgentTextDelta,
)
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.runs import RUN_TRACKER, current_voice_run
from openjarvis.server.voice.llm import (
    UNCERTAIN_SPEAKER_NOTE,
    OpenJarvisLLMService,
    inflight_prompt,
)
from openjarvis.server.voice.responder import ResponderReply

STEP = 2.0

U1 = {"role": "user", "content": "thêm hai cà phê sữa"}
A1 = {"role": "assistant", "content": "Dạ."}
U2 = {"role": "user", "content": "cho xem menu"}
A2 = {"role": "assistant", "content": "Dạ."}
U3 = {"role": "user", "content": "xem giỏ"}
ALO = {"role": "user", "content": "alo"}
HEARD = "Mình nghe rõ bạn."


def _ctx(*messages):
    return LLMContextFrame(LLMContext(messages=[dict(m) for m in messages]))


def _reply(prompt: str) -> str:
    return f"Xong {prompt}."


class _GatedBinding:
    """Each run waits for its own release; optional slots, like the runtime."""

    model = "test-model"

    def __init__(self, slots: int | None = None):
        self._slots = asyncio.Semaphore(slots) if slots else None
        self.prompts: list[str] = []
        self.keys: list[str] = []
        self.contexts: dict[str, object] = {}
        self.started: dict[str, asyncio.Event] = {}
        self.release: dict[str, asyncio.Event] = {}
        self.finished: list[str] = []
        self.cancelled: list[str] = []
        self.voice_runs: list[tuple[str, set[str]]] = []
        self.conversation = ""

    def _event(self, events, prompt):
        return events.setdefault(prompt, asyncio.Event())

    async def run_stream(self, prompt, context):
        self.prompts.append(prompt)
        # A run that is told what is in flight gets a longer prompt: it is
        # known by the customer's words it carries, as the tools see it.
        key = context.metadata.get(CUSTOMER_REQUEST) or prompt
        self.keys.append(key)
        self.contexts[key] = context
        try:
            async with self._slots or nullcontext():
                # A sync tool reaches its thread through to_thread, as the
                # ToolExecutor path does: it must see this run's id.
                self.voice_runs.append(
                    (
                        await asyncio.to_thread(current_voice_run),
                        RUN_TRACKER.in_flight_ids(self.conversation),
                    )
                )
                self._event(self.started, key).set()
                await self._event(self.release, key).wait()
                yield AgentTextDelta(_reply(key))
                yield AgentRunCompleted(AgentResult(content=_reply(key), turns=1))
                self.finished.append(key)
        except asyncio.CancelledError:
            self.cancelled.append(key)
            raise

    async def wait_started(self, message):
        started = self._event(self.started, message["content"])
        await asyncio.wait_for(started.wait(), STEP)

    def let_go(self, message):
        self._event(self.release, message["content"]).set()

    async def wait_finished(self, message):
        await _eventually(lambda: message["content"] in self.finished)


class _Responder:
    """Answers a hear check itself; delegates everything else without a filler."""

    model = "fast-model"

    def __init__(self, *, hold: bool = False, kind: str = "answer"):
        self.calls: list[str] = []
        self.in_progress: list[list[str]] = []
        self.deciding = asyncio.Event()
        self.decide = asyncio.Event()
        self.decided = asyncio.Event()
        self._hold = hold
        self._kind = kind
        if not hold:
            self.decide.set()

    async def respond(
        self, utterance, conversation="", on_delegate=None, in_progress=()
    ):
        del conversation
        self.calls.append(utterance)
        self.in_progress.append(list(in_progress))
        self.deciding.set()
        await self.decide.wait()
        self.decided.set()
        if utterance == ALO["content"] or self._kind == "answer":
            return ResponderReply("answer", HEARD)
        if on_delegate is not None:
            on_delegate("")
        return ResponderReply("delegate", "")


class _Sink(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.texts: list[str] = []
        self.ends = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.texts.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.ends += 1
        await self.push_frame(frame, direction)

    def spoke(self, text):
        return text in "".join(self.texts)


async def _eventually(read, timeout=STEP):
    deadline = asyncio.get_running_loop().time() + timeout
    while not (value := read()):
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)
    return value


async def _drive(service, steps, end=EndFrame):
    sink = _Sink()
    worker = PipelineWorker(
        Pipeline([service, sink]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.01)
        try:
            await steps(worker, sink)
        except BaseException:
            # A failed step cancels the session, so no blocked turn can hang it.
            await worker.queue_frame(CancelFrame())
            raise
        await worker.queue_frame(end())

    await runner.add_workers(worker)
    for result in await asyncio.wait_for(
        asyncio.gather(runner.run(), drive(), return_exceptions=True), timeout=10
    ):
        if isinstance(result, BaseException):
            raise result
    return sink


async def _interrupt(worker):
    await worker.queue_frame(InterruptionFrame())
    await asyncio.sleep(0.05)


@pytest.mark.anyio
async def test_a_barge_in_while_the_agent_thinks_does_not_cancel_the_run():
    binding = _GatedBinding()
    service = OpenJarvisLLMService(binding)
    seen = {}

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        seen["answering"] = service._answering
        await _interrupt(worker)
        seen["answering_after"] = service._answering
        binding.let_go(U1)
        await binding.wait_finished(U1)
        await asyncio.sleep(0.05)

    sink = await _drive(service, steps)

    assert binding.finished == [U1["content"]]
    assert binding.cancelled == []
    assert not sink.spoke(_reply(U1["content"]))
    assert sink.ends == 0
    assert seen == {"answering": True, "answering_after": False}


@pytest.mark.anyio
async def test_a_new_request_starts_its_run_while_the_first_still_works():
    conv = "voice-parallel-new-request"
    binding = _GatedBinding()
    binding.conversation = conv
    service = OpenJarvisLLMService(binding)
    seen = {}

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await worker.queue_frame(_ctx(U1, A1, U2))
        await binding.wait_started(U2)
        seen["first_finished"] = list(binding.finished)
        seen["in_flight"] = sorted(RUN_TRACKER.in_flight(conv))
        first = binding.contexts[U1["content"]].metadata
        second = binding.contexts[U2["content"]].metadata
        seen["requests"] = [first[CUSTOMER_REQUEST], second[CUSTOMER_REQUEST]]
        seen["speaking"] = [first[RUN_SPEAKING](), second[RUN_SPEAKING]()]
        binding.let_go(U2)
        await binding.wait_finished(U2)
        binding.let_go(U1)
        await binding.wait_finished(U1)
        await asyncio.sleep(0.05)

    with conversation_scope(conv):
        sink = await _drive(service, steps)

    assert seen["first_finished"] == []
    assert seen["in_flight"] == sorted([U1["content"], U2["content"]])
    assert seen["requests"] == [U1["content"], U2["content"]]
    assert seen["speaking"] == [False, True]
    assert sink.spoke(_reply(U2["content"]))
    assert not sink.spoke(_reply(U1["content"]))
    assert binding.cancelled == []
    assert RUN_TRACKER.in_flight(conv) == []


@pytest.mark.anyio
async def test_alo_is_answered_by_the_responder_while_the_run_continues():
    binding = _GatedBinding()
    responder = _Responder(kind="delegate")
    service = OpenJarvisLLMService(binding, responder=responder)

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await _interrupt(worker)
        await worker.queue_frame(_ctx(U1, A1, ALO))
        await _eventually(lambda: sink.spoke(HEARD))
        binding.let_go(U1)
        await binding.wait_finished(U1)
        await asyncio.sleep(0.05)

    sink = await _drive(service, steps)

    assert responder.calls == [U1["content"], ALO["content"]]
    assert binding.prompts == [U1["content"]]
    assert binding.finished == [U1["content"]] and binding.cancelled == []
    assert sink.spoke(HEARD)
    assert not sink.spoke(_reply(U1["content"]))


@pytest.mark.anyio
async def test_a_fenced_answer_route_speaks_nothing():
    binding = _GatedBinding()
    responder = _Responder(hold=True, kind="answer")
    service = OpenJarvisLLMService(binding, responder=responder)

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(responder.deciding.wait(), STEP)
        await _interrupt(worker)
        responder.decide.set()
        await asyncio.sleep(0.1)

    sink = await _drive(service, steps)

    assert sink.texts == [] and sink.ends == 0
    assert binding.prompts == []


@pytest.mark.anyio
async def test_a_fenced_agent_route_still_runs_the_agent_silently():
    binding = _GatedBinding()
    responder = _Responder(hold=True, kind="delegate")
    service = OpenJarvisLLMService(binding, responder=responder)

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await asyncio.wait_for(responder.deciding.wait(), STEP)
        await _interrupt(worker)
        responder.decide.set()
        await binding.wait_started(U1)
        binding.let_go(U1)
        await binding.wait_finished(U1)
        await asyncio.sleep(0.05)

    sink = await _drive(service, steps)

    assert binding.finished == [U1["content"]]
    assert sink.texts == [] and sink.ends == 0


@pytest.mark.anyio
@pytest.mark.parametrize("end", [EndFrame, CancelFrame])
async def test_session_end_cancels_every_run_and_closes_the_tracker(end):
    conv = f"voice-parallel-end-{end.__name__}"
    binding = _GatedBinding()
    binding.conversation = conv
    service = OpenJarvisLLMService(binding)
    seen = {}

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await _interrupt(worker)
        await worker.queue_frame(_ctx(U1, A1, U2))
        await binding.wait_started(U2)
        seen["tasks"] = list(service._runs.values())
        seen["in_flight"] = len(RUN_TRACKER.in_flight(conv))

    with conversation_scope(conv):
        await _drive(service, steps, end)
        await _eventually(lambda: all(task.done() for task in seen["tasks"]))

    assert seen["in_flight"] == 2
    assert len(seen["tasks"]) == 2
    assert all(task.cancelled() for task in seen["tasks"])
    assert sorted(binding.cancelled) == sorted([U1["content"], U2["content"]])
    assert service._runs == {}
    assert RUN_TRACKER.in_flight(conv) == []


@pytest.mark.anyio
async def test_cleanup_cancels_every_run_and_closes_the_tracker():
    conv = "voice-parallel-cleanup"
    binding = _GatedBinding()
    binding.conversation = conv
    service = OpenJarvisLLMService(binding)

    async def ignore(*args, **kwargs):
        del args, kwargs

    service.push_frame = ignore
    with conversation_scope(conv):
        service._start_run(U1["content"], U1["content"], _agent_context(), *_quiet())
        await binding.wait_started(U1)
        [task] = service._runs.values()

        await service.cleanup()

    assert task.cancelled()
    assert binding.cancelled == [U1["content"]]
    assert RUN_TRACKER.in_flight(conv) == []


def _agent_context():
    from openjarvis.agents._stubs import AgentContext

    return AgentContext()


def _quiet():
    return nullcontext(), None


@pytest.mark.anyio
async def test_no_carried_prompt_is_built():
    binding = _GatedBinding()
    service = OpenJarvisLLMService(binding)

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await _interrupt(worker)
        await worker.queue_frame(_ctx(U1, A1, U2))
        await binding.wait_started(U2)

    await _drive(service, steps)

    assert binding.keys == [U1["content"], U2["content"]]
    assert binding.prompts[0] == U1["content"]
    assert all("<earlier_unfinished_request>" not in p for p in binding.prompts)


@pytest.mark.anyio
async def test_a_request_waiting_for_a_slot_survives_a_barge_in():
    binding = _GatedBinding(slots=2)
    service = OpenJarvisLLMService(binding)
    seen = {}

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await worker.queue_frame(_ctx(U1, A1, U2))
        await binding.wait_started(U2)
        await worker.queue_frame(_ctx(U1, A1, U2, A2, U3))
        await _eventually(lambda: U3["content"] in binding.keys)
        await asyncio.sleep(0.05)
        seen["third_waited"] = U3["content"] not in binding.started
        await _interrupt(worker)
        binding.let_go(U1)
        await binding.wait_started(U3)
        binding.let_go(U3)
        await binding.wait_finished(U3)
        await asyncio.sleep(0.05)

    sink = await _drive(service, steps)

    assert seen["third_waited"] is True
    assert binding.finished == [U1["content"], U3["content"]]
    assert U3["content"] not in binding.cancelled
    # The barge-in fenced every run's speech, the third's included.
    assert not sink.spoke(_reply(U3["content"]))


@pytest.mark.anyio
async def test_a_tool_inside_a_voice_run_sees_its_run_id():
    conv = "voice-parallel-run-scope"
    binding = _GatedBinding()
    binding.conversation = conv
    service = OpenJarvisLLMService(binding)

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        binding.let_go(U1)
        await binding.wait_finished(U1)

    with conversation_scope(conv):
        await _drive(service, steps)

    [(run_id, in_flight)] = binding.voice_runs
    assert run_id and in_flight == {run_id}


def test_inflight_prompt_names_requests_and_the_do_not_repeat_rule():
    prompt = inflight_prompt(["thêm hai cà phê sữa", "bỏ một ly"], "xem giỏ")

    assert prompt == (
        "<requests_in_progress>\n"
        "thêm hai cà phê sữa\n"
        "bỏ một ly\n"
        "</requests_in_progress>\n"
        "<latest_request>xem giỏ</latest_request>\n"
        "The requests in progress are already being handled in the background; "
        "do not perform them again. If the latest request asks about them, say "
        "they are still in progress unless background_actions shows their "
        "outcome. If it changes or removes what one of them adds, call "
        "await_earlier_requests first and then act on the real result; if it is "
        "unclear which item the customer means, ask before changing anything."
    )


def test_inflight_prompt_is_the_latest_alone_when_nothing_is_in_flight():
    assert inflight_prompt([], "xem giỏ") == "xem giỏ"
    # A request with no words (an unconfirmed speaker's turn) names nothing.
    assert inflight_prompt([""], "xem giỏ") == "xem giỏ"


@pytest.mark.anyio
async def test_a_second_run_is_told_the_first_runs_request():
    conv = "voice-parallel-inflight-prompt"
    binding = _GatedBinding()
    binding.conversation = conv
    service = OpenJarvisLLMService(binding)

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await worker.queue_frame(_ctx(U1, A1, U2))
        await binding.wait_started(U2)
        binding.let_go(U1)
        binding.let_go(U2)
        await binding.wait_finished(U1)
        await binding.wait_finished(U2)

    with conversation_scope(conv):
        await _drive(service, steps)

    first, second = binding.prompts
    assert first == U1["content"]
    assert second == inflight_prompt([U1["content"]], U2["content"])
    assert U1["content"] in second and U2["content"] in second


class _Verdicts:
    """A speaker tracker that rules each turn in turn."""

    def __init__(self, *verdicts):
        self._verdicts = list(verdicts)

    def take_turn_verdict(self):
        return self._verdicts.pop(0)


@pytest.mark.anyio
async def test_a_rejected_turns_words_are_never_shown_as_in_progress():
    from openjarvis.server.voice.speaker import Verdict

    conv = "voice-parallel-rejected-words"
    stranger = {"role": "user", "content": "thanh toán luôn đi"}
    binding = _GatedBinding()
    binding.conversation = conv
    responder = _Responder(kind="delegate")
    service = OpenJarvisLLMService(
        binding,
        responder=responder,
        speaker_tracker=_Verdicts(Verdict.ACCEPT, Verdict.REJECT, Verdict.ACCEPT),
    )
    seen = {}

    async def steps(worker, sink):
        await worker.queue_frame(_ctx(U1))
        await binding.wait_started(U1)
        await worker.queue_frame(_ctx(U1, A1, stranger))
        await _eventually(lambda: len(binding.prompts) == 2)
        seen["tracked"] = RUN_TRACKER.in_flight(conv)
        await worker.queue_frame(_ctx(U1, A1, stranger, A2, U3))
        await binding.wait_started(U3)
        for key in list(binding.keys):
            binding._event(binding.release, key).set()
        await _eventually(lambda: len(binding.finished) == 3)

    with conversation_scope(conv):
        await _drive(service, steps)

    first, rejected, third = binding.prompts
    # The unconfirmed turn is told what is in flight, but its words are no
    # one's request: no run records them, none names them, none is keyed by them.
    assert UNCERTAIN_SPEAKER_NOTE in rejected and stranger["content"] in rejected
    assert U1["content"] in rejected
    assert seen["tracked"] == [U1["content"], ""]
    assert CUSTOMER_REQUEST not in binding.contexts[binding.keys[1]].metadata
    assert third == inflight_prompt([U1["content"]], U3["content"])
    assert stranger["content"] not in third
    # The Responder is asked for the customer's turns only, and never told the
    # stranger's words are in progress.
    assert responder.calls == [U1["content"], U3["content"]]
    assert responder.in_progress == [[], [U1["content"]]]
