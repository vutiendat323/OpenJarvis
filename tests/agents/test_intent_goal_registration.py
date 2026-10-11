"""A refused parallel batch still registers the goal the model declared.

Live (deepseek-flash, 2026-10-10 11:54): the model declared the goal on a
parallel [target, prerequisite] batch, which was refused before the declaration
was recorded. Its next call, a read-only prerequisite declared ``NONE``, then
became the turn's goal, so every later attempt at the target was refused
("intent cannot change") and the customer heard an apology for an item that was
never added.
"""

from __future__ import annotations

import json

import pytest

from openjarvis.agents.intent import IntentState
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Role, ToolCall
from tests.agents.test_immediate_response import _tool_round
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import StreamingEngine, _stream_final_round


def _tools() -> tuple[ExistingTool, ExistingTool]:
    target = ExistingTool("add", metadata={"intent_contract": {"name": "ADD"}})
    read = ExistingTool("tables", metadata={"read_only": True})
    return target, read


def _call(name: str, intent: str) -> ToolCall:
    return ToolCall(id=name, name=name, arguments=json.dumps({"_intent": intent}))


def _args(intent: str) -> str:
    return json.dumps({"_intent": intent})


def test_refused_parallel_batch_registers_the_declared_goal():
    state = IntentState(list(_tools()))

    with pytest.raises(ValueError, match="separately after prerequisites"):
        state.prepare([_call("add", "ADD"), _call("tables", "ADD")])

    assert state.declared == "ADD"
    assert state.target_attempted is False
    assert state.reached is False


def test_prerequisite_must_then_carry_the_registered_goal():
    state = IntentState(list(_tools()))
    with pytest.raises(ValueError):
        state.prepare([_call("add", "ADD"), _call("tables", "ADD")])

    with pytest.raises(ValueError, match="cannot change"):
        state.prepare([_call("tables", "NONE")])

    assert [c.name for c in state.prepare([_call("tables", "ADD")])] == ["tables"]
    assert [c.name for c in state.prepare([_call("add", "ADD")])] == ["add"]
    assert state.target_attempted is True


def test_a_batch_refused_for_a_missing_goal_registers_nothing():
    state = IntentState(list(_tools()))
    bad = ToolCall(id="x", name="add", arguments="{}")

    with pytest.raises(ValueError, match="declared canonical intent"):
        state.prepare([bad, _call("tables", "ADD")])

    assert state.declared is None


def _recovering_engine() -> StreamingEngine:
    return StreamingEngine(
        [
            _tool_round("", ("add", _args("ADD")), ("tables", _args("ADD"))),
            _tool_round("", ("tables", _args("NONE"))),
            _tool_round("", ("tables", _args("ADD"))),
            _tool_round("", ("add", _args("ADD"))),
            _stream_final_round("Xong."),
        ]
    )


@pytest.mark.asyncio
async def test_turn_recovers_and_executes_the_target_after_a_none_read():
    target, read = _tools()
    engine = _recovering_engine()
    agent = OrchestratorAgent(
        engine, "fake", tools=[target, read], intent_normalization=True, max_turns=6
    )

    events = [event async for event in agent.run_stream("request")]

    assert len(target.calls) == 1
    assert events[-1].result.metadata["intent_satisfied"] is True
    assert events[-1].result.metadata["normalized_intent"] == "ADD"


@pytest.mark.asyncio
async def test_refusals_name_the_goal_to_keep():
    target, read = _tools()
    engine = _recovering_engine()
    agent = OrchestratorAgent(
        engine, "fake", tools=[target, read], intent_normalization=True, max_turns=6
    )
    _ = [event async for event in agent.run_stream("request")]

    def refusals(call_index: int) -> list[str]:
        messages, _ = engine.calls[call_index]
        return [
            m.content
            for m in messages
            if m.role == Role.TOOL and "intent_dispatch_blocked" in m.content
        ]

    # after round 1 (parallel batch) and after round 2 (a NONE read)
    assert all("_intent=ADD" in text for text in refusals(1))
    assert refusals(1)
    assert all("_intent=ADD" in text for text in refusals(2))
    assert refusals(2)
