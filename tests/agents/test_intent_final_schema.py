"""The final answer's format is enforced by the API, not asked for in prose."""

from __future__ import annotations

import json

import pytest

from openjarvis.agents.intent import IntentState
from openjarvis.agents.orchestrator import OrchestratorAgent
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import StreamingEngine, _stream_final_round


def _state() -> IntentState:
    target = ExistingTool(
        "checkout", metadata={"intent_contract": {"name": "CHECKOUT_NOW"}}
    )
    return IntentState([target])


def test_final_schema_is_strict_and_writes_intent_and_state_first():
    schema = _state().final_schema()

    assert list(schema["properties"]) == ["_intent", "state", "message"]
    assert schema["required"] == ["_intent", "state", "message"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["_intent"]["enum"] == ["NONE", "CHECKOUT_NOW"]
    assert set(schema["properties"]["state"]["enum"]) == {
        "ANSWER",
        "NEEDS_INPUT",
        "FAILED",
    }


def test_a_reached_target_may_end_with_an_answer():
    state = _state()
    state.declared, state.name, state.reached = "CHECKOUT_NOW", "CHECKOUT_NOW", True

    assert state.accepts_final("CHECKOUT_NOW", "ANSWER")
    message = '{"_intent":"CHECKOUT_NOW","state":"ANSWER","message":"Đơn đã tạo."}'
    assert state.finish(message) == "Đơn đã tạo."


def test_an_unreached_target_still_cannot_claim_an_answer():
    state = _state()

    assert not state.accepts_final("CHECKOUT_NOW", "ANSWER")
    assert (
        state.finish('{"_intent":"CHECKOUT_NOW","state":"ANSWER","message":"Xong."}')
        is None
    )


_FINAL = json.dumps({"_intent": "NONE", "state": "ANSWER", "message": "Chào bạn."})


class _SchemaEngine(StreamingEngine):
    def supports_final_answer_schema(self, model: str) -> bool:
        return True


async def _run(engine) -> None:
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    agent = OrchestratorAgent(engine, "m", tools=[target], intent_normalization=True)
    [event async for event in agent.run_stream("hi")]


@pytest.mark.asyncio
async def test_orchestrator_asks_a_capable_engine_to_enforce_the_final_format():
    engine = _SchemaEngine([_stream_final_round(_FINAL)])
    await _run(engine)

    schema = engine.calls[0][1]["final_answer_schema"]
    assert schema["properties"]["_intent"]["enum"] == ["NONE", "FINALIZE"]


@pytest.mark.asyncio
async def test_orchestrator_sends_no_schema_to_other_engines():
    engine = StreamingEngine([_stream_final_round(_FINAL)])
    await _run(engine)

    assert "final_answer_schema" not in engine.calls[0][1]
