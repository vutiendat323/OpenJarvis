"""await_earlier_requests lets a correction wait for requests still in flight."""

from __future__ import annotations

import threading
import time
from unittest import mock

from openjarvis.agents._stubs import _RUN_WORKER_LEASE
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.runs import RUN_TRACKER, StableOutcome, voice_run_scope
from openjarvis.core.types import ToolCall
from openjarvis.tools import await_requests
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.await_requests import AwaitEarlierRequestsTool

C = "await-conv"


def _end_later(delay, run_id):
    def run():
        time.sleep(delay)
        RUN_TRACKER.end(C, run_id)

    threading.Thread(target=run, daemon=True).start()


def test_returns_after_earlier_runs_end():
    earlier = RUN_TRACKER.begin(C, "earlier request")
    mine = RUN_TRACKER.begin(C, "correction")
    try:
        _end_later(0.1, earlier)
        with conversation_scope(C), voice_run_scope(mine):
            start = time.monotonic()
            result = AwaitEarlierRequestsTool().execute()
        assert result.success is True
        assert result.content == "earlier_requests_finished"
        assert time.monotonic() - start < 5.0
    finally:
        RUN_TRACKER.end(C, earlier)
        RUN_TRACKER.end(C, mine)


def test_reports_still_running_after_the_wait(monkeypatch):
    monkeypatch.setattr(await_requests, "AWAIT_EARLIER_SECONDS", 0.1)
    earlier = RUN_TRACKER.begin(C, "slow request")
    mine = RUN_TRACKER.begin(C, "correction")
    try:
        with conversation_scope(C), voice_run_scope(mine):
            result = AwaitEarlierRequestsTool().execute()
        assert result.success is False
        assert result.content.startswith("earlier_requests_still_running")
        assert "ask before changing them" in result.content
    finally:
        RUN_TRACKER.end(C, earlier)
        RUN_TRACKER.end(C, mine)


def test_outside_a_voice_run_returns_at_once():
    start = time.monotonic()
    result = AwaitEarlierRequestsTool().execute()
    assert result.success is True
    assert result.content == "no_earlier_requests"
    assert time.monotonic() - start < 1.0


def test_is_read_only_so_it_never_takes_the_write_gate():
    spec = AwaitEarlierRequestsTool().spec
    assert spec.name == "await_earlier_requests"
    assert spec.parameters.get("properties", {}) == {}
    assert spec.metadata == {"read_only": True}
    assert spec.timeout_seconds == 20.0

    lease = mock.MagicMock()
    lease.tool_gate.enter.side_effect = AssertionError("write gate taken")
    token = _RUN_WORKER_LEASE.set(lease)
    try:
        executor = ToolExecutor([AwaitEarlierRequestsTool()])
        result = executor.execute(ToolCall("1", "await_earlier_requests", "{}"))
    finally:
        _RUN_WORKER_LEASE.reset(token)
    assert result.success is True
    assert result.content == "no_earlier_requests"
    lease.tool_gate.enter.assert_not_called()


def _await_as(run_id):
    with conversation_scope(C), voice_run_scope(run_id):
        return AwaitEarlierRequestsTool().execute()


def test_two_corrections_in_a_row_do_not_hold_each_other():
    """I4: the earlier correction never waits for the later one."""
    first = RUN_TRACKER.begin(C, "remove the smoothies")
    second = RUN_TRACKER.begin(C, "and the coffee")
    try:
        start = time.monotonic()
        assert _await_as(first).content == "earlier_requests_finished"
        assert time.monotonic() - start < 0.5
        _end_later(0.1, first)
        assert _await_as(second).content == "earlier_requests_finished"
        assert time.monotonic() - start < 1.0
    finally:
        RUN_TRACKER.end(C, first)
        RUN_TRACKER.end(C, second)


def test_a_correction_does_not_wait_for_a_checkout_waiting_for_the_cart():
    """I4: the waiting checkout waits for the correction, not the reverse."""
    checkout = RUN_TRACKER.begin(C, "pay")
    correction = RUN_TRACKER.begin(C, "no smoothies")
    waiter = threading.Thread(
        target=RUN_TRACKER.wait_stable, args=(C, checkout, 2.0), daemon=True
    )
    try:
        waiter.start()
        time.sleep(0.05)
        start = time.monotonic()
        assert _await_as(correction).content == "earlier_requests_finished"
        assert time.monotonic() - start < 0.5
    finally:
        RUN_TRACKER.end(C, correction)
        waiter.join(3)
        RUN_TRACKER.end(C, checkout)


def test_an_ordering_checkout_is_still_running_until_its_run_ends(monkeypatch):
    """N1: leaving wait_stable does not mean the merchant order has finished."""
    monkeypatch.setattr(await_requests, "AWAIT_EARLIER_SECONDS", 0.05)
    checkout = RUN_TRACKER.begin(C, "pay")
    assert RUN_TRACKER.wait_stable(C, checkout, 0.0) is StableOutcome.STABLE
    assert RUN_TRACKER.freeze(C, checkout)
    correction = RUN_TRACKER.begin(C, "no smoothies")
    try:
        frozen = _await_as(correction)
        assert frozen.success is False
        assert frozen.content.startswith("earlier_requests_still_running:")
        RUN_TRACKER.unfreeze(C, checkout)
        thawed = _await_as(correction)
        assert thawed.success is False
        assert thawed.content.startswith("earlier_requests_still_running:")
        RUN_TRACKER.end(C, checkout)
        finished = _await_as(correction)
        assert finished.success is True
        assert finished.content == "earlier_requests_finished"
    finally:
        RUN_TRACKER.end(C, checkout)
        RUN_TRACKER.end(C, correction)
