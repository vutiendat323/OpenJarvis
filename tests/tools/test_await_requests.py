"""await_earlier_requests lets a correction wait for requests still in flight."""

from __future__ import annotations

import threading
import time
from unittest import mock

from openjarvis.agents._stubs import _RUN_WORKER_LEASE
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.runs import RUN_TRACKER, voice_run_scope
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
