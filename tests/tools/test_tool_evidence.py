"""Successful tool output is recorded per conversation, whoever dispatched it."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import evidence
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec


@pytest.fixture(autouse=True)
def _clean_store():
    """The store is module-level, so one test must not seed the next."""
    evidence.reset()
    yield
    evidence.reset()


class _Echo(BaseTool):
    """Returns whatever content and metadata the call asks for."""

    tool_id = "echo"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_request",
            description="test double",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(
            tool_name="http_request",
            content=params.get("body", ""),
            success=params.get("ok", True),
            metadata={"status_code": params.get("status", 200)},
        )


def _call(**arguments: Any) -> ToolCall:
    return ToolCall(id="1", name="http_request", arguments=json.dumps(arguments))


def test_a_successful_result_becomes_evidence():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="QR_REAL", url="https://trendcoffee.net/x"))

        assert evidence.observed_in_tool_output("QR_REAL") is True
        assert evidence.observed_in_tool_output("QR_FAKE") is False


def test_every_executor_writes_the_same_conversation_store():
    """The defect this module exists to prevent.

    A saved skill dispatches its steps through the builder's executor while the
    agent calls display tools through its own. Two instance-owned buffers would
    mean a skill could fetch a real payment QR that the guard cannot see.
    """
    agent_executor = ToolExecutor([_Echo()])
    skill_executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        skill_executor.execute(
            _call(body="QR_FROM_SKILL", url="https://trendcoffee.net/x")
        )

        assert evidence.observed_in_tool_output("QR_FROM_SKILL") is True
        assert agent_executor.__class__ is skill_executor.__class__


def test_evidence_does_not_cross_conversations():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="QR_A", url="https://trendcoffee.net/x"))
    with conversation_scope("b"):
        assert evidence.observed_in_tool_output("QR_A") is False


def test_a_failed_call_leaves_no_evidence():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="QR_REAL", ok=False))

        assert evidence.observed_in_tool_output("QR_REAL") is False


def test_evidence_can_require_a_2xx_status_and_a_trusted_host():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(
            _call(body="QR_500", status=500, url="https://trendcoffee.net/x")
        )
        executor.execute(
            _call(body="QR_ELSEWHERE", status=200, url="https://evil.example/x")
        )
        executor.execute(
            _call(body="QR_GOOD", status=201, url="https://trendcoffee.net/x")
        )

        hosts = ("trendcoffee.net",)
        assert not evidence.observed_in_tool_output(
            "QR_500", require_ok=True, trusted_hosts=hosts
        )
        assert not evidence.observed_in_tool_output(
            "QR_ELSEWHERE", require_ok=True, trusted_hosts=hosts
        )
        assert evidence.observed_in_tool_output(
            "QR_GOOD", require_ok=True, trusted_hosts=hosts
        )


def test_evidence_is_bounded_by_entry_count(monkeypatch):
    monkeypatch.setattr(evidence, "_MAX_ENTRIES", 2)
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        for marker in ("one", "two", "three"):
            executor.execute(_call(body=marker))

        assert evidence.observed_in_tool_output("one") is False
        assert evidence.observed_in_tool_output("three") is True


def test_old_conversations_are_evicted_whole(monkeypatch):
    """Otherwise every chat request leaves a bucket behind forever."""
    monkeypatch.setattr(evidence, "_MAX_CONVERSATIONS", 2)
    executor = ToolExecutor([_Echo()])

    for name in ("first", "second", "third"):
        with conversation_scope(name):
            executor.execute(_call(body=f"body-{name}"))

    with conversation_scope("first"):
        assert evidence.observed_in_tool_output("body-first") is False
    with conversation_scope("third"):
        assert evidence.observed_in_tool_output("body-third") is True


def test_last_result_returns_the_most_recent_matching_body():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="older"))
        executor.execute(_call(body="newer"))

        assert evidence.last_result("http_request") == "newer"
        assert evidence.last_result("nothing_called_this") == ""
