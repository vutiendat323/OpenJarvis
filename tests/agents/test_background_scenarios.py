"""Interrupt while a write runs: reads go on, writes queue, payment stays fresh."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.agents.runtime import NativeAgentRuntime
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import Role, ToolResult
from openjarvis.engine._stubs import StreamChunk
from openjarvis.tools._stubs import BaseTool, ToolSpec


def _latest_user(messages) -> str:
    return next(m.content for m in reversed(messages) if m.role == Role.USER)


def _tool_results(messages) -> list[str]:
    return [m.content for m in messages if m.role == Role.TOOL]


def _call(name, arguments="{}", call_id=None):
    return [
        StreamChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": call_id or f"call-{name}",
                    "function": {"name": name, "arguments": arguments},
                }
            ]
        ),
        StreamChunk(finish_reason="tool_calls"),
    ]


def _say(text):
    return [StreamChunk(content=text), StreamChunk(finish_reason="stop")]


class _Engine:
    def __init__(self, script):
        self._script = script
        self.calls = []

    def generate(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("streaming only")

    def supports_semantic_reasoning_stream(self, model):
        del model
        return True

    async def stream_full(self, messages, **kwargs):
        del kwargs
        self.calls.append(list(messages))
        for chunk in self._script(messages):
            yield chunk


class _Tool(BaseTool):
    def __init__(self, name, handler, *, read_only=False):
        self.tool_id = name
        self._name = name
        self._handler = handler
        self._read_only = read_only

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description="scenario tool",
            parameters={
                "type": "object",
                "properties": {"revision": {"type": "integer"}},
            },
            metadata={"read_only": True} if self._read_only else {},
        )

    def execute(self, **params):
        return self._handler(**params)


async def _interrupt_first(binding, request, started: threading.Event):
    first = asyncio.create_task(binding.run(request, AgentContext()))
    assert await asyncio.to_thread(started.wait, 1)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first


@pytest.mark.asyncio
async def test_add_then_menu_reads_at_once_and_reports_the_add():
    conv = "scenario-menu"
    add_started, add_release = threading.Event(), threading.Event()
    menu_calls = []

    def add(**params):
        add_started.set()
        assert add_release.wait(5)
        return ToolResult(
            tool_name="add_item",
            content="ok",
            success=True,
            metadata={"customer_message": "Đã thêm hai cà phê sữa."},
        )

    def menu(**params):
        menu_calls.append(time.monotonic())
        add_release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if [e.status for e in ACTION_LEDGER.entries(conv)] == ["succeeded"]:
                break
            time.sleep(0.01)
        return ToolResult(tool_name="menu", content="menu rows", success=True)

    def script(messages):
        if _latest_user(messages) == "thêm hai cà phê sữa":
            return _call("add_item")
        if not _tool_results(messages):
            return _call("menu")
        return _say("Đã thêm hai cà phê sữa, menu đây.")

    engine = _Engine(script)
    binding = NativeAgentRuntime(
        OrchestratorAgent(
            engine,
            "fake",
            tools=[_Tool("add_item", add), _Tool("menu", menu, read_only=True)],
        )
    ).bind(model="fake")

    try:
        with conversation_scope(conv):
            await _interrupt_first(binding, "thêm hai cà phê sữa", add_started)
            started = time.monotonic()
            result = await asyncio.wait_for(
                binding.run("cho xem menu", AgentContext()), 3
            )
        assert menu_calls and menu_calls[0] - started < 1.0
        assert result.content == "Đã thêm hai cà phê sữa, menu đây."
        last_round = engine.calls[-1]
        runtime = next(
            m.content
            for m in last_round
            if m.role == Role.SYSTEM and "<runtime_context>" in m.content
        )
        payload = json.loads(
            runtime.split("<runtime_context>")[1].split("</runtime_context>")[0]
        )
        assert payload["background_actions"] == [
            {
                "tool": "add_item",
                "status": "succeeded",
                "message": "Đã thêm hai cà phê sữa.",
            }
        ]
        assert [e.reported for e in ACTION_LEDGER.entries(conv)] == [True]
    finally:
        ACTION_LEDGER.clear(conv)


@pytest.mark.asyncio
async def test_add_then_pay_waits_and_never_pays_a_stale_cart():
    conv = "scenario-pay"
    state = {"revision": 0}
    order: list[str] = []
    add_started, add_release = threading.Event(), threading.Event()

    def add(**params):
        add_started.set()
        assert add_release.wait(5)
        state["revision"] += 1
        order.append("add")
        return ToolResult(tool_name="add_item", content="ok", success=True)

    def checkout(revision=None, **params):
        if revision != state["revision"]:
            order.append(f"checkout:{revision}:stale")
            return ToolResult(
                tool_name="checkout",
                content="cart revision is stale or empty",
                success=False,
            )
        order.append(f"checkout:{revision}:ok")
        return ToolResult(tool_name="checkout", content="paid", success=True)

    def script(messages):
        if _latest_user(messages) == "thêm hai cà phê sữa":
            return _call("add_item")
        results = _tool_results(messages)
        if not results:
            return _call("checkout", json.dumps({"revision": 0}), call_id="pay-1")
        if "stale" in results[-1]:
            # The model reads the fresh revision from runtime context.
            return _call(
                "checkout",
                json.dumps({"revision": state["revision"]}),
                call_id="pay-2",
            )
        return _say("Đã thanh toán.")

    binding = NativeAgentRuntime(
        OrchestratorAgent(
            _Engine(script),
            "fake",
            tools=[_Tool("add_item", add), _Tool("checkout", checkout)],
        )
    ).bind(model="fake")

    try:
        with conversation_scope(conv):
            await _interrupt_first(binding, "thêm hai cà phê sữa", add_started)
            threading.Timer(0.3, add_release.set).start()
            result = await asyncio.wait_for(
                binding.run("thanh toán luôn", AgentContext()), 5
            )
        assert order == ["add", "checkout:0:stale", "checkout:1:ok"]
        assert result.content == "Đã thanh toán."
        assert all(not r.metadata.get("pending_operation") for r in result.tool_results)
    finally:
        ACTION_LEDGER.clear(conv)


def _background_actions(messages) -> list[dict]:
    runtime = next(
        m.content
        for m in messages
        if m.role == Role.SYSTEM and "<runtime_context>" in m.content
    )
    payload = json.loads(
        runtime.split("<runtime_context>")[1].split("</runtime_context>")[0]
    )
    return payload.get("background_actions", [])


@pytest.mark.asyncio
async def test_add_fails_while_pay_waits_so_nothing_is_paid():
    """Spec D4: a pending write failed, so no payment; the model asks first."""
    conv = "scenario-pay-after-failure"
    order: list[str] = []
    add_started, add_release = threading.Event(), threading.Event()

    def add(**params):
        add_started.set()
        assert add_release.wait(5)
        order.append("add:failed")
        return ToolResult(
            tool_name="add_item", content="merchant refused", success=False
        )

    def checkout(revision=None, **params):
        order.append(f"checkout:{revision}:paid")
        return ToolResult(tool_name="checkout", content="paid", success=True)

    def script(messages):
        if _latest_user(messages) == "thêm hai cà phê sữa":
            return _call("add_item")
        if not _tool_results(messages):
            return _call("checkout", json.dumps({"revision": 0}), call_id="pay-1")
        return _say("Chưa thêm được cà phê sữa, bạn vẫn thanh toán chứ?")

    engine = _Engine(script)
    binding = NativeAgentRuntime(
        OrchestratorAgent(
            engine,
            "fake",
            tools=[_Tool("add_item", add), _Tool("checkout", checkout)],
        )
    ).bind(model="fake")

    try:
        with conversation_scope(conv):
            await _interrupt_first(binding, "thêm hai cà phê sữa", add_started)
            threading.Timer(0.3, add_release.set).start()
            result = await asyncio.wait_for(
                binding.run("thanh toán luôn", AgentContext()), 5
            )
        assert order == ["add:failed"]
        [refused] = result.tool_results
        assert refused.metadata["dispatched"] is False
        assert refused.metadata["earlier_action_unsettled"] is True
        assert _background_actions(engine.calls[-1]) == [
            {"tool": "add_item", "status": "failed", "error": "merchant refused"}
        ]
        assert [(e.tool, e.status) for e in ACTION_LEDGER.entries(conv)] == [
            ("add_item", "failed")
        ]
    finally:
        add_release.set()
        ACTION_LEDGER.clear(conv)


@pytest.mark.asyncio
async def test_a_write_queued_by_a_turn_cut_again_is_dropped_at_once():
    """Barge-in twice: the second turn's queued add never runs or lingers."""
    conv = "scenario-cut-twice"
    coffee_started, coffee_release = threading.Event(), threading.Event()
    tea_calls: list[int] = []

    def add_coffee(**params):
        coffee_started.set()
        assert coffee_release.wait(5)
        return ToolResult(tool_name="add_coffee", content="ok", success=True)

    def add_tea(**params):
        tea_calls.append(1)
        return ToolResult(tool_name="add_tea", content="ok", success=True)

    def script(messages):
        if _tool_results(messages):
            return _say("Dạ.")
        if _latest_user(messages) == "một cà phê":
            return _call("add_coffee")
        return _call("add_tea")

    binding = NativeAgentRuntime(
        OrchestratorAgent(
            _Engine(script),
            "fake",
            tools=[_Tool("add_coffee", add_coffee), _Tool("add_tea", add_tea)],
        )
    ).bind(model="fake")

    def tools():
        return [(e.tool, e.status) for e in ACTION_LEDGER.entries(conv)]

    try:
        with conversation_scope(conv):
            await _interrupt_first(binding, "một cà phê", coffee_started)
            second = asyncio.create_task(binding.run("một trà đào", AgentContext()))
            deadline = time.monotonic() + 2
            while ("add_tea", "running") not in tools():
                assert time.monotonic() < deadline, tools()
                await asyncio.sleep(0.01)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            cut = time.monotonic()
            while len(tools()) > 1 and time.monotonic() - cut < 1:
                await asyncio.sleep(0.01)
            dropped_after = time.monotonic() - cut
            assert tools() == [("add_coffee", "running")]
            assert dropped_after < 0.5
            coffee_release.set()
            deadline = time.monotonic() + 2
            while tools() != [("add_coffee", "succeeded")]:
                assert time.monotonic() < deadline, tools()
                await asyncio.sleep(0.01)
        assert tea_calls == []
    finally:
        coffee_release.set()
        ACTION_LEDGER.clear(conv)
