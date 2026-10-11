"""Prerequisites in a mixed batch run now; only the target waits a round.

Live 2026-10-10: nearly every "add item + table" request opened with a parallel
[target, prerequisite] batch that was refused whole, so the prerequisite read
was repeated one model round later (3-6 s each time). The target still must not
run in the same round as its prerequisites, but the prerequisites need not be
thrown away.
"""

from __future__ import annotations

import json

import pytest

from openjarvis.agents.intent import IntentState
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Role, ToolCall
from tests.agents.test_immediate_response import _tool_round
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import StreamingEngine


def _tools() -> tuple[ExistingTool, ExistingTool]:
    target = ExistingTool("add", metadata={"intent_contract": {"name": "ADD"}})
    read = ExistingTool("tables", metadata={"read_only": True})
    return target, read


def _call(name: str, intent: str = "ADD") -> ToolCall:
    return ToolCall(id=name, name=name, arguments=json.dumps({"_intent": intent}))


def _args(intent: str = "ADD") -> str:
    return json.dumps({"_intent": intent})


def test_prerequisites_are_returned_and_the_target_is_deferred():
    state = IntentState(list(_tools()))

    runnable = state.prepare([_call("add"), _call("tables")], defer_targets=True)

    assert [c.name for c in runnable] == ["tables"]
    assert [c.name for c in state.deferred] == ["add"]
    assert state.declared == "ADD"
    assert state.target_attempted is False


def test_nothing_is_deferred_without_the_option():
    state = IntentState(list(_tools()))

    with pytest.raises(ValueError, match="separately after prerequisites"):
        state.prepare([_call("add"), _call("tables")])


def test_a_prerequisite_that_is_not_read_only_still_refuses_the_whole_batch():
    target = ExistingTool("add", metadata={"intent_contract": {"name": "ADD"}})
    state = IntentState([target, ExistingTool("prepare")])

    with pytest.raises(ValueError, match="separately after prerequisites"):
        state.prepare([_call("add"), _call("prepare")], defer_targets=True)
    assert state.deferred == []


def test_a_batch_of_only_targets_is_still_refused():
    target, read = _tools()
    other = ExistingTool("add2", metadata={"intent_contract": {"name": "ADD"}})
    state = IntentState([target, other, read])

    with pytest.raises(ValueError, match="separately after prerequisites"):
        state.prepare([_call("add"), _call("add2")], defer_targets=True)


def test_a_lone_target_runs_normally():
    state = IntentState(list(_tools()))

    assert [c.name for c in state.prepare([_call("add")], defer_targets=True)] == [
        "add"
    ]
    assert state.deferred == []
    assert state.target_attempted is True


@pytest.mark.asyncio
async def test_turn_reads_once_and_adds_in_the_next_round():
    target, read = _tools()
    engine = StreamingEngine(
        [
            _tool_round("", ("add", _args()), ("tables", _args())),
            _tool_round("", ("add", _args())),
        ]
    )
    agent = OrchestratorAgent(
        engine, "fake", tools=[target, read], intent_normalization=True, max_turns=6
    )

    events = [event async for event in agent.run_stream("request")]

    assert len(read.calls) == 1
    assert len(target.calls) == 1
    # round 1 (read + deferred add), round 2 (add): the turn ends there
    assert len(engine.calls) == 2
    assert events[-1].result.metadata["intent_satisfied"] is True

    messages, _ = engine.calls[1]
    deferred = [
        m.content
        for m in messages
        if m.role == Role.TOOL and "intent_target_deferred" in m.content
    ]
    assert len(deferred) == 1
    assert "_intent=ADD" in deferred[0]
    assert "alone" in deferred[0]
