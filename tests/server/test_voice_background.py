"""Outcomes that finish while no turn runs are reported once, in order."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    StartFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents._stubs import AgentResult, AgentRunCompleted, AgentTextDelta
from openjarvis.agents.action_ledger import ActionLedger
from openjarvis.core.types import ToolResult
from openjarvis.server.voice.background import (
    BackgroundReporter,
    event_text,
    report_text,
)
from openjarvis.server.voice.lifecycle import VoiceSessionLifecycle
from openjarvis.server.voice.llm import OpenJarvisLLMService

CONV = "voice-1"
PAYMENT_EVENT = (
    '{"event":"payment_completed","order_id":"order-1",'
    '"payment_status":"paid","view":"bill"}'
)


def _finish(ledger, call_id, *, success=True, message="", run_id="r1"):
    ledger.start(CONV, call_id=call_id, tool="add", run_id=run_id)
    metadata = {"customer_message": message} if message else {}
    ledger.finish(
        CONV,
        call_id=call_id,
        run_id=run_id,
        result=ToolResult(
            tool_name="add",
            content="ok" if success else "merchant refused",
            success=success,
            metadata=metadata,
        ),
    )


class _Fakes:
    def __init__(self, *, event_completes=True):
        self.spoken: list[str] = []
        self.events: list[str] = []
        self.flushes = 0
        self._event_completes = event_completes

    async def speak(self, text):
        self.spoken.append(text)

    async def flush(self):
        self.flushes += 1

    async def run_event_turn(self, event):
        self.events.append(event)
        return self._event_completes


def _reporter(ledger, fakes):
    return BackgroundReporter(
        ledger,
        CONV,
        speak=fakes.speak,
        flush=fakes.flush,
        run_event_turn=fakes.run_event_turn,
    )


@pytest.mark.anyio
async def test_two_finished_outcomes_are_spoken_once_in_order():
    ledger, fakes = ActionLedger(), _Fakes()
    _finish(ledger, "a", message="Đã thêm cà phê sữa.")
    _finish(ledger, "b", message="Đã thêm trà đào.")
    reporter = _reporter(ledger, fakes)

    await reporter.on_idle()
    await reporter.on_idle()

    assert fakes.spoken == ["Đã thêm cà phê sữa.", "Đã thêm trà đào."]
    assert ledger.unreported_finished(CONV) == []


@pytest.mark.anyio
async def test_a_failure_goes_through_an_event_turn_and_is_then_reported():
    ledger, fakes = ActionLedger(), _Fakes()
    _finish(ledger, "a", success=False)
    reporter = _reporter(ledger, fakes)

    await reporter.on_idle()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert fakes.spoken == []
    assert len(fakes.events) == 1 and "merchant refused" in fakes.events[0]
    assert ledger.unreported_finished(CONV) == []


@pytest.mark.anyio
async def test_interrupted_event_turn_leaves_the_outcome_unreported():
    ledger, fakes = ActionLedger(), _Fakes(event_completes=False)
    _finish(ledger, "a", success=False)
    reporter = _reporter(ledger, fakes)

    await reporter.on_idle()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(ledger.unreported_finished(CONV)) == 1


@pytest.mark.anyio
async def test_a_finished_outcome_from_a_worker_thread_triggers_a_flush():
    ledger, fakes = ActionLedger(), _Fakes()
    reporter = _reporter(ledger, fakes)
    reporter.start()

    await asyncio.to_thread(_finish, ledger, "a", message="Đã thêm.")
    for _ in range(50):
        if fakes.flushes:
            break
        await asyncio.sleep(0.01)
    reporter.stop()

    assert fakes.flushes == 1


@pytest.mark.anyio
async def test_payment_confirmation_uses_model_event_turn_once_without_fixed_speech():
    ledger, fakes = ActionLedger(), _Fakes()
    ledger.start(CONV, call_id="order-1", tool="workflow_result", run_id="payment")
    ledger.finish(
        CONV,
        call_id="order-1",
        run_id="payment",
        result=ToolResult(
            tool_name="workflow_result",
            content="paid",
            metadata={"event_context": PAYMENT_EVENT},
        ),
    )
    reporter = _reporter(ledger, fakes)

    await reporter.on_idle()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await reporter.on_idle()

    assert fakes.spoken == []
    [event] = fakes.events
    assert "order-1" in event and "paid" in event
    assert "not the customer's words" in event
    assert "own words" in event
    assert ledger.unreported_finished(CONV) == []


@pytest.mark.anyio
async def test_worker_notification_retains_voice_context_instead_of_http_context():
    from openjarvis.core.conversation import conversation_scope, current_conversation_id

    ledger, fakes = ActionLedger(), _Fakes()
    contexts = []

    async def flush():
        contexts.append(current_conversation_id())

    reporter = BackgroundReporter(
        ledger,
        CONV,
        speak=fakes.speak,
        flush=flush,
        run_event_turn=fakes.run_event_turn,
    )
    with conversation_scope(CONV):
        reporter.start()
    try:
        with conversation_scope("http-request"):
            await asyncio.to_thread(_finish, ledger, "a", message="Đã thêm.")
        for _ in range(50):
            if contexts:
                break
            await asyncio.sleep(0.01)
        assert contexts == [CONV]
    finally:
        reporter.stop()


def test_event_text_names_tool_status_and_detail_for_the_model():
    ledger = ActionLedger()
    _finish(ledger, "a", success=False)
    [entry] = ledger.unreported_finished(CONV)
    text = event_text(entry)
    assert "not the customer's words" in text
    assert "add" in text and "failed" in text and "merchant refused" in text


class _RecordingBinding:
    model = "test-model"

    def __init__(self, reply="Dạ."):
        self.reply = reply
        self.calls = []
        self.done = asyncio.Event()

    async def run_stream(self, prompt, context):
        self.calls.append((prompt, [m.text for m in context.conversation.messages]))
        yield AgentTextDelta(self.reply)
        yield AgentRunCompleted(AgentResult(content=self.reply, turns=1))
        self.done.set()


@pytest.mark.anyio
@pytest.mark.parametrize("fast_model", [False, True])
@pytest.mark.parametrize(
    "reply",
    [
        "Đã thanh toán.",
        "Payment confirmed.",
    ],
    ids=["vietnamese", "english"],
)
async def test_touch_only_payment_speaks_the_model_response_before_any_user_turn(
    reply, fast_model
):
    notification = _RecordingBinding(reply)

    class SlowBinding(_RecordingBinding):
        def with_model(self, model):
            assert model == "fast-model"
            return notification

        async def run_stream(self, prompt, context):
            await asyncio.Event().wait()  # Heavy model must not delay notification.
            yield AgentTextDelta("late")

    binding = SlowBinding() if fast_model else notification
    service = OpenJarvisLLMService(
        binding,
        responder=SimpleNamespace(model="fast-model") if fast_model else None,
    )
    texts = []
    ready = asyncio.Event()

    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, StartFrame):
                ready.set()
            if isinstance(frame, LLMTextFrame):
                texts.append(frame.text)
            await self.push_frame(frame, direction)

    worker = PipelineWorker(
        Pipeline([service, Sink()]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    ledger = ActionLedger()
    ledger.start(CONV, call_id="order-1", tool="workflow_result", run_id="payment")
    ledger.finish(
        CONV,
        call_id="order-1",
        run_id="payment",
        result=ToolResult(
            tool_name="workflow_result",
            content="paid",
            metadata={"event_context": PAYMENT_EVENT},
        ),
    )
    [entry] = ledger.unreported_finished(CONV)
    event = event_text(entry)
    outcome = {}

    async def drive():
        await asyncio.wait_for(ready.wait(), 2)
        outcome["completed"] = await service.run_event_turn(event)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=2)

    assert outcome["completed"] is True
    assert "".join(texts) == reply
    [(prompt, history)] = notification.calls
    assert prompt == event
    assert event not in history


@pytest.mark.anyio
async def test_event_turn_sees_the_session_history_without_writing_to_it():
    binding = _RecordingBinding()
    service = OpenJarvisLLMService(binding)
    llm_context = LLMContext(
        messages=[{"role": "user", "content": "thêm hai cà phê sữa"}]
    )
    worker = PipelineWorker(
        Pipeline([service]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    outcome = {}

    async def drive():
        await asyncio.sleep(0.01)
        await worker.queue_frame(LLMContextFrame(llm_context))
        await asyncio.wait_for(binding.done.wait(), 2)
        outcome["completed"] = await service.run_event_turn("EVENT: add failed")
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=10)

    prompt, history = binding.calls[1]
    assert prompt == "EVENT: add failed"
    assert "thêm hai cà phê sữa" in history
    assert outcome["completed"] is True
    assert [m["content"] for m in llm_context.get_messages()] == ["thêm hai cà phê sữa"]


class _BlockingBinding:
    """The first turn blocks until cancelled; any other turn would be recorded."""

    model = "test-model"

    def __init__(self):
        self.prompts: list[str] = []
        self.blocking = asyncio.Event()

    async def run_stream(self, prompt, context):
        del context
        self.prompts.append(prompt)
        self.blocking.set()
        await asyncio.Event().wait()
        yield AgentRunCompleted(AgentResult(content="", turns=1))


@pytest.mark.anyio
async def test_event_turn_does_not_start_while_a_customer_turn_runs():
    binding = _BlockingBinding()
    service = OpenJarvisLLMService(binding)
    worker = PipelineWorker(
        Pipeline([service]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    outcome = {}

    async def drive():
        await asyncio.sleep(0.01)
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "thêm hai cà phê"}])
            )
        )
        await asyncio.wait_for(binding.blocking.wait(), 2)
        outcome["started"] = await service.run_event_turn("EVENT")
        outcome["prompts"] = list(binding.prompts)
        await worker.queue_frame(InterruptionFrame())
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), timeout=10)

    assert outcome["started"] is False
    assert outcome["prompts"] == ["thêm hai cà phê"]


async def _noop(self, frame, direction):
    pass


def _wired(monkeypatch, ledger, fakes):
    """The real lifecycle and reporter, where speaking re-enters on_idle."""
    monkeypatch.setattr(FrameProcessor, "process_frame", _noop)
    reporter = None

    async def on_idle():
        await reporter.on_idle()

    lifecycle = VoiceSessionLifecycle(on_idle=on_idle)
    reporter = BackgroundReporter(
        ledger,
        CONV,
        speak=lifecycle.report,
        flush=lifecycle.flush,
        run_event_turn=fakes.run_event_turn,
    )
    spoken: list[str] = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        del direction
        if isinstance(frame, TTSSpeakFrame):
            spoken.append(frame.text)

    monkeypatch.setattr(lifecycle, "push_frame", push)
    return lifecycle, spoken


@pytest.mark.anyio
async def test_real_lifecycle_speaks_each_verified_outcome_once(monkeypatch):
    ledger, fakes = ActionLedger(), _Fakes()
    for call_id, text in (("a", "A."), ("b", "B."), ("c", "C.")):
        _finish(ledger, call_id, message=text)
    lifecycle, spoken = _wired(monkeypatch, ledger, fakes)

    await lifecycle.flush()

    assert spoken == ["A.", "B.", "C."]
    assert ledger.unreported_finished(CONV) == []


@pytest.mark.anyio
async def test_real_lifecycle_starts_one_event_turn_for_one_failure(monkeypatch):
    ledger, fakes = ActionLedger(), _Fakes()
    _finish(ledger, "a", message="A.")
    _finish(ledger, "b", success=False)
    lifecycle, spoken = _wired(monkeypatch, ledger, fakes)

    await lifecycle.flush()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert spoken == ["A."]
    assert len(fakes.events) == 1


class _ThinkingBinding:
    """Admits one run at a time, like NativeAgentRuntime; event turns think long."""

    model = "test-model"

    def __init__(self, think: float):
        self._think = think
        self.runtime = asyncio.Lock()
        self.admitted: dict[str, float] = {}
        self.thinking = asyncio.Event()
        self.answered = asyncio.Event()
        self.cut: list[str] = []

    async def run_stream(self, prompt, context):
        del context
        event = prompt not in CUSTOMER_WORDS
        async with self.runtime:
            self.admitted[prompt] = asyncio.get_running_loop().time()
            if event:
                self.thinking.set()
                try:
                    await asyncio.sleep(self._think)
                except asyncio.CancelledError:
                    self.cut.append(prompt)
                    raise
            yield AgentTextDelta("Dạ.")
            yield AgentRunCompleted(AgentResult(content="Dạ.", turns=1))
        if not event:
            self.answered.set()


THINK = 3.0
U1 = {"role": "user", "content": "thêm hai cà phê sữa"}
A1 = {"role": "assistant", "content": "Dạ."}
U2 = {"role": "user", "content": "cho xem menu"}
CUSTOMER_WORDS = {U1["content"], U2["content"]}


def _ctx(*messages):
    return LLMContextFrame(LLMContext(messages=[dict(m) for m in messages]))


class _ReplyEnds(FrameProcessor):
    """Downstream of the service: a reply's end frame means it stopped answering."""

    def __init__(self):
        super().__init__()
        self.ended = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseEndFrame):
            self.ended.set()
        await self.push_frame(frame, direction)


async def _drive(service, steps, end=EndFrame):
    replies = _ReplyEnds()
    worker = PipelineWorker(
        Pipeline([service, replies]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.01)
        try:
            await steps(worker, replies)
        finally:
            # Ends the session even when a step failed, so no test can hang.
            await worker.queue_frame(end())

    await runner.add_workers(worker)
    # A failed step is raised only once the pipeline has ended.
    for result in await asyncio.wait_for(
        asyncio.gather(runner.run(), drive(), return_exceptions=True), timeout=10
    ):
        if isinstance(result, BaseException):
            raise result


async def _answered_once(worker, replies, binding):
    """A first customer turn, so the session has history for event turns."""
    await worker.queue_frame(_ctx(U1))
    await asyncio.wait_for(replies.ended.wait(), 2)
    binding.answered.clear()


@pytest.mark.anyio
async def test_a_customer_turn_does_not_wait_for_an_event_turn_to_think():
    """The customer wins at once: the event turn is cut and its outcome kept."""
    ledger, fakes = ActionLedger(), _Fakes()
    _finish(ledger, "a", success=False)
    binding = _ThinkingBinding(THINK)
    service = OpenJarvisLLMService(binding)
    returned: list[bool] = []

    async def run_event_turn(event):
        returned.append(await service.run_event_turn(event))
        return returned[-1]

    reporter = BackgroundReporter(
        ledger,
        CONV,
        speak=fakes.speak,
        flush=fakes.flush,
        run_event_turn=run_event_turn,
    )
    outcome = {}

    async def steps(worker, replies):
        await _answered_once(worker, replies, binding)
        await reporter.on_idle()
        await asyncio.wait_for(binding.thinking.wait(), 2)
        spoke = asyncio.get_running_loop().time()
        await worker.queue_frame(_ctx(U1, A1, U2))
        await asyncio.wait_for(binding.answered.wait(), THINK / 2)
        outcome["waited"] = binding.admitted["cho xem menu"] - spoke
        for _ in range(100):
            if returned:
                break
            await asyncio.sleep(0.01)

    await _drive(service, steps)

    assert outcome["waited"] < 0.5 < THINK
    assert returned == [False]
    assert len(binding.cut) == 1
    # Not told by the cut event turn: the customer's turn sees it instead.
    assert [e.call_id for e in ledger.unreported_finished(CONV)] == ["a"]


@pytest.mark.anyio
async def test_an_interruption_cuts_a_running_event_turn_at_once():
    binding = _ThinkingBinding(THINK)
    service = OpenJarvisLLMService(binding)
    outcome = {}

    async def steps(worker, replies):
        await _answered_once(worker, replies, binding)
        event = asyncio.create_task(service.run_event_turn("EVENT: add failed"))
        await asyncio.wait_for(binding.thinking.wait(), 2)
        await worker.queue_frame(InterruptionFrame())
        outcome["completed"] = await asyncio.wait_for(event, THINK / 3)
        outcome["runtime_free"] = not binding.runtime.locked()

    await _drive(service, steps)

    assert outcome == {"completed": False, "runtime_free": True}
    assert binding.cut == ["EVENT: add failed"]


@pytest.mark.anyio
@pytest.mark.parametrize("end", [EndFrame, CancelFrame])
async def test_the_session_ending_cuts_a_running_event_turn(end):
    binding = _ThinkingBinding(THINK)
    service = OpenJarvisLLMService(binding)
    events = []

    async def steps(worker, replies):
        await _answered_once(worker, replies, binding)
        events.append(asyncio.create_task(service.run_event_turn("EVENT: add failed")))
        await asyncio.wait_for(binding.thinking.wait(), 2)

    await _drive(service, steps, end)

    assert await asyncio.wait_for(events[0], THINK / 3) is False
    assert binding.cut == ["EVENT: add failed"]


@pytest.mark.anyio
async def test_stopping_the_reporter_cuts_its_event_turn():
    ledger = ActionLedger()
    _finish(ledger, "a", success=False)
    started, cut = asyncio.Event(), asyncio.Event()

    async def run_event_turn(event):
        del event
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cut.set()
            raise
        return True

    reporter = BackgroundReporter(
        ledger,
        CONV,
        speak=_Fakes().speak,
        flush=_Fakes().flush,
        run_event_turn=run_event_turn,
    )
    reporter.start()
    await reporter.on_idle()
    await asyncio.wait_for(started.wait(), 1)

    reporter.stop()

    await asyncio.wait_for(cut.wait(), 1)
    assert [e.call_id for e in ledger.unreported_finished(CONV)] == ["a"]


# --- late results of interrupted requests (spec 2026-10-17 barge-in §5.6) ---

QR_LINE = "Quét mã QR để thanh toán."


def _finish_interrupted(ledger, call_id="a", *, message=QR_LINE, run_id="cut"):
    ledger.mark_run_interrupted(CONV, run_id)
    ledger.start(CONV, call_id=call_id, tool="skill_checkout", run_id=run_id)
    ledger.finish(
        CONV,
        call_id=call_id,
        run_id=run_id,
        result=ToolResult(
            tool_name="skill_checkout",
            content="ok",
            success=True,
            metadata={"customer_message": message},
        ),
    )


def _phrasing_reporter(ledger, fakes, outcome):
    phrased: list[str] = []

    async def run_phrased_report(event):
        phrased.append(event)
        return outcome

    reporter = BackgroundReporter(
        ledger,
        CONV,
        speak=fakes.speak,
        flush=fakes.flush,
        run_event_turn=fakes.run_event_turn,
        run_phrased_report=run_phrased_report if outcome is not None else None,
    )
    return reporter, phrased


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.anyio
async def test_an_interrupted_success_is_phrased_not_spoken_verbatim():
    ledger, fakes = ActionLedger(), _Fakes()
    _finish_interrupted(ledger)
    reporter, phrased = _phrasing_reporter(ledger, fakes, "spoken")

    await reporter.on_idle()
    await _settle()

    [entry] = ledger.entries(CONV)
    assert fakes.spoken == []
    assert phrased == [report_text(entry)]
    assert fakes.events == []
    assert ledger.unreported_finished(CONV) == []


@pytest.mark.anyio
async def test_an_uninterrupted_success_is_still_spoken_verbatim():
    ledger, fakes = ActionLedger(), _Fakes()
    _finish(ledger, "a", message="Đã thêm cà phê sữa.")
    reporter, phrased = _phrasing_reporter(ledger, fakes, "spoken")

    await reporter.on_idle()
    await _settle()

    assert fakes.spoken == ["Đã thêm cà phê sữa."]
    assert phrased == []


@pytest.mark.anyio
async def test_phrased_report_cut_by_customer_activity_leaves_entry_unreported():
    ledger, fakes = ActionLedger(), _Fakes()
    _finish_interrupted(ledger)
    reporter, phrased = _phrasing_reporter(ledger, fakes, "cut")

    await reporter.on_idle()
    await _settle()

    assert len(phrased) == 1
    assert fakes.spoken == [] and fakes.events == []
    assert [e.call_id for e in ledger.unreported_finished(CONV)] == ["a"]


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["failed", None])
async def test_empty_or_unconfigured_phrasing_falls_back_to_event_turn(outcome):
    ledger, fakes = ActionLedger(), _Fakes()
    _finish_interrupted(ledger)
    reporter, _ = _phrasing_reporter(ledger, fakes, outcome)

    await reporter.on_idle()
    await _settle()

    [entry] = ledger.entries(CONV)
    assert fakes.spoken == []
    assert fakes.events == [event_text(entry)]
    assert ledger.unreported_finished(CONV) == []


def test_report_text_names_tool_status_confirmation_and_the_interruption():
    ledger = ActionLedger()
    _finish_interrupted(ledger)
    [entry] = ledger.entries(CONV)

    text = report_text(entry)

    assert "skill_checkout" in text and "succeeded" in text
    assert QR_LINE in text
    assert "interrupted" in text


class _FakeResponder:
    model = "fast"

    def __init__(self, line="", *, delay=0.0):
        self._line = line
        self._delay = delay
        self.events: list[tuple[str, str]] = []
        self.started = asyncio.Event()

    async def respond(self, utterance, conversation="", on_delegate=None):
        del utterance, conversation, on_delegate
        return None

    async def report(self, event, conversation=""):
        self.events.append((event, conversation))
        self.started.set()
        await asyncio.sleep(self._delay)
        return self._line


class _Texts(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.texts: list[LLMTextFrame] = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.texts.append(frame)
        await self.push_frame(frame, direction)


async def _drive_texts(service, steps):
    texts = _Texts()
    worker = PipelineWorker(
        Pipeline([service, texts]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.01)
        try:
            await steps(worker)
        finally:
            await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    for result in await asyncio.wait_for(
        asyncio.gather(runner.run(), drive(), return_exceptions=True), timeout=10
    ):
        if isinstance(result, BaseException):
            raise result
    return texts.texts


@pytest.mark.anyio
async def test_run_phrased_report_speaks_one_line_through_the_responder():
    from openjarvis.server.voice.llm import FLUSH_TTS_METADATA

    responder = _FakeResponder("Đơn đã tạo, QR trên màn hình.")
    service = OpenJarvisLLMService(_RecordingBinding(), responder=responder)
    outcome = {}

    async def steps(worker):
        del worker
        outcome["result"] = await service.run_phrased_report("EVENT")

    texts = await _drive_texts(service, steps)

    assert outcome["result"] == "spoken"
    assert [e for e, _ in responder.events] == ["EVENT"]
    assert [t.text.strip() for t in texts] == ["Đơn đã tạo, QR trên màn hình."]
    assert texts[0].metadata.get(FLUSH_TTS_METADATA) is True


@pytest.mark.anyio
async def test_run_phrased_report_times_out_to_failed(monkeypatch):
    monkeypatch.setattr("openjarvis.server.voice.llm.PHRASE_TIMEOUT_SECONDS", 0.05)
    responder = _FakeResponder("late", delay=1.0)
    service = OpenJarvisLLMService(_RecordingBinding(), responder=responder)
    outcome = {}

    async def steps(worker):
        del worker
        outcome["result"] = await service.run_phrased_report("EVENT")

    texts = await _drive_texts(service, steps)

    assert outcome["result"] == "failed"
    assert texts == []


@pytest.mark.anyio
async def test_run_phrased_report_without_a_responder_fails():
    service = OpenJarvisLLMService(_RecordingBinding())

    assert await service.run_phrased_report("EVENT") == "failed"


@pytest.mark.anyio
async def test_run_phrased_report_is_cut_by_an_interruption():
    responder = _FakeResponder("late", delay=THINK)
    service = OpenJarvisLLMService(_RecordingBinding(), responder=responder)
    outcome = {}

    async def steps(worker):
        task = asyncio.create_task(service.run_phrased_report("EVENT"))
        await asyncio.wait_for(responder.started.wait(), 2)
        await worker.queue_frame(InterruptionFrame())
        outcome["result"] = await asyncio.wait_for(task, THINK / 3)

    texts = await _drive_texts(service, steps)

    assert outcome["result"] == "cut"
    assert texts == []


@pytest.mark.anyio
async def test_run_phrased_report_does_not_start_while_a_customer_turn_runs():
    binding = _BlockingBinding()
    responder = _FakeResponder("line")
    service = OpenJarvisLLMService(binding, responder=responder)
    outcome = {}

    async def steps(worker):
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": "thêm hai cà phê"}])
            )
        )
        await asyncio.wait_for(binding.blocking.wait(), 2)
        outcome["result"] = await service.run_phrased_report("EVENT")
        await worker.queue_frame(InterruptionFrame())

    await _drive_texts(service, steps)

    assert outcome["result"] == "cut"
    assert responder.events == []
