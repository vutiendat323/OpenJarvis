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
    assert output.role == Role.TOOL and output.tool_call_id == call.tool_calls[0].id


@pytest.mark.asyncio
async def test_run_records_the_reasoning_items_of_its_tool_round():
    """Dropping them changed the history the next run sends, so the prompt cache
    matched only up to the first tool call of the earlier run."""
    from openjarvis.engine._stubs import StreamChunk

    items = [
        {"id": "rs_1", "type": "reasoning", "encrypted_content": "opaque"},
        {
            "type": "function_call",
            "call_id": "call-calculator-0",
            "name": "calculator",
            "arguments": CALC[1],
        },
    ]
    round_one = _tool_round("", CALC)
    round_one[-1] = StreamChunk(finish_reason="tool_calls", response_items=items)
    engine = StreamingEngine([round_one, _stream_final_round("Bằng 4.")])
    context = AgentContext(conversation=Conversation(), metadata={TOOL_TRANSCRIPT: []})
    agent = OrchestratorAgent(engine, "m", tools=[_CalculatorStub()])

    [event async for event in agent.run_stream("2+2?", context)]

    call, _ = context.metadata[TOOL_TRANSCRIPT]
    assert call.metadata["response_items"] == items


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


@pytest.mark.asyncio
async def test_runtime_context_goes_last_so_the_history_prefix_stays_cacheable():
    """OpenAI writes the cache at the end of the latest user message or tool
    output. Runtime state placed before them made every later run diverge
    right after the fixed prompt; placed last, the history before it matches."""

    class _CartTool(_CalculatorStub):
        def agent_context(self):
            return {"draft_cart": {"lines": []}}

    class _Snapshots(StreamingEngine):
        def stream_full(self, messages, **kwargs):
            self.seen = [*getattr(self, "seen", []), list(messages)]
            return super().stream_full(messages, **kwargs)

    engine = _Snapshots([_tool_round("", CALC), _stream_final_round("Bằng 4.")])
    agent = OrchestratorAgent(engine, "m", tools=[_CartTool()])
    [event async for event in agent.run_stream("2+2?", AgentContext())]

    first, second = engine.seen
    assert "<runtime_context>" in first[-1].text and first[-2].role == Role.USER
    assert "<runtime_context>" in second[-1].text and second[-2].role == Role.TOOL
    assert sum("<runtime_context>" in m.text for m in second) == 1
