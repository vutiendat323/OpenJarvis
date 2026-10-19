"""Per-round diagnostics: why a turn looped, without customer content."""

from __future__ import annotations

import json
import logging

import pytest

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.engine._stubs import StreamChunk
from tests.agents.test_immediate_response import CALC, _tool_round
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import (
    StreamingEngine,
    _CalculatorStub,
    _stream_final_round,
)

LOGGER = "openjarvis.agents.orchestrator"
# `jarvis serve` runs the openjarvis logger at WARNING (cli/log_config.py).
# Capturing lower would pass tests for lines the live log never shows.
SERVE_LEVEL = logging.WARNING


def _lines(caplog, marker: str) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.getMessage().startswith(marker)
    ]


async def _run(agent: OrchestratorAgent) -> list:
    return [event async for event in agent.run_stream("request")]


@pytest.mark.asyncio
async def test_round_log_reports_shape_without_customer_text(caplog):
    caplog.set_level(SERVE_LEVEL, logger=LOGGER)
    secret = "Dạ em tính cho bạn Nguyễn Văn A"
    engine = StreamingEngine(
        [
            [
                StreamChunk(reasoning_content="thinking " * 10),
                *_tool_round(secret, CALC),
            ],
            _stream_final_round("Kết quả là 4."),
        ]
    )
    await _run(
        OrchestratorAgent(engine, "deepseek-v4-flash", tools=[_CalculatorStub()])
    )

    rounds = _lines(caplog, "agent_round")
    assert len(rounds) == 2
    assert "finish=tool_calls" in rounds[0]
    assert "tool_calls=calculator" in rounds[0]
    assert "reasoning_chars=90" in rounds[0]
    assert f"visible_chars={len(secret)}" in rounds[0]
    assert "tool_calls=-" in rounds[1]
    assert not any(secret in record.getMessage() for record in caplog.records)
    assert not any("Kết quả là 4" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_round_log_reports_prompt_cache_and_reasoning_tokens(caplog):
    caplog.set_level(SERVE_LEVEL, logger=LOGGER)
    usage = {
        "prompt_tokens": 7000,
        "completion_tokens": 355,
        "total_tokens": 7355,
        "cached_tokens": 6400,
        "reasoning_tokens": 340,
    }
    engine = StreamingEngine(
        [
            [
                StreamChunk(content="Xin chào."),
                StreamChunk(finish_reason="stop", usage=usage),
            ]
        ]
    )
    await _run(OrchestratorAgent(engine, "gpt-6-luna"))

    [line] = _lines(caplog, "agent_round")
    assert "prompt_tokens=7000" in line
    assert "cached_tokens=6400" in line
    assert "reasoning_tokens=340" in line
    assert "completion_tokens=355" in line


@pytest.mark.asyncio
async def test_reasoning_only_round_and_rejection_are_logged(caplog):
    caplog.set_level(SERVE_LEVEL, logger=LOGGER)
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = StreamingEngine(
        [
            [
                StreamChunk(reasoning_content="r" * 200),
                StreamChunk(finish_reason="stop"),
            ],
            _stream_final_round(
                json.dumps(
                    {"_intent": "NONE", "state": "ANSWER", "message": "Xin chào."}
                )
            ),
        ]
    )
    await _run(
        OrchestratorAgent(engine, "fake", tools=[target], intent_normalization=True)
    )

    first = _lines(caplog, "agent_round")[0]
    assert "visible_chars=0" in first and "reasoning_chars=200" in first
    assert "tool_calls=-" in first
    rejected = _lines(caplog, "agent_final_rejected")
    assert len(rejected) == 1
    assert "visible_chars=0" in rejected[0]
    assert "declared=None" in rejected[0]


@pytest.mark.asyncio
async def test_blocked_dispatch_is_logged_with_its_reason(caplog):
    caplog.set_level(SERVE_LEVEL, logger=LOGGER)
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = StreamingEngine(
        [
            # No _intent: IntentState.prepare refuses the call before dispatch.
            _tool_round("", ("finish", "{}")),
            _stream_final_round(
                json.dumps(
                    {
                        "_intent": "FINALIZE",
                        "state": "NEEDS_INPUT",
                        "message": "Cần thêm thông tin.",
                    }
                )
            ),
        ]
    )
    await _run(
        OrchestratorAgent(engine, "fake", tools=[target], intent_normalization=True)
    )

    blocked = _lines(caplog, "agent_dispatch_blocked")
    assert len(blocked) == 1
    assert "tools=finish" in blocked[0]
    assert "a declared canonical intent is required" in blocked[0]
    assert target.calls == []


@pytest.mark.asyncio
async def test_max_turns_is_logged_with_intent_state(caplog):
    caplog.set_level(SERVE_LEVEL, logger=LOGGER)
    engine = StreamingEngine([_tool_round("", CALC), _tool_round("", CALC)])
    await _run(
        OrchestratorAgent(
            engine, "deepseek-v4-flash", tools=[_CalculatorStub()], max_turns=2
        )
    )

    exhausted = _lines(caplog, "agent_max_turns")
    assert len(exhausted) == 1
    assert "turns=2" in exhausted[0]
    assert "tool_results=2" in exhausted[0]


def test_sync_path_logs_rounds_rejection_and_max_turns(caplog):
    from tests.agents.fake_engine import FakeEngine

    caplog.set_level(SERVE_LEVEL, logger=LOGGER)
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"content": "Không phải JSON.", "finish_reason": "stop", "usage": {}},
            {
                "content": json.dumps(
                    {"_intent": "NONE", "state": "ANSWER", "message": "Xin chào."}
                ),
                "finish_reason": "stop",
                "usage": {"completion_tokens": 7},
            },
        ]
    )
    OrchestratorAgent(engine, "fake", tools=[target], intent_normalization=True).run(
        "request"
    )

    rounds = _lines(caplog, "agent_round")
    assert len(rounds) == 2
    assert "path=sync" in rounds[0] and "visible_chars=16" in rounds[0]
    assert "reasoning_chars=-" in rounds[0]
    assert "completion_tokens=7" in rounds[1]
    assert len(_lines(caplog, "agent_final_rejected")) == 1

    caplog.clear()
    looping = FakeEngine(
        [
            {
                "content": "",
                "finish_reason": "tool_calls",
                "usage": {},
                "tool_calls": [{"id": "c1", "name": "calculator", "arguments": "{}"}],
            }
        ]
    )
    OrchestratorAgent(looping, "fake", tools=[_CalculatorStub()], max_turns=2).run(
        "request"
    )
    assert len(_lines(caplog, "agent_max_turns")) == 1
    assert "path=sync" in _lines(caplog, "agent_max_turns")[0]
