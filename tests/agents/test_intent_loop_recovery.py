"""A refused or exhausted turn tells the model why and never speaks a stub."""

from __future__ import annotations

import json

import pytest

from openjarvis.agents._stubs import AgentRunCompleted, AgentTextDelta
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import Role
from tests.agents.test_immediate_response import _tool_round
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import StreamingEngine, _stream_final_round


class ContinuingTarget(ExistingTool):
    """A target that succeeds but asks the agent to keep going."""

    def execute(self, **params):
        result = super().execute(**params)
        result.metadata["continue_agent"] = True
        return result


def _finish(intent: str = "FINALIZE") -> tuple[str, str]:
    return ("finish", json.dumps({"_intent": intent}))


def _target() -> ExistingTool:
    return ContinuingTarget(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}
    )


async def _run(agent: OrchestratorAgent) -> list:
    return [event async for event in agent.run_stream("request")]


def _tool_feedback(engine: StreamingEngine, call: int) -> list[str]:
    messages, _ = engine.calls[call]
    return [m.content for m in messages if m.role == Role.TOOL]


@pytest.mark.asyncio
async def test_blocked_batch_tells_the_model_to_call_the_target_alone():
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    lookup = ExistingTool("lookup")
    engine = StreamingEngine(
        [
            _tool_round("", _finish(), ("lookup", json.dumps({"_intent": "FINALIZE"}))),
            _stream_final_round(
                json.dumps(
                    {"_intent": "FINALIZE", "state": "NEEDS_INPUT", "message": "?"}
                )
            ),
        ]
    )
    await _run(
        OrchestratorAgent(
            engine, "fake", tools=[target, lookup], intent_normalization=True
        )
    )

    feedback = _tool_feedback(engine, 1)
    assert feedback and all(
        "execute the intent target separately after prerequisites" in text
        for text in feedback
    )
    assert target.calls == [] and lookup.calls == []


@pytest.mark.asyncio
async def test_blocked_call_after_the_goal_is_reached_says_to_answer_now():
    engine = StreamingEngine(
        [
            _tool_round("", _finish()),
            _tool_round("", _finish()),
            _stream_final_round("Đã xong."),
        ]
    )
    await _run(
        OrchestratorAgent(engine, "fake", tools=[_target()], intent_normalization=True)
    )

    refused = _tool_feedback(engine, 2)[-1]
    assert "requested outcome already completed" in refused
    assert "do not call" in refused.lower()


@pytest.mark.asyncio
async def test_exhausted_turn_speaks_the_reached_target_message_not_a_stub():
    engine = StreamingEngine([_tool_round("", _finish()) for _ in range(3)])
    events = await _run(
        OrchestratorAgent(
            engine,
            "fake",
            tools=[_target()],
            intent_normalization=True,
            max_turns=3,
        )
    )

    spoken = [e.content for e in events if isinstance(e, AgentTextDelta)]
    assert spoken == ["verified result"]
    completed = events[-1]
    assert isinstance(completed, AgentRunCompleted)
    assert completed.result.content == "verified result"
    assert completed.result.metadata["max_turns_exceeded"] is True


@pytest.mark.asyncio
async def test_exhausted_turn_without_a_reached_target_stays_silent():
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    # No _intent: every call is refused before dispatch, the goal never lands.
    engine = StreamingEngine([_tool_round("", ("finish", "{}")) for _ in range(2)])
    events = await _run(
        OrchestratorAgent(
            engine, "fake", tools=[target], intent_normalization=True, max_turns=2
        )
    )

    assert not [e for e in events if isinstance(e, AgentTextDelta)]
    assert events[-1].result.content == ""
    assert events[-1].result.metadata["max_turns_exceeded"] is True
