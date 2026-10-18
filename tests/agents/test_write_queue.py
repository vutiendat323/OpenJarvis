"""Reads never wait for another run; writes queue behind it, briefly and in order.

Live 2026-10-10: while an interrupted turn's cart write was still running, the
next turn's every tool call, reads included, came back "a previous turn's tool
operation is still running" and the customer's new request stalled.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from openjarvis.agents._stubs import (
    _RUN_WORKER_LEASE,
    AgentToolGate,
    AgentWorkerLease,
    committed_dispatch,
)
from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.core.conversation import (
    agent_turn_scope,
    conversation_scope,
    current_turn_nonce,
)
from openjarvis.core.events import EventBus
from openjarvis.core.runs import RUN_TRACKER, StableOutcome, voice_run_scope
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.loader import load_skill
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.tools import _stubs as tool_stubs
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec


class _Tool(BaseTool):
    def __init__(
        self,
        name,
        *,
        read_only=False,
        block=None,
        metadata=None,
        timeout=30.0,
        inner=None,
        sleep=0.0,
    ):
        self.tool_id = name
        self._name = name
        self._metadata = {"read_only": True} if read_only else dict(metadata or {})
        self._block = block
        self._timeout = timeout
        self._inner = inner
        self._sleep = sleep
        self.calls = 0

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
            metadata=self._metadata,
            timeout_seconds=self._timeout,
        )

    def execute(self, **params):
        del params
        self.calls += 1
        if self._inner is not None:
            nested = self._inner()
            if not nested.success:
                return nested
        if self._block is not None:
            assert self._block.wait(5)
        if self._sleep:
            time.sleep(self._sleep)
        return ToolResult(tool_name=self._name, content="ok", success=True)


class _ThenNested(BaseTool):
    """Waits for ``release``, then runs a nested step through an executor."""

    def __init__(self, name, started, release, nested):
        self.tool_id = name
        self._name = name
        self._started = started
        self._release = release
        self._nested = nested

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description="test tool",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params):
        del params
        self._started.set()
        assert self._release.wait(5)
        nested = self._nested()
        return ToolResult(tool_name=self._name, content="ok", success=nested.success)


class _Guarded(BaseTool):
    """A tool with the fields the pre-dispatch checks look at."""

    def __init__(self, *, local=True, capabilities=(), confirm=False):
        self.tool_id = "guarded"
        self.is_local = local
        self._capabilities = list(capabilities)
        self._confirm = confirm
        self.calls = 0

    @property
    def spec(self):
        return ToolSpec(
            name="guarded",
            description="test tool",
            parameters={"type": "object", "properties": {}},
            required_capabilities=self._capabilities,
            requires_confirmation=self._confirm,
        )

    def execute(self, **params):
        del params
        self.calls += 1
        return ToolResult(tool_name="guarded", content="ok", success=True)


class _Guard:
    def __init__(self, replacement=None):
        self._replacement = replacement

    def check_outbound(self, tool_call):
        if self._replacement is None:
            raise ValueError("blocked")
        return self._replacement


class _Deny:
    def check(self, agent_id, capability, tool):
        del agent_id, capability, tool
        return False


def _as(lease, fn):
    def runner():
        _RUN_WORKER_LEASE.set(lease)
        return fn()

    return contextvars.copy_context().run(runner)


def _until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


# --- AgentToolGate ---------------------------------------------------------


def test_same_owner_reenters_without_waiting():
    gate, owner = AgentToolGate(), object()
    with gate.enter(owner, wait_seconds=0.1) as outer:
        with gate.enter(owner, wait_seconds=0.1) as inner:
            assert outer and inner


def test_other_owner_without_a_wait_is_refused_at_once():
    gate, first, second = AgentToolGate(), object(), object()
    with gate.enter(first):
        started = time.monotonic()
        with gate.enter(second) as acquired:
            assert not acquired
        assert time.monotonic() - started < 0.05


def test_waiting_owner_runs_after_the_holder_releases():
    gate, first, second = AgentToolGate(), object(), object()
    entered, release, order = threading.Event(), threading.Event(), []

    def hold():
        with gate.enter(first):
            entered.set()
            release.wait(2)
            order.append("first")

    def wait_then_run():
        with gate.enter(second, wait_seconds=2) as acquired:
            order.append(("second", acquired))

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(hold)
        assert entered.wait(1)
        waiting = pool.submit(wait_then_run)
        time.sleep(0.05)
        release.set()
        held.result(2)
        waiting.result(2)

    assert order == ["first", ("second", True)]


def test_the_wait_gives_up_after_its_bound():
    gate, first, second = AgentToolGate(), object(), object()
    with gate.enter(first):
        started = time.monotonic()
        with gate.enter(second, wait_seconds=0.1) as acquired:
            assert not acquired
        assert 0.09 <= time.monotonic() - started < 0.5


def test_waiters_are_served_in_arrival_order():
    gate, holder, a, b = AgentToolGate(), object(), object(), object()
    entered, release, order = threading.Event(), threading.Event(), []

    def hold():
        with gate.enter(holder):
            entered.set()
            release.wait(2)

    def wait(owner, name):
        with gate.enter(owner, wait_seconds=2) as acquired:
            assert acquired
            order.append(name)

    with ThreadPoolExecutor(3) as pool:
        held = pool.submit(hold)
        assert entered.wait(1)
        first = pool.submit(wait, a, "a")
        time.sleep(0.05)
        second = pool.submit(wait, b, "b")
        time.sleep(0.05)
        release.set()
        for future in (held, first, second):
            future.result(2)

    assert order == ["a", "b"]


def test_a_cancelled_waiter_stops_waiting_once_woken():
    gate, holder, waiter = AgentToolGate(), object(), object()
    cancelled = threading.Event()

    def wait():
        started = time.monotonic()
        with gate.enter(waiter, wait_seconds=5, cancelled=cancelled.is_set) as acquired:
            return acquired, time.monotonic() - started

    with gate.enter(holder), ThreadPoolExecutor(1) as pool:
        waiting = pool.submit(wait)
        time.sleep(0.05)
        cancelled.set()
        gate.wake()
        acquired, waited = waiting.result(1)

    assert acquired is False and waited < 0.5
    with gate.enter(object()) as after:
        assert after  # the cancelled waiter left the queue


# --- ToolExecutor ----------------------------------------------------------


def _leases():
    gate = AgentToolGate()
    return AgentWorkerLease(gate), AgentWorkerLease(gate)


def test_a_read_of_another_run_is_not_blocked_by_a_running_write():
    old, new = _leases()
    release = threading.Event()
    write, read = _Tool("write", block=release), _Tool("read", read_only=True)
    executor = ToolExecutor([write, read])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "write", "{}"))
        )
        _until(lambda: write.calls == 1)
        result = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "read", "{}"))
        ).result(1)
        assert result.success and read.calls == 1
        release.set()
        held.result(2)


def test_a_write_of_another_run_waits_then_runs(monkeypatch):
    monkeypatch.setattr(tool_stubs, "WRITE_WAIT_SECONDS", 2.0)
    old, new = _leases()
    release = threading.Event()
    first, second = _Tool("first", block=release), _Tool("second")
    executor = ToolExecutor([first, second])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "first", "{}"))
        )
        _until(lambda: first.calls == 1)
        queued = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "second", "{}"))
        )
        time.sleep(0.1)
        assert second.calls == 0
        release.set()
        held.result(2)
        result = queued.result(2)

    assert result.success and second.calls == 1


def test_a_write_wait_that_times_out_is_not_dispatched(monkeypatch):
    monkeypatch.setattr(tool_stubs, "WRITE_WAIT_SECONDS", 0.05)
    old, new = _leases()
    release = threading.Event()
    first, second = _Tool("first", block=release), _Tool("second")
    executor = ToolExecutor([first, second])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "first", "{}"))
        )
        _until(lambda: first.calls == 1)
        result = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "second", "{}"))
        ).result(2)
        release.set()
        held.result(2)

    assert result.metadata["pending_operation"] is True
    assert result.metadata["dispatched"] is False
    assert second.calls == 0


def test_nested_write_inside_a_read_waits_for_the_gate(monkeypatch):
    monkeypatch.setattr(tool_stubs, "WRITE_WAIT_SECONDS", 2.0)
    old, new = _leases()
    release = threading.Event()
    holder = _Tool("holder", block=release)
    display = _Tool("display")
    nested_executor = ToolExecutor([display])
    menu = _Tool(
        "menu",
        read_only=True,
        inner=lambda: nested_executor.execute(ToolCall("n", "display", "{}")),
    )
    executor = ToolExecutor([holder, menu])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "holder", "{}"))
        )
        _until(lambda: holder.calls == 1)
        reading = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "menu", "{}"))
        )
        _until(lambda: menu.calls == 1)
        time.sleep(0.05)
        assert display.calls == 0
        release.set()
        held.result(2)
        result = reading.result(2)

    assert result.success and display.calls == 1


def _earlier_failure(conversation):
    ACTION_LEDGER.start(conversation, call_id="old", tool="add", run_id="old-run")
    ACTION_LEDGER.finish(
        conversation,
        call_id="old",
        run_id="old-run",
        result=ToolResult(tool_name="add", content="refused", success=False),
    )


def test_a_write_after_an_unseen_earlier_failure_is_not_dispatched():
    """Spec 2026-10-10 D4: never act past a failed earlier write unseen."""
    conversation = "wq-unsettled"
    lease, _ = _leases()
    pay = _Tool("pay")
    display = _Tool("display")
    nested_executor = ToolExecutor([display])
    menu = _Tool(
        "menu",
        read_only=True,
        inner=lambda: nested_executor.execute(ToolCall("n", "display", "{}")),
    )
    executor = ToolExecutor([pay, menu])
    _earlier_failure(conversation)

    def turn():
        with conversation_scope(conversation), agent_turn_scope():
            refused = executor.execute(ToolCall("1", "pay", "{}"))
            read = executor.execute(ToolCall("2", "menu", "{}"))
            ACTION_LEDGER.background_for(conversation, current_turn_nonce())
            after_seen = executor.execute(ToolCall("3", "pay", "{}"))
        return refused, read, after_seen

    try:
        refused, read, after_seen = _as(lease, turn)
    finally:
        ACTION_LEDGER.clear(conversation)

    assert refused.success is False
    assert refused.metadata == {"dispatched": False, "earlier_action_unsettled": True}
    assert "background_actions" in refused.content
    # Reads and a dispatched tool's own steps are not held back.
    assert read.success and display.calls == 1
    # The model has now seen the failure in background_actions.
    assert after_seen.success and pay.calls == 1


def test_an_earlier_failure_does_not_hold_writes_outside_a_run_or_conversation():
    conversation = "wq-unsettled-scope"
    lease, _ = _leases()
    pay = _Tool("pay")
    executor = ToolExecutor([pay])
    _earlier_failure(conversation)
    try:
        with conversation_scope(conversation), agent_turn_scope():
            no_lease = executor.execute(ToolCall("1", "pay", "{}"))
        no_conversation = _as(
            lease, lambda: executor.execute(ToolCall("2", "pay", "{}"))
        )
    finally:
        ACTION_LEDGER.clear(conversation)

    assert no_lease.success and no_conversation.success and pay.calls == 2


def test_a_queued_write_of_a_cancelled_run_stops_waiting_at_once():
    """Barge-in while a write is queued: it leaves the queue, not after 5 s."""
    old, new = _leases()
    release = threading.Event()
    first, second = _Tool("first", block=release), _Tool("second")
    executor = ToolExecutor([first, second])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "first", "{}"))
        )
        _until(lambda: first.calls == 1)
        queued = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "second", "{}"))
        )
        time.sleep(0.05)
        cut = time.monotonic()
        new._signal_cancelled()
        try:
            result = queued.result(2)
            waited = time.monotonic() - cut
        finally:
            release.set()
        held.result(2)

    assert result.metadata == {"pending_operation": True, "dispatched": False}
    assert waited < 0.5 < tool_stubs.WRITE_WAIT_SECONDS
    assert second.calls == 0


def test_a_nested_step_of_a_dispatched_tool_keeps_waiting_after_cancellation(
    monkeypatch,
):
    """Spec D2: a tool already dispatched finishes its steps."""
    monkeypatch.setattr(tool_stubs, "WRITE_WAIT_SECONDS", 2.0)
    old, new = _leases()
    release = threading.Event()
    holder = _Tool("holder", block=release)
    display = _Tool("display")
    nested_executor = ToolExecutor([display])
    menu = _Tool(
        "menu",
        read_only=True,
        inner=lambda: nested_executor.execute(ToolCall("n", "display", "{}")),
    )
    executor = ToolExecutor([holder, menu])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "holder", "{}"))
        )
        _until(lambda: holder.calls == 1)
        reading = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "menu", "{}"))
        )
        _until(lambda: menu.calls == 1)
        time.sleep(0.05)
        new._signal_cancelled()
        time.sleep(0.1)
        still_waiting = not reading.done() and display.calls == 0
        release.set()
        held.result(2)
        result = reading.result(2)

    assert still_waiting
    assert result.success and display.calls == 1


def test_a_dispatched_tool_runs_its_nested_steps_after_the_turn_is_cancelled():
    """Spec 2026-10-10 D2: a dispatched action finishes, every step of it."""
    lease, _ = _leases()
    started, release = threading.Event(), threading.Event()
    step = _Tool("step")
    outer = _ThenNested(
        "outer",
        started,
        release,
        lambda: executor.execute(ToolCall("n", "step", "{}")),
    )
    executor = ToolExecutor([outer, step])

    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(
            _as, lease, lambda: executor.execute(ToolCall("1", "outer", "{}"))
        )
        assert started.wait(1)
        lease._signal_cancelled()
        release.set()
        result = running.result(2)

    assert result.success and step.calls == 1
    # Only a call made from inside a dispatched tool is exempt.
    with pytest.raises(asyncio.CancelledError):
        _as(lease, lambda: executor.execute(ToolCall("2", "step", "{}")))
    assert step.calls == 1


class _Cancels(BaseTool):
    """Raises ``asyncio.CancelledError`` from inside ``execute``."""

    def __init__(self):
        self.tool_id = "cancels"
        self.calls = 0

    @property
    def spec(self):
        return ToolSpec(
            name="cancels",
            description="test tool",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params):
        del params
        self.calls += 1
        raise asyncio.CancelledError("agent_turn_interrupted")


def test_a_cancellation_after_the_tool_started_is_an_unknown_outcome():
    lease, _ = _leases()
    tool = _Cancels()
    executor = ToolExecutor([tool])

    result = _as(lease, lambda: executor.execute(ToolCall("1", "cancels", "{}")))

    assert tool.calls == 1
    assert result.success is False
    assert result.metadata["outcome_unknown"] is True
    assert "do not re-execute" in result.content


def test_a_cancellation_before_dispatch_still_raises():
    lease, _ = _leases()
    tool = _Cancels()
    executor = ToolExecutor([tool])
    lease._signal_cancelled()

    with pytest.raises(asyncio.CancelledError):
        _as(lease, lambda: executor.execute(ToolCall("1", "cancels", "{}")))

    assert tool.calls == 0


_REFUSALS = {
    "unknown tool": (lambda: ToolExecutor([]), "{}", "ghost"),
    "invalid json": (lambda: ToolExecutor([_Guarded()]), "{not json", "guarded"),
    "non-object arguments": (lambda: ToolExecutor([_Guarded()]), "[1]", "guarded"),
    "security block": (
        lambda: ToolExecutor([_Guarded(local=False)], boundary_guard=_Guard()),
        "{}",
        "guarded",
    ),
    "non-object after redaction": (
        lambda: ToolExecutor(
            [_Guarded(local=False)],
            boundary_guard=_Guard(ToolCall("1", "guarded", "[1]")),
        ),
        "{}",
        "guarded",
    ),
    "capability denied": (
        lambda: ToolExecutor([_Guarded(capabilities=["x"])], capability_policy=_Deny()),
        "{}",
        "guarded",
    ),
    "confirmation unavailable": (
        lambda: ToolExecutor([_Guarded(confirm=True)]),
        "{}",
        "guarded",
    ),
    "confirmation denied": (
        lambda: ToolExecutor(
            [_Guarded(confirm=True)],
            interactive=True,
            confirm_callback=lambda prompt: False,
        ),
        "{}",
        "guarded",
    ),
}


@pytest.mark.parametrize("case", list(_REFUSALS))
def test_a_call_refused_before_dispatch_says_it_was_not_dispatched(case):
    """Spec 2026-10-10: a write refused before dispatch is not a write."""
    build, arguments, name = _REFUSALS[case]
    result = build().execute(ToolCall("1", name, arguments))

    assert result.success is False
    assert result.metadata["dispatched"] is False


def test_a_mutation_that_times_out_is_flagged_outcome_unknown():
    slow = _Tool("slow", metadata={"mutates": True}, timeout=0.05, sleep=0.3)
    result = ToolExecutor([slow]).execute(ToolCall("1", "slow", "{}"))

    assert result.success is False
    assert result.metadata.get("outcome_unknown") is True


def test_a_plain_write_that_times_out_is_flagged_outcome_unknown():
    slow = _Tool("slow", timeout=0.05, sleep=0.3)
    result = ToolExecutor([slow]).execute(ToolCall("1", "slow", "{}"))

    assert result.success is False
    assert result.metadata.get("outcome_unknown") is True
    assert "may already have been dispatched" not in result.content


def test_a_read_that_times_out_is_not_flagged():
    slow = _Tool("slow", read_only=True, timeout=0.05, sleep=0.3)
    result = ToolExecutor([slow]).execute(ToolCall("1", "slow", "{}"))

    assert result.success is False
    assert "outcome_unknown" not in result.metadata


def test_the_menu_skill_is_declared_read_only():
    tool = SkillTool(
        load_skill(Path("skills/trendcoffee-menu.toml")),
        SkillExecutor(ToolExecutor([], EventBus())),
    )
    assert tool.spec.metadata.get("read_only") is True


# --- committed dispatch (spec 2026-10-17 barge-in §5.2, §5.4) ---------------


def test_a_committed_call_runs_although_its_lease_was_cancelled():
    lease, _ = _leases()
    tool = _Tool("first")
    executor = ToolExecutor([tool])
    lease._signal_cancelled()

    def run():
        with committed_dispatch():
            return executor.execute(ToolCall("1", "first", "{}"))

    result = _as(lease, run)
    assert result.success and tool.calls == 1


def test_a_queued_committed_write_keeps_waiting_after_cancellation():
    old, new = _leases()
    release = threading.Event()
    first, second = _Tool("first", block=release), _Tool("second")
    executor = ToolExecutor([first, second])

    def queued_run():
        with committed_dispatch():
            return executor.execute(ToolCall("2", "second", "{}"))

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "first", "{}"))
        )
        _until(lambda: first.calls == 1)
        queued = pool.submit(_as, new, queued_run)
        time.sleep(0.05)
        new._signal_cancelled()
        time.sleep(0.1)
        release.set()
        result = queued.result(2)
        held.result(2)
    assert result.success and second.calls == 1


# --- same-session queue, freeze, write notes (spec 2026-10-18 §5) ----------


def _in_run(lease, conversation, run, fn):
    def scoped():
        with conversation_scope(conversation), voice_run_scope(run):
            return fn()

    return _as(lease, scoped)


def test_a_write_of_the_same_session_waits_past_the_five_second_bound(monkeypatch):
    monkeypatch.setattr(tool_stubs, "WRITE_WAIT_SECONDS", 0.05)
    gate = AgentToolGate()
    old = AgentWorkerLease(gate, conversation_id="s")
    new = AgentWorkerLease(gate, conversation_id="s")
    first, second = _Tool("first", sleep=0.3), _Tool("second", timeout=2.0)
    executor = ToolExecutor([first, second])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "first", "{}"))
        )
        _until(lambda: first.calls == 1)
        result = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "second", "{}"))
        ).result(3)
        held.result(2)

    assert result.success and second.calls == 1
    assert not result.metadata.get("pending_operation")


def test_a_write_behind_another_session_still_gives_up_after_the_bound(monkeypatch):
    monkeypatch.setattr(tool_stubs, "WRITE_WAIT_SECONDS", 0.05)
    gate = AgentToolGate()
    old = AgentWorkerLease(gate, conversation_id="s")
    new = AgentWorkerLease(gate, conversation_id="t")
    first, second = _Tool("first", sleep=0.3), _Tool("second", timeout=2.0)
    executor = ToolExecutor([first, second])

    with ThreadPoolExecutor(2) as pool:
        held = pool.submit(
            _as, old, lambda: executor.execute(ToolCall("1", "first", "{}"))
        )
        _until(lambda: first.calls == 1)
        result = pool.submit(
            _as, new, lambda: executor.execute(ToolCall("2", "second", "{}"))
        ).result(3)
        held.result(2)

    assert result.metadata.get("pending_operation") is True
    assert second.calls == 0


def test_a_frozen_conversation_holds_other_runs_writes():
    conversation = "frozen-conv"
    gate = AgentToolGate()
    lease_a = AgentWorkerLease(gate, conversation_id=conversation)
    lease_b = AgentWorkerLease(gate, conversation_id=conversation)
    tool = _Tool("write", timeout=3.0)
    executor = ToolExecutor([tool])
    assert RUN_TRACKER.freeze(conversation, "A")
    try:
        with ThreadPoolExecutor(2) as pool:
            blocked = pool.submit(
                _in_run,
                lease_b,
                conversation,
                "B",
                lambda: executor.execute(ToolCall("1", "write", "{}")),
            )
            time.sleep(0.15)
            assert tool.calls == 0 and not blocked.done()

            own = _in_run(
                lease_a,
                conversation,
                "A",
                lambda: executor.execute(ToolCall("2", "write", "{}")),
            )
            assert own.success and tool.calls == 1

            RUN_TRACKER.unfreeze(conversation, "A")
            result = blocked.result(2)
        assert result.success and tool.calls == 2
    finally:
        RUN_TRACKER.close(conversation)


def test_a_write_behind_a_freeze_that_outlasts_its_timeout_is_pending():
    conversation = "frozen-timeout-conv"
    lease = AgentWorkerLease(AgentToolGate(), conversation_id=conversation)
    tool = _Tool("write", timeout=0.1)
    executor = ToolExecutor([tool])
    assert RUN_TRACKER.freeze(conversation, "A")
    try:
        result = _in_run(
            lease,
            conversation,
            "B",
            lambda: executor.execute(ToolCall("1", "write", "{}")),
        )
    finally:
        RUN_TRACKER.close(conversation)
    assert result.metadata == {"pending_operation": True, "dispatched": False}
    assert tool.calls == 0


def test_a_write_is_noted_for_waiting_checkouts():
    conversation = "noted-conv"
    lease = AgentWorkerLease(AgentToolGate(), conversation_id=conversation)
    tool = _Tool("write", timeout=2.0)
    executor = ToolExecutor([tool])
    run_a = RUN_TRACKER.begin(conversation, "checkout")
    RUN_TRACKER.begin(conversation, "still running")  # keeps A waiting
    # "B-unseen" is not in A's snapshot, so its write is a change A must see.
    try:
        with ThreadPoolExecutor(1) as pool:
            waiting = pool.submit(RUN_TRACKER.wait_stable, conversation, run_a, 3.0)
            time.sleep(0.1)
            result = _in_run(
                lease,
                conversation,
                "B-unseen",
                lambda: executor.execute(ToolCall("1", "write", "{}")),
            )
            assert result.success
            assert waiting.result(3) is StableOutcome.CHANGED
    finally:
        RUN_TRACKER.close(conversation)


def test_a_read_is_neither_frozen_nor_noted():
    conversation = "read-conv"
    lease = AgentWorkerLease(AgentToolGate(), conversation_id=conversation)
    tool = _Tool("read", read_only=True)
    executor = ToolExecutor([tool])
    assert RUN_TRACKER.freeze(conversation, "A")
    try:
        result = _in_run(
            lease,
            conversation,
            "B",
            lambda: executor.execute(ToolCall("1", "read", "{}")),
        )
    finally:
        RUN_TRACKER.close(conversation)
    assert result.success and tool.calls == 1


def test_a_write_queued_in_the_gate_does_not_run_after_a_freeze_taken_meanwhile():
    conversation = "frozen-in-gate-conv"
    gate = AgentToolGate()
    holder, lease_c, lease_a = (
        AgentWorkerLease(gate, conversation_id=conversation) for _ in range(3)
    )
    release = threading.Event()
    held = _Tool("held", block=release, timeout=5.0)
    queued = _Tool("queued", timeout=5.0)
    own = _Tool("own", timeout=5.0)
    executor = ToolExecutor([held, queued, own])
    try:
        with ThreadPoolExecutor(3) as pool:
            running = pool.submit(
                _in_run,
                holder,
                conversation,
                "H",
                lambda: executor.execute(ToolCall("1", "held", "{}")),
            )
            _until(lambda: held.calls == 1)
            waiting = pool.submit(
                _in_run,
                lease_c,
                conversation,
                "C",
                lambda: executor.execute(ToolCall("2", "queued", "{}")),
            )
            time.sleep(0.15)  # C is past the freeze check and queued in the gate
            assert RUN_TRACKER.freeze(conversation, "A")
            release.set()
            running.result(2)
            time.sleep(0.2)
            assert queued.calls == 0 and not waiting.done()

            mine = _in_run(
                lease_a,
                conversation,
                "A",
                lambda: executor.execute(ToolCall("3", "own", "{}")),
            )
            assert mine.success and own.calls == 1
            assert queued.calls == 0

            RUN_TRACKER.unfreeze(conversation, "A")
            result = waiting.result(2)
        assert result.success and queued.calls == 1
    finally:
        RUN_TRACKER.close(conversation)


def test_a_nested_step_of_a_dispatched_skill_ignores_a_freeze_taken_meanwhile():
    conversation = "frozen-nested-conv"
    gate = AgentToolGate()
    lease_c, lease_a = (
        AgentWorkerLease(gate, conversation_id=conversation) for _ in range(2)
    )
    started, release = threading.Event(), threading.Event()
    step = _Tool("step", timeout=0.5)
    own = _Tool("own", timeout=5.0)
    skill = _ThenNested(
        "skill",
        started,
        release,
        lambda: executor.execute(ToolCall("n", "step", "{}")),
    )
    executor = ToolExecutor([skill, step, own])
    try:
        with ThreadPoolExecutor(2) as pool:
            running = pool.submit(
                _in_run,
                lease_c,
                conversation,
                "C",
                lambda: executor.execute(ToolCall("1", "skill", "{}")),
            )
            assert started.wait(2)  # C passed the freeze check and holds the gate
            assert RUN_TRACKER.freeze(conversation, "A")
            begun = time.monotonic()
            release.set()
            result = running.result(3)

        # The nested write step finished at once, not after its 0.5 s timeout.
        assert result.success and step.calls == 1
        assert time.monotonic() - begun < 0.4
        mine = _in_run(
            lease_a,
            conversation,
            "A",
            lambda: executor.execute(ToolCall("2", "own", "{}")),
        )
        assert mine.success and own.calls == 1
    finally:
        RUN_TRACKER.close(conversation)
