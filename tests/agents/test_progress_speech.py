"""The first tool call of a turn carries a short, request-specific clause.

Asked for in the prompt alone, the clause was skipped: on 2026-10-10 12:13-12:16
deepseek-flash wrote no text beside any of ten first-round tool calls, so the
customer heard nothing until the turn's last round. A required ``_progress``
argument makes the model write it from the customer's request (never a fixed
phrase); the runtime speaks it before the tool runs and never passes it on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjarvis.agents._stubs import AgentTextDelta, AgentToolStarted
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.config import AgentConfig, load_config
from tests.agents.fake_engine import FakeEngine
from tests.agents.test_immediate_response import _tool_round
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import StreamingEngine, _stream_final_round


def _args(**values: str) -> str:
    return json.dumps(values, ensure_ascii=False)


async def _run(agent: OrchestratorAgent) -> list:
    return [event async for event in agent.run_stream("request")]


def _spoken(events: list) -> list[str]:
    return [e.content for e in events if isinstance(e, AgentTextDelta)]


def _agent(engine, tools, **options) -> OrchestratorAgent:
    return OrchestratorAgent(engine, "fake", tools=tools, **options)


@pytest.mark.asyncio
async def test_enabled_every_tool_requires_a_progress_clause():
    tool = ExistingTool("lookup")
    engine = StreamingEngine(
        [
            _tool_round("", ("lookup", _args(_progress="Mình xem bàn 57"))),
            _stream_final_round("Xong."),
        ]
    )
    await _run(_agent(engine, [tool], progress_speech=True))

    schema = engine.calls[0][1]["tools"][0]["function"]["parameters"]
    assert schema["properties"]["_progress"]["type"] == "string"
    assert "_progress" in schema["required"]


@pytest.mark.asyncio
async def test_disabled_by_default():
    tool = ExistingTool("lookup")
    engine = StreamingEngine(
        [_tool_round("", ("lookup", "{}")), _stream_final_round("Xong.")]
    )
    await _run(_agent(engine, [tool]))

    schema = engine.calls[0][1]["tools"][0]["function"]["parameters"]
    assert "_progress" not in schema.get("properties", {})
    assert AgentConfig().progress_speech is False


@pytest.mark.asyncio
async def test_clause_is_spoken_before_the_tool_and_never_reaches_it():
    tool = ExistingTool("lookup")
    engine = StreamingEngine(
        [
            _tool_round("", ("lookup", _args(_progress="Mình xem bàn 57"))),
            _stream_final_round("Xong."),
        ]
    )
    events = await _run(_agent(engine, [tool], progress_speech=True))

    spoken = AgentTextDelta("Mình xem bàn 57")
    started = next(i for i, e in enumerate(events) if isinstance(e, AgentToolStarted))
    assert events.index(spoken) < started
    assert tool.calls == [{}]


@pytest.mark.asyncio
async def test_only_the_turns_first_clause_is_spoken():
    tool = ExistingTool("lookup")
    engine = StreamingEngine(
        [
            _tool_round("", ("lookup", _args(_progress="Mình xem bàn"))),
            _tool_round("", ("lookup", _args(_progress="Mình thêm món"))),
            _stream_final_round("Xong."),
        ]
    )
    events = await _run(_agent(engine, [tool], progress_speech=True))

    assert "Mình xem bàn" in _spoken(events)
    assert "Mình thêm món" not in _spoken(events)


@pytest.mark.asyncio
async def test_round_prose_already_spoken_suppresses_the_clause():
    tool = ExistingTool("lookup")
    engine = StreamingEngine(
        [
            _tool_round(
                "Mình kiểm tra nhé.", ("lookup", _args(_progress="Mình xem bàn"))
            ),
            _stream_final_round("Xong."),
        ]
    )
    events = await _run(_agent(engine, [tool], progress_speech=True))

    assert "Mình kiểm tra nhé." in _spoken(events)
    assert "Mình xem bàn" not in _spoken(events)


@pytest.mark.asyncio
async def test_clause_coexists_with_intent_and_is_stripped_before_dispatch():
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = StreamingEngine(
        [
            _tool_round(
                "", ("finish", _args(_intent="FINALIZE", _progress="Mình làm ngay"))
            ),
            _stream_final_round("Xong."),
        ]
    )
    events = await _run(
        _agent(engine, [target], progress_speech=True, intent_normalization=True)
    )

    schema = engine.calls[0][1]["tools"][0]["function"]["parameters"]
    assert {"_intent", "_progress"} <= set(schema["required"])
    assert "Mình làm ngay" in _spoken(events)
    assert target.calls == [{}]


def test_blocking_path_strips_the_clause_too():
    tool = ExistingTool("lookup")
    engine = FakeEngine(
        [
            {
                "tool_calls": [
                    {
                        "id": "c1",
                        "name": "lookup",
                        "arguments": _args(_progress="Mình xem"),
                    }
                ]
            },
            {"content": "Xong.", "finish_reason": "stop", "usage": {}},
        ]
    )
    _agent(engine, [tool], progress_speech=True).run("request")

    assert tool.calls == [{}]


def test_kiosk_config_enables_progress_speech():
    path = Path("configs/openjarvis/examples/ordering-kiosk-mcp.toml")
    assert load_config(path=path).agent.progress_speech is True
