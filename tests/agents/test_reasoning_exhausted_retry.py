"""A round that only reasoned until the budget ran out is retried with
reasoning off, once, instead of reasoning again.

Live 2026-10-10 13:25 (deepseek-flash, ``low``): round 1 ended ``finish=length``
after 15.8k characters of reasoning with no text and no tool call; the old
"Continue from where you left off" nudge reasoned another 6.9k characters and
the first spoken word came 32 s after the customer stopped talking.
"""

from __future__ import annotations

import json

import pytest

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.engine._stubs import StreamChunk
from tests.agents.test_immediate_response import _tool_round
from tests.agents.test_orchestrator import (
    StreamingEngine,
    _CalculatorStub,
    _stream_final_round,
)

CALC = ("calculator", json.dumps({"expression": "2+2"}))
OFF = {"extra_body": {"thinking": {"type": "disabled"}}}


class OffCapableEngine(StreamingEngine):
    def reasoning_off_options(self, model: str):
        del model
        return json.loads(json.dumps(OFF))


def _exhausted() -> list[StreamChunk]:
    return [
        StreamChunk(reasoning_content="r" * 300),
        StreamChunk(finish_reason="length"),
    ]


async def _run(engine, **kwargs) -> list:
    agent = OrchestratorAgent(
        engine, "deepseek-flash", tools=[_CalculatorStub()], max_turns=6, **kwargs
    )
    return [event async for event in agent.run_stream("request")]


def _continue_nudges(engine) -> int:
    # The engine keeps a reference to the one growing message list, so the last
    # call's view holds every nudge sent during the turn.
    messages, _ = engine.calls[-1]
    return sum(
        1 for m in messages if "Continue from where you left off" in (m.content or "")
    )


@pytest.mark.asyncio
async def test_reasoning_only_round_is_retried_with_reasoning_off():
    engine = OffCapableEngine(
        [_exhausted(), _tool_round("", CALC), _stream_final_round("Kết quả là 4.")]
    )

    await _run(engine)

    assert "extra_body" not in engine.calls[0][1]
    assert engine.calls[1][1]["extra_body"] == OFF["extra_body"]
    assert _continue_nudges(engine) == 0


@pytest.mark.asyncio
async def test_reasoning_stays_off_for_the_rest_of_the_turn():
    engine = OffCapableEngine(
        [_exhausted(), _tool_round("", CALC), _stream_final_round("Kết quả là 4.")]
    )

    await _run(engine)

    assert engine.calls[2][1]["extra_body"] == OFF["extra_body"]


@pytest.mark.asyncio
async def test_the_retry_happens_once():
    engine = OffCapableEngine([_exhausted() for _ in range(4)])

    await _run(engine)

    # first round, one retry with reasoning off, then the old nudges (two max)
    assert [("extra_body" in kwargs) for _, kwargs in engine.calls] == [
        False,
        True,
        True,
        True,
    ]
    assert _continue_nudges(engine) == 2


@pytest.mark.asyncio
async def test_visible_text_that_ran_out_keeps_the_continuation():
    engine = OffCapableEngine(
        [
            [StreamChunk(content="Một nửa"), StreamChunk(finish_reason="length")],
            _stream_final_round(" câu trả lời."),
        ]
    )

    await _run(engine)

    assert "extra_body" not in engine.calls[1][1]
    assert _continue_nudges(engine) == 1


@pytest.mark.asyncio
async def test_engine_without_an_override_keeps_the_old_behaviour():
    engine = StreamingEngine([_exhausted(), _stream_final_round("Xong.")])

    await _run(engine)

    assert "extra_body" not in engine.calls[1][1]
    assert _continue_nudges(engine) == 1
