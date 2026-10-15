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
async def test_engine_error_mid_round_keeps_what_was_already_spoken():
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

    # Speech leaves with the first tokens; the voice layer resets its
    # aggregation when the run errors, so the rest is never spoken.
    assert _texts(events) == ["Em đang "]
    assert not any(isinstance(event, AgentToolStarted) for event in events)
    assert not any(isinstance(event, AgentRunCompleted) for event in events)


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
async def test_length_continued_tool_round_is_spoken_once_across_continuations():
    engine = StreamingEngine(
        [
            [StreamChunk(content="Em đang "), StreamChunk(finish_reason="length")],
            _tool_round("tính.", CALC),
            _stream_final_round("Kết quả là 4."),
        ]
    )
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _texts(events) == ["Em đang ", "tính.", "Kết quả là 4."]
    assert len(engine.calls) == 3
    assert isinstance(events[-1], AgentRunCompleted)


@pytest.mark.asyncio
async def test_json_shaped_tool_round_text_speaks_only_its_accepted_message():
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

    # Its message is speech as it is written; the JSON around it never is.
    assert _texts(events) == ["Dạ em xem.", "Đây ạ."]
    assert not any("_intent" in text or "{" in text for text in _texts(events))
    assert any(isinstance(event, AgentToolStarted) for event in events)


def _json_chunks(*parts: str) -> list[StreamChunk]:
    return [StreamChunk(content=part) for part in parts] + [
        StreamChunk(finish_reason="stop")
    ]


def _guarded_agent(engine) -> OrchestratorAgent:
    reader = ExistingTool("reader", metadata={"read_only": True})
    return OrchestratorAgent(engine, "fake", tools=[reader], intent_normalization=True)


@pytest.mark.asyncio
async def test_plain_final_answer_is_spoken_chunk_by_chunk():
    engine = StreamingEngine([_json_chunks("Dạ ", "món ", "này ", "35k.")])
    events = await _run(OrchestratorAgent(engine, MODEL, tools=[_CalculatorStub()]))

    assert _texts(events) == ["Dạ ", "món ", "này ", "35k."]
    assert events[-1].result.content == "Dạ món này 35k."


@pytest.mark.asyncio
async def test_guarded_json_answer_streams_message_not_json():
    engine = StreamingEngine(
        [
            _json_chunks(
                '{"_intent":"NONE","state":"ANSW',
                'ER","message":"Dạ quán ',
                "mở đến 22h ",
                'ạ."}',
            )
        ]
    )
    events = await _run(_guarded_agent(engine))

    texts = _texts(events)
    assert texts == ["Dạ quán ", "mở đến 22h ", "ạ."]
    assert events[-1].result.content == "Dạ quán mở đến 22h ạ."


@pytest.mark.asyncio
async def test_guarded_json_answer_decodes_escapes_and_split_unicode():
    engine = StreamingEngine(
        [
            _json_chunks(
                '{"_intent":"NONE","state":"ANSWER","message":"Cà ph\\u00',
                'ea \\"đá\\"\\nxong"}',
            )
        ]
    )
    events = await _run(_guarded_agent(engine))

    assert "".join(_texts(events)) == 'Cà phê "đá"\nxong'
    assert events[-1].result.content == 'Cà phê "đá"\nxong'


@pytest.mark.asyncio
async def test_rejected_guarded_answer_is_never_spoken_before_the_repair():
    # An unknown intent is rejected by the contract before the message is
    # written, so none of it is spoken; the repair is.
    engine = StreamingEngine(
        [
            _json_chunks(
                '{"_intent":"BOGUS","state":"ANSWER","message":"Em đã đặt xong."}'
            ),
            _json_chunks(
                '{"_intent":"NONE","state":"ANSWER","message":"Anh cần gì ạ?"}'
            ),
        ]
    )
    events = await _run(_guarded_agent(engine))

    texts = _texts(events)
    assert not any("đặt xong" in text for text in texts)
    assert "".join(texts) == "Anh cần gì ạ?"


@pytest.mark.asyncio
async def test_guarded_plain_answer_is_held_until_the_contract_decides():
    # Not reached: plain text is rejected by the contract, so it is not spoken.
    engine = StreamingEngine(
        [
            _json_chunks("Em đã đặt xong rồi ạ."),
            _json_chunks(
                '{"_intent":"NONE","state":"ANSWER","message":"Anh cần gì ạ?"}'
            ),
        ]
    )
    events = await _run(_guarded_agent(engine))

    assert "".join(_texts(events)) == "Anh cần gì ạ?"
    assert not any("đặt xong" in text for text in _texts(events))


@pytest.mark.asyncio
async def test_message_written_before_intent_waits_for_the_contract():
    engine = StreamingEngine(
        [_json_chunks('{"message":"Dạ ', 'có ạ.","_intent":"NONE","state":"ANSWER"}')]
    )
    events = await _run(_guarded_agent(engine))

    # Nothing can be vetted early, so the whole message leaves at the end.
    assert _texts(events) == ["Dạ có ạ."]
