"""The orchestrator records writes and shows earlier turns' outcomes."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from openjarvis.agents._stubs import AgentContext, AgentToolStarted
from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.agents.runtime import NativeAgentRuntime
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import Role, ToolResult
from openjarvis.engine._stubs import StreamChunk
from openjarvis.kiosk.presentation import PresentationSessionManager
from openjarvis.tools._stubs import BaseTool, ToolSpec
from tests.agents.test_orchestrator import StreamingEngine, _stream_final_round
from tests.kiosk.test_presentation import _FakeMCPClient


def _tool_round(text: str, *calls: tuple[str, str]) -> list[StreamChunk]:
    """One decision round: optional visible text, then its tool calls."""
    chunks = [StreamChunk(content=text)] if text else []
    for index, (name, arguments) in enumerate(calls):
        chunks.append(
            StreamChunk(
                tool_calls=[
                    {
                        "index": index,
                        "id": f"call-{name}-{index}",
                        "function": {"name": name, "arguments": arguments},
                    }
                ]
            )
        )
    chunks.append(StreamChunk(finish_reason="tool_calls"))
    return chunks


class _Tool(BaseTool):
    def __init__(self, name, *, read_only=False, message="", block=None):
        self.tool_id = name
        self._name = name
        self._read_only = read_only
        self._message = message
        self._block = block
        self.started = threading.Event()

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
            metadata={"read_only": True} if self._read_only else {},
        )

    def execute(self, **params):
        del params
        self.started.set()
        if self._block is not None:
            assert self._block.wait(5)
        metadata = {"customer_message": self._message} if self._message else {}
        return ToolResult(
            tool_name=self._name, content="ok", success=True, metadata=metadata
        )


_CONVERSATIONS = (
    "c-record",
    "c-cancel",
    "c-context",
    "c-interrupt",
    "c-sync",
    "c-sync-cancel",
    "c-two",
    "c-unknown",
    "c-display",
    "c-raises",
    "c-handoff",
    "c-streaming",
    "c-done",
)


def _finished(obj, name: str) -> threading.Event:
    """Set when ``obj.name`` returns or raises, wherever it runs."""
    done = threading.Event()
    original = getattr(obj, name)

    def wrapper(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            done.set()

    setattr(obj, name, wrapper)
    return done


async def _until_status(conversation: str, status: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entries = ACTION_LEDGER.entries(conversation)
        if entries and entries[0].status == status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"no {status} entry for {conversation}")


@pytest.fixture(autouse=True)
def _clean_ledger():
    yield
    for conv in _CONVERSATIONS:
        ACTION_LEDGER.clear(conv)


def _runtime_contexts(engine) -> list[str]:
    return [
        message.content
        for messages, _ in engine.calls
        for message in messages
        if message.role == Role.SYSTEM and "<runtime_context>" in message.content
    ]


@pytest.mark.asyncio
async def test_writes_are_recorded_reads_are_not_and_own_writes_are_reported():
    write = _Tool("add", message="Đã thêm.")
    read = _Tool("menu", read_only=True)
    engine = StreamingEngine(
        [
            _tool_round("", ("add", "{}")),
            _tool_round("", ("menu", "{}")),
            _stream_final_round("Xong."),
        ]
    )
    agent = OrchestratorAgent(engine, "fake", tools=[write, read])

    with conversation_scope("c-record"):
        [event async for event in agent.run_stream("request")]

    [entry] = ACTION_LEDGER.entries("c-record")
    assert (entry.tool, entry.status, entry.reported) == ("add", "succeeded", True)


class _OneToolEngine:
    """Calls `add` until a tool message exists, then answers."""

    def __init__(self):
        self.calls = []

    def generate(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("streaming only")

    def supports_semantic_reasoning_stream(self, model):
        del model
        return True

    async def stream_full(self, messages, **kwargs):
        self.calls.append((list(messages), kwargs))
        if any(m.role == Role.TOOL for m in messages):
            yield StreamChunk(content="Xong.")
            yield StreamChunk(finish_reason="stop")
            return
        yield StreamChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": "add", "arguments": "{}"},
                }
            ]
        )
        yield StreamChunk(finish_reason="tool_calls")


@pytest.mark.asyncio
async def test_a_write_finishing_after_its_turn_was_cancelled_is_recorded():
    release = threading.Event()
    write = _Tool("add", message="Đã thêm.", block=release)
    binding = NativeAgentRuntime(
        OrchestratorAgent(_OneToolEngine(), "fake", tools=[write])
    ).bind(model="fake")

    with conversation_scope("c-cancel"):
        first = asyncio.create_task(binding.run("thêm", AgentContext()))
        assert await asyncio.to_thread(write.started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        entries = ACTION_LEDGER.entries("c-cancel")
        if entries and entries[0].status == "succeeded":
            break
        await asyncio.sleep(0.02)
    [entry] = ACTION_LEDGER.entries("c-cancel")
    assert (entry.status, entry.customer_message, entry.reported) == (
        "succeeded",
        "Đã thêm.",
        False,
    )
    assert entry.interrupted is True


def _seed(conv, *, message="Đã thêm hai cà phê sữa."):
    ACTION_LEDGER.start(conv, call_id="old-call", tool="add", run_id="old-run")
    ACTION_LEDGER.finish(
        conv,
        call_id="old-call",
        run_id="old-run",
        result=ToolResult(
            tool_name="add",
            content="ok",
            success=True,
            metadata={"customer_message": message},
        ),
    )


@pytest.mark.asyncio
async def test_each_round_sees_earlier_unreported_outcomes_and_completion_reports_them():  # noqa: E501
    _seed("c-context")
    engine = StreamingEngine([_stream_final_round("Đã thêm, menu đây.")])
    agent = OrchestratorAgent(engine, "fake", tools=[_Tool("menu", read_only=True)])

    with conversation_scope("c-context"):
        [event async for event in agent.run_stream("menu")]

    [context] = _runtime_contexts(engine)
    payload = json.loads(
        context.split("<runtime_context>")[1].split("</runtime_context>")[0]
    )
    assert payload["background_actions"] == [
        {"tool": "add", "status": "succeeded", "message": "Đã thêm hai cà phê sữa."}
    ]
    contract = context.split("<runtime_context>")[0]
    assert "background_actions" in contract
    assert "already_told" in contract
    assert "Never proceed to payment past a failed or unknown" in contract
    assert ACTION_LEDGER.entries("c-context")[0].reported is True


class _StallEngine:
    def __init__(self):
        self.recorded = asyncio.Event()

    def generate(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("streaming only")

    def supports_semantic_reasoning_stream(self, model):
        del model
        return True

    async def stream_full(self, messages, **kwargs):
        del messages, kwargs
        self.recorded.set()
        await asyncio.Event().wait()
        yield StreamChunk(finish_reason="stop")  # pragma: no cover


@pytest.mark.asyncio
async def test_an_interrupted_run_marks_nothing_reported():
    _seed("c-interrupt")
    engine = _StallEngine()
    binding = NativeAgentRuntime(
        OrchestratorAgent(engine, "fake", tools=[_Tool("menu", read_only=True)])
    ).bind(model="fake")

    with conversation_scope("c-interrupt"):
        task = asyncio.create_task(binding.run("menu", AgentContext()))
        await asyncio.wait_for(engine.recorded.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    [entry] = ACTION_LEDGER.entries("c-interrupt")
    assert entry.seen_by_run != "" and entry.reported is False


def test_the_blocking_path_reports_on_completion_too():
    from tests.agents.fake_engine import FakeEngine

    _seed("c-sync")
    engine = FakeEngine([{"content": "Xong.", "finish_reason": "stop", "usage": {}}])
    agent = OrchestratorAgent(engine, "fake", tools=[_Tool("menu", read_only=True)])

    with conversation_scope("c-sync"):
        agent.run("menu")

    assert ACTION_LEDGER.entries("c-sync")[0].reported is True


@pytest.mark.asyncio
async def test_an_interrupted_sync_fallback_run_marks_nothing_reported():
    """A sync-only engine runs `run` on the lease worker; it must not report."""
    from tests.agents.fake_engine import FakeEngine

    release = threading.Event()
    write = _Tool("add", message="Đã thêm.", block=release)
    engine = FakeEngine(
        [{"tool_calls": [{"id": "call-1", "name": "add", "arguments": "{}"}]}]
    )
    # One turn: after the tool result the run ends normally (max-turns ending).
    agent = OrchestratorAgent(engine, "fake", tools=[write], max_turns=1)
    run_ended = _finished(agent, "run")
    binding = NativeAgentRuntime(agent).bind(model="fake")

    with conversation_scope("c-sync-cancel"):
        first = asyncio.create_task(binding.run("thêm", AgentContext()))
        assert await asyncio.to_thread(write.started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
    release.set()

    await _until_status("c-sync-cancel", "succeeded")
    assert await asyncio.to_thread(run_ended.wait, 2)
    [entry] = ACTION_LEDGER.entries("c-sync-cancel")
    assert (entry.status, entry.reported) == ("succeeded", False)


@pytest.mark.asyncio
async def test_every_completed_call_of_a_cut_round_runs():
    """Spec 2026-10-17 barge-in §5.2: barge-in stops speech, not tools."""
    release = threading.Event()
    first = _Tool("first", message="Đã thêm.", block=release)
    second = _Tool("second")
    engine = StreamingEngine(
        [_tool_round("", ("first", "{}"), ("second", "{}")), _stream_final_round("")]
    )
    agent = OrchestratorAgent(
        engine, "fake", tools=[first, second], parallel_tools=False
    )
    batch_ended = _finished(agent, "_collect_function_tool_results")
    binding = NativeAgentRuntime(agent).bind(model="fake")

    with conversation_scope("c-two"):
        task = asyncio.create_task(binding.run("thêm", AgentContext()))
        assert await asyncio.to_thread(first.started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    release.set()

    await _until_status("c-two", "succeeded")
    assert await asyncio.to_thread(batch_ended.wait, 2)
    assert {(e.tool, e.status) for e in ACTION_LEDGER.entries("c-two")} == {
        ("first", "succeeded"),
        ("second", "succeeded"),
    }
    assert all(e.interrupted for e in ACTION_LEDGER.entries("c-two"))


@pytest.mark.asyncio
async def test_a_call_to_an_unknown_tool_is_not_recorded_as_a_write():
    engine = StreamingEngine(
        [_tool_round("", ("ghost", "{}")), _stream_final_round("Xong.")]
    )
    agent = OrchestratorAgent(engine, "fake", tools=[_Tool("menu", read_only=True)])

    with conversation_scope("c-unknown"):
        [event async for event in agent.run_stream("request")]

    assert ACTION_LEDGER.entries("c-unknown") == []


class _AfterCancel(BaseTool):
    """Waits for ``block``, then runs ``step`` (a nested display or a raise)."""

    def __init__(self, name, block, step):
        self.tool_id = name
        self._name = name
        self._block = block
        self._step = step
        self.started = threading.Event()

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params):
        del params
        self.started.set()
        assert self._block.wait(5)
        return self._step()


async def _cancel_then_release(conversation, tool, release):
    agent = OrchestratorAgent(_OneToolEngine(), "fake", tools=[tool])
    binding = NativeAgentRuntime(agent).bind(model="fake")
    with conversation_scope(conversation):
        task = asyncio.create_task(binding.run("request", AgentContext()))
        assert await asyncio.to_thread(tool.started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    release.set()


@pytest.mark.asyncio
async def test_a_dispatched_write_publishes_its_display_after_the_turn_was_cancelled():
    bus = EventBus(record_history=True)
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    manager.ensure("http://127.0.0.1:5173")
    release = threading.Event()
    published = []

    def step():
        result = manager.publish({"view": "cart", "text": "kept"})
        published.append(result)
        return ToolResult(tool_name="add", content="ok", success=result.success)

    write = _AfterCancel("add", release, step)
    await _cancel_then_release("c-display", write, release)

    await _until_status("c-display", "succeeded")
    [entry] = ACTION_LEDGER.entries("c-display")
    assert entry.tool == "add"
    assert published and published[0].success
    assert any(e.event_type == EventType.DISPLAY_UPDATE for e in bus.history)


@pytest.mark.asyncio
async def test_a_cancellation_raised_after_the_write_started_is_recorded_unknown():
    release = threading.Event()

    def step():
        raise asyncio.CancelledError("agent_turn_interrupted")

    write = _AfterCancel("add", release, step)
    await _cancel_then_release("c-raises", write, release)

    await _until_status("c-raises", "unknown")
    [entry] = ACTION_LEDGER.entries("c-raises")
    assert (entry.tool, entry.status) == ("add", "unknown")


@pytest.mark.asyncio
async def test_a_barge_in_during_the_tool_started_hand_off_still_runs_the_call():
    write = _Tool("add", message="Đã thêm.")
    engine = StreamingEngine([_tool_round("", ("add", "{}")), _stream_final_round("")])
    binding = NativeAgentRuntime(OrchestratorAgent(engine, "fake", tools=[write])).bind(
        model="fake"
    )
    handed_off = asyncio.Event()

    async def consume():
        async for event in binding.run_stream("thêm", AgentContext()):
            if isinstance(event, AgentToolStarted):
                handed_off.set()
                await asyncio.Event().wait()

    with conversation_scope("c-handoff"):
        task = asyncio.create_task(consume())
        await asyncio.wait_for(handed_off.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    await _until_status("c-handoff", "succeeded")
    [entry] = ACTION_LEDGER.entries("c-handoff")
    assert (entry.tool, entry.interrupted) == ("add", True)


class _HalfCallEngine:
    """Streams part of a tool call, then waits: the call is never completed."""

    def __init__(self):
        self.streaming = asyncio.Event()

    def generate(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("streaming only")

    def supports_semantic_reasoning_stream(self, model):
        del model
        return True

    async def stream_full(self, messages, **kwargs):
        del messages, kwargs
        yield StreamChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": "add", "arguments": "{"},
                }
            ]
        )
        self.streaming.set()
        await asyncio.Event().wait()
        yield StreamChunk(finish_reason="tool_calls")  # pragma: no cover


@pytest.mark.asyncio
async def test_a_tool_call_still_streaming_at_barge_in_does_not_run():
    write = _Tool("add")
    engine = _HalfCallEngine()
    binding = NativeAgentRuntime(OrchestratorAgent(engine, "fake", tools=[write])).bind(
        model="fake"
    )

    with conversation_scope("c-streaming"):
        task = asyncio.create_task(binding.run("thêm", AgentContext()))
        await asyncio.wait_for(engine.streaming.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0.1)

    assert not write.started.is_set()
    assert ACTION_LEDGER.entries("c-streaming") == []


@pytest.mark.asyncio
async def test_a_completed_run_does_not_flag_its_entries():
    write = _Tool("add", message="Đã thêm.")
    engine = StreamingEngine(
        [_tool_round("", ("add", "{}")), _stream_final_round("Xong.")]
    )
    binding = NativeAgentRuntime(OrchestratorAgent(engine, "fake", tools=[write])).bind(
        model="fake"
    )

    with conversation_scope("c-done"):
        await binding.run("thêm", AgentContext())

    [entry] = ACTION_LEDGER.entries("c-done")
    assert (entry.status, entry.interrupted) == ("succeeded", False)
