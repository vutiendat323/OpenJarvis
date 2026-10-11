"""A round that is still only reasoning after a deadline is retried without it.

Live 2026-10-10 13:49 (deepseek-flash, ``low``): a "payment + add" request kept
the customer waiting 15 s inside one round that never produced a word or a tool
call. ``finish=length`` only arrives after the whole budget is spent (~20 s);
the deadline cuts that wait to a few seconds.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.config import AgentConfig, load_config
from openjarvis.engine._stubs import StreamChunk
from tests.agents.test_immediate_response import _tool_round
from tests.agents.test_orchestrator import (
    StreamingEngine,
    _CalculatorStub,
    _stream_final_round,
)

CALC = ("calculator", json.dumps({"expression": "2+2"}))
OFF = {"extra_body": {"thinking": {"type": "disabled"}}}


class StallingEngine(StreamingEngine):
    """Round ``stall_round`` reasons, then goes silent for ``stall_seconds``."""

    def __init__(self, rounds, *, stall_round=0, stall_seconds=30.0, off=True):
        super().__init__(rounds)
        self._stall_round = stall_round
        self._stall_seconds = stall_seconds
        self._off = off
        self.cancelled = 0

    def reasoning_off_options(self, model):
        del model
        return json.loads(json.dumps(OFF)) if self._off else None

    async def stream_full(self, messages, **kwargs):
        index = len(self.calls)
        self.calls.append((messages, kwargs))
        if index == self._stall_round and "extra_body" not in kwargs:
            self._rounds.pop(0)
            yield StreamChunk(reasoning_content="thinking " * 5)
            try:
                await asyncio.sleep(self._stall_seconds)
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            yield StreamChunk(finish_reason="stop")
            return
        for chunk in self._rounds.pop(0):
            yield chunk


async def _run(engine, **kwargs) -> tuple[list, float]:
    agent = OrchestratorAgent(
        engine, "deepseek-flash", tools=[_CalculatorStub()], max_turns=6, **kwargs
    )
    started = time.monotonic()
    events = [event async for event in agent.run_stream("request")]
    return events, time.monotonic() - started


@pytest.mark.asyncio
async def test_a_stalled_reasoning_round_is_retried_with_reasoning_off():
    engine = StallingEngine([[], _tool_round("", CALC), _stream_final_round("Xong.")])

    events, elapsed = await _run(engine, reasoning_deadline_seconds=0.2)

    assert elapsed < 5
    assert engine.cancelled == 1
    assert "extra_body" not in engine.calls[0][1]
    assert engine.calls[1][1]["extra_body"] == OFF["extra_body"]
    assert events[-1].result.content == "Xong."


@pytest.mark.asyncio
async def test_disabled_by_default():
    engine = StallingEngine([[], _stream_final_round("Xong.")], stall_seconds=0.3)

    events, _ = await _run(engine)

    assert engine.cancelled == 0
    assert len(engine.calls) == 1
    assert events[-1].result.content == ""


@pytest.mark.asyncio
async def test_engine_without_a_reasoning_switch_is_left_alone():
    engine = StallingEngine([[], []], stall_seconds=0.3, off=False)

    await _run(engine, reasoning_deadline_seconds=0.05)

    assert engine.cancelled == 0
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_a_round_that_answers_in_time_is_not_interrupted():
    engine = StallingEngine(
        [_tool_round("", CALC), _stream_final_round("Xong.")], stall_round=99
    )

    await _run(engine, reasoning_deadline_seconds=0.2)

    assert all("extra_body" not in kwargs for _, kwargs in engine.calls)


def test_kiosk_config_sets_a_deadline_and_core_default_is_off():
    assert AgentConfig().reasoning_deadline_seconds == 0.0
    cfg = load_config(path="configs/openjarvis/examples/ordering-kiosk-mcp.toml")
    assert 3.0 <= cfg.agent.reasoning_deadline_seconds <= 15.0
