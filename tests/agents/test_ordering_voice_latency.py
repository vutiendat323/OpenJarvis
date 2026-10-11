"""Terminal tool responses work across sites without rewriting agent content."""

from __future__ import annotations

import json

import pytest

from openjarvis.agents._stubs import (
    AgentTextDelta,
    AgentToolFinished,
    AgentToolStarted,
)
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import ToolResult
from openjarvis.engine._stubs import StreamChunk
from openjarvis.tools._stubs import BaseTool, ToolSpec


class SiteTool(BaseTool):
    def __init__(
        self, name, response, *, success=True, terminal=True, continuing=False
    ):
        self.tool_id = name
        self.response = response
        self.success = success
        self.terminal = terminal
        self.continuing = continuing

    @property
    def spec(self):
        return ToolSpec(name=self.tool_id, description="Configured website tool")

    def execute(self, **params):
        return ToolResult(
            tool_name=self.tool_id,
            content="Verified site data",
            success=self.success,
            metadata={
                "complete_turn": self.terminal,
                "continue_agent": self.continuing,
                "customer_message": self.response,
            },
        )


class DecisionEngine:
    engine_id = "scripted"

    def __init__(self, decisions, *, streaming):
        self.decisions = iter(decisions)
        self.streaming = streaming
        self.calls = 0

    def supports_semantic_reasoning_stream(self, model):
        return self.streaming

    def generate(self, messages, **kwargs):
        self.calls += 1
        return next(self.decisions)

    async def stream_full(self, messages, **kwargs):
        response = self.generate(messages, **kwargs)
        if response.get("content"):
            yield StreamChunk(content=response["content"])
        for index, call in enumerate(response.get("tool_calls", [])):
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": index,
                        "id": call["id"],
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"],
                        },
                    }
                ]
            )
        yield StreamChunk(
            finish_reason="tool_calls" if response.get("tool_calls") else "stop"
        )


PREAMBLE = "I will use the website tool now."


def _spoken(events):
    return [event.content for event in events if isinstance(event, AgentTextDelta)]


def decision(tool):
    return {
        "content": PREAMBLE,
        "tool_calls": [
            {"id": "site-action", "name": tool.tool_id, "arguments": json.dumps({})}
        ],
    }


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "name,response",
    [
        ("clinic_appointment", "Your requested appointment information is displayed."),
        ("school_lesson", "Bài học đã được hiển thị."),
        ("library_search", "Les résultats sont affichés."),
    ],
)
@pytest.mark.asyncio
async def test_any_site_terminal_tool_speaks_its_own_response_once(
    name, response, streaming
):
    tool = SiteTool(name, response)
    engine = DecisionEngine([decision(tool)], streaming=streaming)
    agent = OrchestratorAgent(engine, "test", tools=[tool])
    events = [event async for event in agent.run_stream("Use the current website")]
    assert engine.calls == 1
    assert events[-1].result.content == response
    if streaming:
        # The decision text is released before the tool; the tool's own
        # message follows its completion. Each is spoken once.
        assert _spoken(events) == [PREAMBLE, response]
        first_tool = next(
            i for i, e in enumerate(events) if isinstance(e, AgentToolStarted)
        )
        finished = next(
            i for i, e in enumerate(events) if isinstance(e, AgentToolFinished)
        )
        assert events.index(AgentTextDelta(PREAMBLE)) < first_tool
        assert finished < events.index(AgentTextDelta(response))
    else:
        assert _spoken(events) == [response]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_terminal_tool_with_no_speech_never_reinfers_or_invents_an_ack(streaming):
    tool = SiteTool("site_display", "")
    engine = DecisionEngine([decision(tool)], streaming=streaming)
    agent = OrchestratorAgent(engine, "test", tools=[tool])
    events = [
        event async for event in agent.run_stream("Display the requested information")
    ]
    assert engine.calls == 1
    assert events[-1].result.content == ""
    # Only the model's own decision text may be spoken; nothing is invented.
    assert _spoken(events) == ([PREAMBLE] if streaming else [])


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "options", [{"success": False}, {"terminal": False}, {"continuing": True}]
)
@pytest.mark.asyncio
async def test_nonterminal_or_failed_site_tool_allows_agent_to_answer(
    options, streaming
):
    tool = SiteTool("site_data", "Tool message", **options)
    answer = "Here is the response based on the current site's verified result."
    engine = DecisionEngine([decision(tool), {"content": answer}], streaming=streaming)
    agent = OrchestratorAgent(engine, "test", tools=[tool])
    events = [event async for event in agent.run_stream("Explain this site result")]
    assert engine.calls == 2
    assert events[-1].result.content == answer
    assert _spoken(events) == ([PREAMBLE, answer] if streaming else [answer])


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_agent_speech_is_not_rewritten_by_a_merchant_or_language_rule(streaming):
    answer = (
        " ".join(["configured"] * 25) + "? Additional detail supplied by the agent."
    )
    engine = DecisionEngine([{"content": answer}], streaming=streaming)
    agent = OrchestratorAgent(engine, "test", tools=[SiteTool("site_tool", "")])
    events = [
        event async for event in agent.run_stream("Explain the learning material")
    ]
    assert events[-1].result.content == answer
    assert (
        "".join(event.content for event in events if isinstance(event, AgentTextDelta))
        == answer
    )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_terminal_site_response_is_not_clipped_or_translated(streaming):
    response = " ".join(["site-provided"] * 30)
    tool = SiteTool("learning_material", response)
    engine = DecisionEngine([decision(tool)], streaming=streaming)
    agent = OrchestratorAgent(engine, "test", tools=[tool])
    events = [event async for event in agent.run_stream("Display learning material")]
    assert engine.calls == 1
    assert events[-1].result.content == response
    assert _spoken(events) == ([PREAMBLE, response] if streaming else [response])
