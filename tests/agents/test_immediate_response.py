"""Speech written alongside a tool call is released before the tool runs."""

from __future__ import annotations

import json

import pytest

from openjarvis.agents._stubs import (
    AgentRunCompleted,
    AgentTextDelta,
    AgentToolFinished,
    AgentToolStarted,
)
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.engine._stubs import StreamChunk
from tests.agents.test_intent_normalization import ExistingTool
from tests.agents.test_orchestrator import (
    StreamingEngine,
    _CalculatorStub,
    _DisplayStub,
    _PendingApprovalStub,
    _stream_final_round,
)
from tests.agents.test_ordering_voice_latency import SiteTool

MODEL = "deepseek-v4-flash"
CALC = ("calculator", '{"expression":"2+2"}')


def _tool_round(text: str, *calls: tuple[str, str]) -> list[StreamChunk]:
    """One decision round: optional visible text, then its tool calls."""
    chunks = [StreamChunk(content=text)] if text else []
    for index, (name, arguments) in enumerate(calls):
        chunks.append(
            StreamChunk(
                tool_calls=[
                    {
                        "index": index,
                        "id": f"call-{name}-{index}",
                        "function": {"name": name, "arguments": arguments},
                    }
                ]
            )
        )
    chunks.append(StreamChunk(finish_reason="tool_calls"))
    return chunks


def _texts(events) -> list[str]:
    return [event.content for event in events if isinstance(event, AgentTextDelta)]


def _kinds(events) -> list[str]:
    return [type(event).__name__ for event in events]


async def _run(agent: OrchestratorAgent, prompt: str = "request") -> list:
    return [event async for event in agent.run_stream(prompt)]


@pytest.mark.asyncio
async def test_tool_round_text_is_released_before_its_tool_starts():
    engine = StreamingEngine(
        [_tool_round("Dạ, em tính ngay.", CALC), _stream_final_round("Kết quả là 4.")]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _kinds(events) == [
        "AgentTextDelta",
        "AgentToolStarted",
        "AgentToolFinished",
        "AgentTextDelta",
        "AgentRunCompleted",
    ]
    assert _texts(events) == ["Dạ, em tính ngay.", "Kết quả là 4."]
    assert events[-1].result.content == "Kết quả là 4."


@pytest.mark.asyncio
async def test_every_tool_round_is_released_once_in_order():
    engine = StreamingEngine(
        [
            _tool_round("Em kiểm tra trước.", CALC),
            _tool_round("Đã có số liệu, em tính tiếp.", CALC),
            _stream_final_round("Xong, kết quả là 4."),
        ]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _kinds(events) == [
        "AgentTextDelta",
        "AgentToolStarted",
        "AgentToolFinished",
        "AgentTextDelta",
        "AgentToolStarted",
        "AgentToolFinished",
        "AgentTextDelta",
        "AgentRunCompleted",
    ]
    assert _texts(events) == [
        "Em kiểm tra trước.",
        "Đã có số liệu, em tính tiếp.",
        "Xong, kết quả là 4.",
    ]


@pytest.mark.asyncio
async def test_blank_tool_round_releases_nothing_and_still_runs_the_tool():
    engine = StreamingEngine(
        [_tool_round("   ", CALC), _stream_final_round("Kết quả là 4.")]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _texts(events) == ["Kết quả là 4."]
    assert any(isinstance(event, AgentToolFinished) and event.ok for event in events)


@pytest.mark.asyncio
async def test_completed_display_does_not_repeat_the_released_round():
    engine = StreamingEngine([_tool_round("Menu đây ạ.", ("display_menu", "{}"))])
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_DisplayStub()]))

    assert _kinds(events) == [
        "AgentTextDelta",
        "AgentToolStarted",
        "AgentToolFinished",
        "AgentRunCompleted",
    ]
    assert _texts(events) == ["Menu đây ạ."]
    assert events[-1].result.content == "Menu đây ạ."
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_completed_display_speaks_round_then_tool_customer_message():
    tool = SiteTool("site_display", "Đây là kết quả đã xác minh.")
    engine = StreamingEngine([_tool_round("Em mở ngay.", ("site_display", "{}"))])
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[tool]))

    assert _kinds(events) == [
        "AgentTextDelta",
        "AgentToolStarted",
        "AgentToolFinished",
        "AgentTextDelta",
        "AgentRunCompleted",
    ]
    assert _texts(events) == ["Em mở ngay.", "Đây là kết quả đã xác minh."]
    assert events[-1].result.content == "Đây là kết quả đã xác minh."


@pytest.mark.asyncio
async def test_pending_approval_speaks_round_text_but_completes_empty():
    engine = StreamingEngine(
        [_tool_round("Em gửi yêu cầu xác nhận.", ("approval", "{}"))]
    )
    events = await _run(
        OrchestratorAgent(engine, MODEL, tools=[_PendingApprovalStub()])
    )

    assert _kinds(events)[:2] == ["AgentTextDelta", "AgentToolStarted"]
    assert _texts(events) == ["Em gửi yêu cầu xác nhận."]
    assert events[-1].result.content == ""
    assert events[-1].result.metadata["pending_approval"] is True


@pytest.mark.asyncio
async def test_failed_target_after_released_round_ends_without_success():
    target = ExistingTool(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}, fail=True
    )
    engine = StreamingEngine(
        [
            _tool_round(
                "Em đang hoàn tất.", ("finish", json.dumps({"_intent": "FINALIZE"}))
            ),
            _stream_final_round(
                json.dumps(
                    {
                        "_intent": "FINALIZE",
                        "state": "FAILED",
                        "message": "Chưa hoàn tất được.",
                    }
                )
            ),
        ]
    )
    events = await _run(
        OrchestratorAgent(engine, "fake", tools=[target], intent_normalization=True)
    )

    assert _texts(events) == ["Em đang hoàn tất.", "Chưa hoàn tất được."]
    assert not any("{" in text for text in _texts(events))
    assert events[-1].result.metadata["intent_satisfied"] is False


@pytest.mark.asyncio
async def test_blocked_dispatch_after_released_round_ends_without_success():
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = StreamingEngine(
        [
            _tool_round("Em đang hoàn tất.", ("finish", "{}")),
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
    events = await _run(
        OrchestratorAgent(engine, "fake", tools=[target], intent_normalization=True)
    )

    assert target.calls == []
    assert _texts(events) == ["Em đang hoàn tất.", "Cần thêm thông tin."]
    assert events[-1].result.metadata["intent_satisfied"] is False


@pytest.mark.asyncio
async def test_engine_error_before_round_completes_releases_nothing():
    class DroppingEngine(StreamingEngine):
        async def stream_full(self, messages, **kwargs):
            self.calls.append((messages, kwargs))
            yield StreamChunk(content="Em đang ")
            raise RuntimeError("stream dropped")

    agent = OrchestratorAgent(DroppingEngine([]), MODEL, tools=[_CalculatorStub()])
    events = []
    with pytest.raises(RuntimeError, match="stream dropped"):
        async for event in agent.run_stream("request"):
            events.append(event)

    assert _texts(events) == []
    assert not any(isinstance(event, AgentToolStarted) for event in events)


@pytest.mark.asyncio
async def test_private_reasoning_in_a_tool_round_is_never_released():
    engine = StreamingEngine(
        [
            _tool_round("<think>private plan</think>Em tính nhé.", CALC),
            _stream_final_round("Kết quả là 4."),
        ]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _texts(events) == ["Em tính nhé.", "Kết quả là 4."]
    assert not any("private" in text for text in _texts(events))


@pytest.mark.asyncio
async def test_round_with_several_tool_calls_is_released_once_before_all_of_them():
    engine = StreamingEngine(
        [
            _tool_round("Em làm hai phép tính.", CALC, CALC),
            _stream_final_round("Cả hai đều là 4."),
        ]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _kinds(events)[:3] == [
        "AgentTextDelta",
        "AgentToolStarted",
        "AgentToolStarted",
    ]
    assert _texts(events) == ["Em làm hai phép tính.", "Cả hai đều là 4."]


@pytest.mark.asyncio
async def test_length_continued_tool_round_is_released_once_merged():
    engine = StreamingEngine(
        [
            [StreamChunk(content="Em đang "), StreamChunk(finish_reason="length")],
            _tool_round("tính.", CALC),
            _stream_final_round("Kết quả là 4."),
        ]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _texts(events) == ["Em đang tính.", "Kết quả là 4."]
    assert len(engine.calls) == 3
    assert isinstance(events[-1], AgentRunCompleted)


@pytest.mark.asyncio
async def test_json_shaped_tool_round_text_is_never_released():
    reader = ExistingTool("reader", metadata={"read_only": True})
    engine = StreamingEngine(
        [
            _tool_round(
                json.dumps(
                    {"_intent": "NONE", "state": "ANSWER", "message": "Dạ em xem."}
                ),
                ("reader", json.dumps({"_intent": "NONE"})),
            ),
            _stream_final_round(
                json.dumps({"_intent": "NONE", "state": "ANSWER", "message": "Đây ạ."})
            ),
        ]
    )
    events = await _run(
        OrchestratorAgent(engine, "fake", tools=[reader], intent_normalization=True)
    )

    assert _texts(events) == ["Đây ạ."]
    assert any(isinstance(event, AgentToolStarted) for event in events)
