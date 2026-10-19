"""A run hands its tool calls and outputs back, so later runs keep them in history.

This is how the OpenAI Responses API expects tool use to be kept: function calls
and their outputs stay in the conversation, appended, instead of a separate
evidence block rebuilt each run.
"""

from __future__ import annotations

import pytest

from openjarvis.agents._stubs import TOOL_TRANSCRIPT, AgentContext
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import Conversation, Role
from openjarvis.tools import evidence
from tests.agents.test_immediate_response import CALC, _tool_round
from tests.agents.test_orchestrator import (
    StreamingEngine,
    _CalculatorStub,
    _stream_final_round,
)


@pytest.mark.asyncio
async def test_run_records_its_tool_calls_and_outputs_without_system_text():
    engine = StreamingEngine([_tool_round("", CALC), _stream_final_round("Bằng 4.")])
    context = AgentContext(conversation=Conversation(), metadata={TOOL_TRANSCRIPT: []})
    agent = OrchestratorAgent(engine, "m", tools=[_CalculatorStub()])

    [event async for event in agent.run_stream("2+2?", context)]

    call, output = context.metadata[TOOL_TRANSCRIPT]
    assert call.role == Role.ASSISTANT and call.tool_calls[0].name == "calculator"
    assert "response_items" not in call.metadata
    assert output.role == Role.TOOL and output.tool_call_id == call.tool_calls[0].id


def test_tool_history_replaces_the_evidence_block():
    evidence.reset()
    agent = OrchestratorAgent(object(), "m", tools=[_CalculatorStub()])
    with conversation_scope("transcript"):
        evidence.record("calculator", '{"value":4}', None, None)
        with_history = agent._build_messages(
            "hi", AgentContext(metadata={TOOL_TRANSCRIPT: []}), system_prompt="P"
        )
        without = agent._build_messages("hi", AgentContext(), system_prompt="P")
    evidence.reset()

    assert not any("Recent tool evidence" in m.text for m in with_history)
    assert any("Recent tool evidence" in m.text for m in without)
