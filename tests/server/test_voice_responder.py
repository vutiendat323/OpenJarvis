"""A fast first sentence races the Agent's first text (chat-supervisor pattern)."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
import tomllib

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents._stubs import (
    PREAMBLE_SPOKEN,
    AgentContext,
    AgentResult,
    AgentRunCompleted,
    AgentTextDelta,
)
from openjarvis.engine._stubs import StreamChunk
from openjarvis.server.voice.llm import OpenJarvisLLMService, recent_conversation
from openjarvis.server.voice.responder import (
    DELEGATE_TOOL,
    ResponderReply,
    VoiceResponder,
    load_responder_settings,
)
from openjarvis.server.voice.tts import FLUSH_TTS_METADATA

_TURN_TIMEOUT_S = 10.0


class _Responder:
    model = "fast-model"

    def __init__(
        self,
        line: str | None,
        *,
        after: float = 0.0,
        fail: bool = False,
        kind: str = "delegate",
        during=None,
    ):
        self._line = line
        self._after = after
        self._fail = fail
        self._kind = kind
        self._during = during
        self.calls: list[tuple[str, str]] = []
        self.delegated_at: float | None = None

    async def respond(self, utterance, conversation="", on_delegate=None):
        self.calls.append((utterance, conversation))
        await asyncio.sleep(self._after)
        if self._during is not None:
            self._during()
        if self._fail:
            raise RuntimeError("provider down")
        if self._kind == "delegate":
            self.delegated_at = time.monotonic()
            if on_delegate is not None:
                on_delegate(self._line or "")
            await asyncio.sleep(0.05)  # the unused argument still streams
            return ResponderReply("delegate", self._line or "")
        return ResponderReply("answer", self._line) if self._line else None


class _Binding:
    model = "test-model"

    def __init__(self, text: str, *, after: float = 0.0):
        self._text = text
        self._after = after
        self.context: AgentContext | None = None
        self.started_at: float | None = None

    def with_model(self, model):
        binding = _Binding(self._text, after=self._after)
        binding.model = model
        return binding

    async def run_stream(self, prompt, context):
        del prompt
        self.started_at = time.monotonic()
        self.context = context
        await asyncio.sleep(self._after)
        yield AgentTextDelta(self._text)
        yield AgentRunCompleted(AgentResult(content=self._text, turns=1))


async def _spoken(service, messages) -> list[LLMTextFrame]:
    seen: list[LLMTextFrame] = []

    class _Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMTextFrame):
                seen.append(frame)
            await self.push_frame(frame, direction)

    worker = PipelineWorker(
        Pipeline([service, _Sink()]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.01)
        await worker.queue_frame(LLMContextFrame(LLMContext(messages=messages)))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if not service._answering and service._turn_state.turn_id:
                break
        await asyncio.sleep(0.05)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(
        asyncio.gather(runner.run(), drive()), timeout=_TURN_TIMEOUT_S
    )
    return seen


def _user(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


@pytest.mark.anyio
async def test_preamble_is_spoken_first_while_the_agent_is_slow():
    responder = _Responder("Dạ, để em kiểm tra ạ.")
    binding = _Binding("Có ạ, quán mở đến 22h.", after=0.3)
    service = OpenJarvisLLMService(binding, responder=responder)

    frames = await _spoken(service, _user("quán mở tới mấy giờ"))

    assert frames[0].text == "Dạ, để em kiểm tra ạ. "
    assert (
        "".join(f.text for f in frames)
        == "Dạ, để em kiểm tra ạ. Có ạ, quán mở đến 22h."
    )
    # TTS synthesizes it at once rather than waiting for more text.
    assert frames[0].metadata.get(FLUSH_TTS_METADATA) is True
    assert binding.context.metadata[PREAMBLE_SPOKEN].is_set()


@pytest.mark.anyio
async def test_responder_sees_the_assistant_line_a_confirmation_answers():
    responder = _Responder(None)
    service = OpenJarvisLLMService(_Binding("Dạ."), responder=responder)

    await _spoken(
        service,
        [
            {"role": "assistant", "content": "Anh thanh toán bằng QR phải không ạ?"},
            {"role": "user", "content": "ừ đúng rồi"},
        ],
    )

    assert responder.calls == [
        ("ừ đúng rồi", "Assistant: Anh thanh toán bằng QR phải không ạ?")
    ]


@pytest.mark.anyio
async def test_event_turns_never_get_a_preamble():
    responder = _Responder("Dạ, để em kiểm tra ạ.")
    service = OpenJarvisLLMService(_Binding("Đơn đã thanh toán."), responder=responder)
    service.push_frame = _ignore  # an event turn outside a running pipeline

    await service.run_event_turn("payment_completed")

    assert responder.calls == []


async def _ignore(*args, **kwargs):
    del args, kwargs


def test_recent_conversation_skips_the_latest_user_words():
    class _Context:
        def get_messages(self):
            return [
                {"role": "assistant", "content": "old"},
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "Anh lấy size nào ạ?"},
                {"role": "user", "content": "size lớn"},
                {"role": "user", "content": "nhé"},
            ]

    assert recent_conversation(_Context()) == (
        "Assistant: old\nCustomer: a\nAssistant: Anh lấy size nào ạ?"
    )


class _ToolEngine:
    """Yields the given chunks, as a provider streaming text and tool calls."""

    def __init__(self, *chunks: StreamChunk):
        self.chunks = chunks
        self.kwargs = None
        self.messages = None

    def reasoning_off_options(self, model):
        return {"extra_body": {"thinking": {"type": "disabled"}}}

    async def stream_full(self, messages, **kwargs):
        self.kwargs = kwargs
        self.messages = messages
        for chunk in self.chunks:
            yield chunk


def _call(
    arguments: str = "", name: str = "getNextResponseFromSupervisor"
) -> StreamChunk:
    return StreamChunk(
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "function": {"name": name, "arguments": arguments},
            }
        ]
    )


@pytest.mark.anyio
async def test_a_tool_call_delegates_with_the_text_before_it_as_the_filler():
    seen: list[str] = []
    engine = _ToolEngine(
        StreamChunk(content="Mình mở "),
        StreamChunk(content="menu nhé."),
        _call('{"relevantContext'),
        _call('FromLastUserMessage": "menu"}', name=""),
        StreamChunk(finish_reason="tool_calls"),
    )

    reply = await VoiceResponder(engine, "fast").respond("menu", "", seen.append)

    assert seen == ["Mình mở menu nhé."]
    assert reply == ResponderReply("delegate", "Mình mở menu nhé.")


@pytest.mark.anyio
async def test_text_without_a_tool_call_answers_alone():
    seen: list[str] = []
    engine = _ToolEngine(
        StreamChunk(content="Chào bạn!\nBạn cần gì ạ?"),
        StreamChunk(finish_reason="stop"),
    )

    reply = await VoiceResponder(engine, "fast").respond("chào em", "", seen.append)

    assert seen == []
    assert reply == ResponderReply("answer", "Chào bạn! Bạn cần gì ạ?")


@pytest.mark.anyio
async def test_a_tool_call_without_text_delegates_with_no_filler():
    seen: list[str] = []

    reply = await VoiceResponder(_ToolEngine(_call("{}")), "fast").respond(
        "ừ", "", seen.append
    )

    assert seen == [""]
    assert reply == ResponderReply("delegate", "")


@pytest.mark.anyio
async def test_only_text_before_the_call_is_the_filler():
    # Text and the first, nameless call fragment arrive in one chunk; the
    # argument and any text after the call are never returned.
    engine = _ToolEngine(
        StreamChunk(content="Mình xem "),
        StreamChunk(
            content="giỏ hàng nhé.",
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "", "arguments": ""},
                }
            ],
        ),
        _call('{"relevantContextFromLastUserMessage": "BÍ MẬT"}', name=""),
        StreamChunk(content=" Đã thêm."),
    )

    reply = await VoiceResponder(engine, "fast").respond("giỏ hàng")

    assert reply == ResponderReply("delegate", "Mình xem giỏ hàng nhé.")


@pytest.mark.anyio
@pytest.mark.parametrize("parts", [(), ("  ",)])
async def test_an_empty_reply_is_none(parts):
    engine = _ToolEngine(
        *(StreamChunk(content=part) for part in parts),
        StreamChunk(finish_reason="stop"),
    )

    assert await VoiceResponder(engine, "fast").respond("ừ") is None


@pytest.mark.anyio
async def test_the_only_tool_is_the_delegation_tool_with_reasoning_off():
    engine = _ToolEngine(StreamChunk(content="Dạ."))

    await VoiceResponder(engine, "fast").respond("menu có gì", "Assistant: Chào")

    assert engine.kwargs["tools"] == [DELEGATE_TOOL]
    function = DELEGATE_TOOL["function"]
    assert function["name"] == "getNextResponseFromSupervisor"
    assert function["parameters"]["required"] == ["relevantContextFromLastUserMessage"]
    assert engine.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert engine.messages[-1].content == (
        "Conversation so far:\nAssistant: Chào\nCustomer (latest): menu có gì"
    )


def test_settings_disabled_without_a_preset(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_CONFIG", raising=False)

    assert load_responder_settings().model == ""


def test_settings_read_the_model_from_the_preset(monkeypatch, tmp_path):
    preset = tmp_path / "preset.toml"
    preset.write_text('[voice.responder]\nmodel = " fast-model "\n', encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    assert load_responder_settings().model == "fast-model"


def test_preamble_flag_is_a_thread_safe_event():
    # The orchestrator reads it from the Agent's worker; the voice loop sets it.
    flag = threading.Event()
    context = AgentContext(metadata={PREAMBLE_SPOKEN: flag})
    flag.set()

    from openjarvis.agents.orchestrator import _preamble_spoken

    assert _preamble_spoken(context) is True
    assert _preamble_spoken(AgentContext()) is False
    assert _preamble_spoken(None) is False


@pytest.mark.anyio
async def test_responder_input_names_requests_in_progress():
    engine = _ToolEngine(StreamChunk(content="Dạ."))

    await VoiceResponder(engine, "fast").respond(
        "alo", "Assistant: Chào", in_progress=["thêm hai cà phê", "", "xem giỏ"]
    )

    assert engine.messages[-1].content == (
        "Conversation so far:\nAssistant: Chào\n"
        "Requests in progress: thêm hai cà phê; xem giỏ\n"
        "Customer (latest): alo"
    )

    await VoiceResponder(engine, "fast").respond("alo", in_progress=[""])

    assert engine.messages[-1].content == "Customer (latest): alo"


def test_core_default_prompt_mentions_requests_in_progress_without_business_words():
    from openjarvis.server.voice.responder import DEFAULT_PROMPT

    assert (
        "If requests are in progress and the latest words are only a hear check, "
        "a greeting or small talk, say you hear them and name what you are "
        "working on; do not ask what they would like."
    ) in " ".join(DEFAULT_PROMPT.split())
    for word in ("menu", "cart", "order", "payment", "kiosk", "price", "dish"):
        assert word not in DEFAULT_PROMPT.lower()


def test_core_default_prompt_carries_no_business_wording():
    from openjarvis.server.voice.responder import DEFAULT_PROMPT

    for word in ("menu", "cart", "order", "payment", "kiosk", "price", "dish"):
        assert word not in DEFAULT_PROMPT.lower()


def test_preset_prompt_file_replaces_the_default(monkeypatch, tmp_path):
    prompt = tmp_path / "responder.md"
    prompt.write_text("  Speak like a shop assistant. SKIP otherwise.\n", "utf-8")
    preset = tmp_path / "preset.toml"
    preset.write_text(
        f'[voice.responder]\nmodel = "fast"\nprompt_file = "{prompt}"\n', "utf-8"
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    settings = load_responder_settings()

    assert settings.prompt == "Speak like a shop assistant. SKIP otherwise."
    prompt.write_text("Changed wording.", "utf-8")
    assert load_responder_settings().prompt == "Changed wording."  # no restart


def test_without_a_prompt_file_the_default_is_used(monkeypatch, tmp_path):
    from openjarvis.server.voice.responder import DEFAULT_PROMPT

    preset = tmp_path / "preset.toml"
    preset.write_text('[voice.responder]\nmodel = "fast"\n', "utf-8")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    assert load_responder_settings().prompt == DEFAULT_PROMPT


@pytest.mark.parametrize("content", [None, "  \n"])
def test_unreadable_or_empty_prompt_file_is_an_error(monkeypatch, tmp_path, content):
    prompt = tmp_path / "responder.md"
    if content is not None:
        prompt.write_text(content, "utf-8")
    preset = tmp_path / "preset.toml"
    preset.write_text(
        f'[voice.responder]\nmodel = "fast"\nprompt_file = "{prompt}"\n', "utf-8"
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    with pytest.raises(ValueError, match="prompt_file"):
        load_responder_settings()


def test_shipped_preset_prompt_file_is_the_kiosk_wording(monkeypatch):
    monkeypatch.setenv(
        "OPENJARVIS_CONFIG", "configs/openjarvis/examples/ordering-kiosk-mcp.toml"
    )

    settings = load_responder_settings()

    assert settings.model
    assert "kiosk" in settings.prompt
    assert "getNextResponseFromSupervisor" in settings.prompt


def test_prompts_leave_repeat_and_explain_requests_to_the_agent():
    from openjarvis.server.voice.responder import DEFAULT_PROMPT

    shipped = Path("configs/openjarvis/prompts/voice-responder.md").read_text("utf-8")
    for prompt in (DEFAULT_PROMPT, shipped):
        lowered = " ".join(prompt.lower().split())
        # The Responder answers only greetings, thanks, hear checks and unclear
        # words; repeating or explaining goes to the Agent, which has the history.
        assert "asks to repeat or explain" not in lowered
        assert "for a repeat request" not in lowered
        assert "verbatim" not in lowered
        assert "repeat or explain" in lowered  # named under the tool-call rule


def test_prompts_delegate_through_the_tool_without_prefixes():
    from openjarvis.server.voice.responder import DEFAULT_PROMPT, DELEGATE_TOOL_NAME

    shipped = Path("configs/openjarvis/prompts/voice-responder.md").read_text("utf-8")
    for prompt in (DEFAULT_PROMPT, shipped):
        assert DELEGATE_TOOL_NAME in prompt
        for prefix in ("SKIP", "SAY:", "ANSWER:"):
            assert prefix not in prompt


class _SlowAgent:
    """An Agent that would speak late, recording whether its run was closed."""

    model = "test-model"

    def __init__(
        self, *, tool_first: bool = False, after: float = 0.5, streams: bool = True
    ):
        self._tool_first = tool_first
        self._after = after
        self._streams = streams
        self.closed = False
        self.finished = False
        self.prompt = None

    def streams_tool_starts(self) -> bool:
        return self._streams

    async def run_stream(self, prompt, context):
        del context
        self.prompt = prompt
        from openjarvis.agents._stubs import AgentToolFinished, AgentToolStarted

        try:
            if self._tool_first:
                yield AgentToolStarted("display_cart")
                await asyncio.sleep(self._after)
                yield AgentToolFinished("display_cart", ok=True)
            else:
                await asyncio.sleep(self._after)
            yield AgentTextDelta("Câu trả lời của agent.")
            yield AgentRunCompleted(
                AgentResult(content="Câu trả lời của agent.", turns=1)
            )
            self.finished = True
        finally:
            self.closed = True


@pytest.mark.anyio
async def test_responder_sends_the_prompt_it_was_given():
    engine = _ToolEngine(StreamChunk(content="Dạ."))
    await VoiceResponder(engine, "fast", "Custom wording.").respond("hi")

    assert engine.messages[0].content == "Custom wording."


@pytest.mark.anyio
@pytest.mark.parametrize(
    "responder",
    [_Responder(None, kind="answer"), _Responder("x", fail=True)],
)
async def test_an_empty_or_failed_responder_leaves_only_the_agent_answer(responder):
    binding = _Binding("Dạ.", after=0.1)
    service = OpenJarvisLLMService(binding, responder=responder)

    frames = await _spoken(service, _user("ừ đúng rồi"))

    assert "".join(f.text for f in frames) == "Dạ."


@pytest.mark.anyio
async def test_agent_is_told_not_to_restate_the_acknowledged_request():
    from openjarvis.server.voice.responder import AGENT_NOTE

    with_filler = _Binding("Dạ.")
    await _spoken(
        OpenJarvisLLMService(with_filler, responder=_Responder("Mình xem menu nhé.")),
        _user("menu"),
    )
    without = _Binding("Dạ.")
    await _spoken(OpenJarvisLLMService(without), _user("menu"))

    texts = [m.content for m in with_filler.context.conversation.messages]
    assert texts[1] == AGENT_NOTE
    assert AGENT_NOTE not in [m.content for m in without.context.conversation.messages]


@pytest.mark.anyio
async def test_the_agent_starts_only_after_the_delegation_with_the_filler_first():
    from openjarvis.server.voice.responder import AGENT_NOTE

    responder = _Responder("Mình mở menu cho bạn nhé.", after=0.2)
    binding = _Binding("Menu đây ạ.")
    service = OpenJarvisLLMService(binding, responder=responder)

    frames = await _spoken(service, _user("cho mình xem menu"))

    assert frames[0].text == "Mình mở menu cho bạn nhé. "
    assert frames[0].metadata.get(FLUSH_TTS_METADATA) is True
    assert "".join(f.text for f in frames) == "Mình mở menu cho bạn nhé. Menu đây ạ."
    assert binding.started_at >= responder.delegated_at
    assert binding.context.metadata[PREAMBLE_SPOKEN].is_set()
    assert binding.context.conversation.messages[1].content == AGENT_NOTE


@pytest.mark.anyio
async def test_an_answer_never_starts_the_agent():
    agent = _SlowAgent()
    service = OpenJarvisLLMService(
        agent, responder=_Responder("Dạ, mình nghe rõ ạ.", kind="answer")
    )

    frames = await _spoken(service, _user("em nghe rõ không"))

    assert "".join(f.text for f in frames) == "Dạ, mình nghe rõ ạ. "
    assert agent.prompt is None


@pytest.mark.anyio
async def test_a_delegation_without_a_filler_runs_the_agent_without_the_note():
    from openjarvis.agents.orchestrator import _preamble_spoken
    from openjarvis.server.voice.responder import AGENT_NOTE

    binding = _Binding("Dạ, bạn muốn thanh toán bằng QR ạ?")
    service = OpenJarvisLLMService(binding, responder=_Responder(""))

    frames = await _spoken(service, _user("thôi khỏi"))

    assert "".join(f.text for f in frames) == "Dạ, bạn muốn thanh toán bằng QR ạ?"
    assert _preamble_spoken(binding.context) is False
    texts = [m.content for m in binding.context.conversation.messages]
    assert AGENT_NOTE not in texts


@pytest.mark.anyio
async def test_a_responder_past_the_deadline_is_dropped_and_the_agent_runs(
    monkeypatch,
):
    monkeypatch.setattr("openjarvis.server.voice.llm.RESPONDER_DECISION_SECONDS", 0.1)
    responder = _Responder("Chào bạn.", kind="answer", after=0.5)
    binding = _Binding("Dạ, quán mở đến 22h.")
    service = OpenJarvisLLMService(binding, responder=responder)

    frames = await _spoken(service, _user("quán mở tới mấy giờ"))

    assert "".join(f.text for f in frames) == "Dạ, quán mở đến 22h."


@pytest.mark.anyio
async def test_late_delegation_can_acknowledge_while_the_agent_is_working(monkeypatch):
    monkeypatch.setattr("openjarvis.server.voice.llm.RESPONDER_DECISION_SECONDS", 0.02)
    agent_started = asyncio.Event()
    line = "Mình đang xem lại menu nhé."

    class Engine(_ToolEngine):
        closed = False

        async def stream_full(self, messages, **kwargs):
            try:
                yield StreamChunk(content=line)
                # Real failure shape: text is buffered, tool call misses the
                # decision deadline. No paid provider or merchant call here.
                await agent_started.wait()
                yield _call()
                await asyncio.Event().wait()
            finally:
                self.closed = True

    class Binding(_Binding):
        acknowledged_at_start = None

        async def run_stream(self, prompt, context):
            marker = context.metadata.get(PREAMBLE_SPOKEN)
            self.acknowledged_at_start = marker is not None and marker.is_set()
            agent_started.set()
            async for event in super().run_stream(prompt, context):
                yield event

    engine = Engine()
    binding = Binding("Menu đã mở.", after=0.1)
    service = OpenJarvisLLMService(
        binding, responder=VoiceResponder(engine, "fast-model")
    )

    frames = await _spoken(service, _user("mở lại menu"))

    assert "".join(f.text for f in frames) == f"{line} Menu đã mở."
    assert frames[0].metadata.get(FLUSH_TTS_METADATA) is True
    assert binding.acknowledged_at_start is False
    assert binding.context.metadata[PREAMBLE_SPOKEN].is_set()
    assert engine.closed


@pytest.mark.anyio
@pytest.mark.parametrize("late_result", ["answer", "error", "never"])
async def test_fallback_ignores_late_answers_or_failures_and_closes_the_stream(
    monkeypatch, late_result
):
    monkeypatch.setattr("openjarvis.server.voice.llm.RESPONDER_DECISION_SECONDS", 0.02)
    agent_started = asyncio.Event()

    class Engine(_ToolEngine):
        closed = False

        async def stream_full(self, messages, **kwargs):
            try:
                await agent_started.wait()
                if late_result == "never":
                    await asyncio.Event().wait()  # Agent finishes first.
                if late_result == "error":
                    raise RuntimeError("provider unavailable after fallback")
                yield StreamChunk(content="Mình nghe rõ bạn nhé.")
            finally:
                self.closed = True

    class Binding(_Binding):
        async def run_stream(self, prompt, context):
            agent_started.set()
            async for event in super().run_stream(prompt, context):
                yield event

    engine = Engine()
    binding = Binding("Menu đã mở.", after=0.05)
    service = OpenJarvisLLMService(
        binding, responder=VoiceResponder(engine, "fast-model")
    )

    frames = await _spoken(service, _user("mở menu"))

    assert "".join(f.text for f in frames) == "Menu đã mở."
    assert engine.closed
    assert not binding.context.metadata[PREAMBLE_SPOKEN].is_set()


@pytest.mark.anyio
async def test_interruption_after_fallback_closes_the_responder_and_agent(monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr("openjarvis.server.voice.llm.RESPONDER_DECISION_SECONDS", 0.02)
    agent_started = asyncio.Event()
    frames = []

    class Engine(_ToolEngine):
        closed = False

        async def stream_full(self, messages, **kwargs):
            try:
                yield StreamChunk(content="Mình đang xem menu nhé.")
                await asyncio.Event().wait()
            finally:
                self.closed = True

    class Binding(_Binding):
        closed = False

        async def run_stream(self, prompt, context):
            try:
                agent_started.set()
                await asyncio.Event().wait()
                yield AgentTextDelta("Unreachable")
            finally:
                self.closed = True

    engine, binding = Engine(), Binding("")
    service = OpenJarvisLLMService(
        binding, responder=VoiceResponder(engine, "fast-model")
    )

    async def capture(frame):
        frames.append(frame)

    service.push_frame = capture
    task = asyncio.create_task(
        service._answer(
            "mở menu", AgentContext(), nullcontext(), preamble=("mở menu", "")
        )
    )
    try:
        await asyncio.wait_for(agent_started.wait(), timeout=1)
        service._turn_state.interrupt()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert not any(isinstance(frame, LLMTextFrame) for frame in frames)
    assert engine.closed and binding.closed


@pytest.mark.anyio
async def test_late_filler_does_not_follow_an_agent_fragment_waiting_for_projection(
    monkeypatch,
):
    monkeypatch.setattr("openjarvis.server.voice.llm.RESPONDER_DECISION_SECONDS", 0.02)
    first_fragment = asyncio.Event()

    class Engine(_ToolEngine):
        async def stream_full(self, messages, **kwargs):
            await first_fragment.wait()
            yield StreamChunk(content="Mình đang xem menu nhé.")
            yield _call()

    class Binding(_Binding):
        async def run_stream(self, prompt, context):
            self.context = context
            # Incomplete markdown is buffered by the real speech projector.
            yield AgentTextDelta("**Menu")
            first_fragment.set()
            await asyncio.sleep(0.05)
            yield AgentTextDelta("** đã mở.")
            yield AgentRunCompleted(AgentResult(content="Menu đã mở.", turns=1))

    binding = Binding("")
    service = OpenJarvisLLMService(
        binding, responder=VoiceResponder(Engine(), "fast-model")
    )
    frames = await _spoken(service, _user("mở menu"))

    assert "".join(f.text for f in frames) == "Menu đã mở."
    assert not binding.context.metadata[PREAMBLE_SPOKEN].is_set()


@pytest.mark.anyio
async def test_a_turn_interrupted_before_the_delegation_lands_runs_the_agent_silently():
    agent = _SlowAgent(after=0.0)
    holder: dict = {}
    responder = _Responder(
        "Mình mở menu nhé.",
        during=lambda: holder["service"]._turn_state.interrupt(),
    )
    service = OpenJarvisLLMService(agent, responder=responder)
    holder["service"] = service

    frames = await _spoken(service, _user("cho mình xem menu"))

    # A barge-in fences speech, not work: the delegated request still runs.
    assert frames == []
    assert agent.prompt == "cho mình xem menu"


@pytest.mark.anyio
async def test_an_interruption_while_the_responder_decides_speaks_nothing():
    agent = _SlowAgent(after=0.0)
    responder = _Responder("Mình mở menu nhé.", after=1.0)
    service = OpenJarvisLLMService(agent, responder=responder)
    seen: list[LLMTextFrame] = []

    class _Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMTextFrame):
                seen.append(frame)
            await self.push_frame(frame, direction)

    worker = PipelineWorker(
        Pipeline([service, _Sink()]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def drive():
        await asyncio.sleep(0.01)
        await worker.queue_frame(
            LLMContextFrame(LLMContext(messages=_user("cho mình xem menu")))
        )
        for _ in range(100):
            await asyncio.sleep(0.01)
            if responder.calls:
                break
        await worker.queue_frame(InterruptionFrame())
        await asyncio.sleep(1.3)
        await worker.queue_frame(EndFrame())

    await runner.add_workers(worker)
    await asyncio.wait_for(
        asyncio.gather(runner.run(), drive()), timeout=_TURN_TIMEOUT_S
    )

    assert seen == []
    # The delegation that lands after the barge-in still runs the Agent.
    assert agent.prompt == "cho mình xem menu" and agent.finished


class _SelfCancellingResponder(_Responder):
    """A Responder whose own task ends cancelled, as a library may do inside."""

    async def respond(self, utterance, conversation="", on_delegate=None):
        self.calls.append((utterance, conversation))
        raise asyncio.CancelledError()


@pytest.mark.anyio
async def test_a_responder_task_that_ends_cancelled_is_routed_to_the_agent():
    binding = _Binding("Dạ, menu đây ạ.")
    service = OpenJarvisLLMService(binding, responder=_SelfCancellingResponder(None))

    frames = await _spoken(service, _user("cho mình xem menu"))

    assert "".join(f.text for f in frames) == "Dạ, menu đây ạ."


@pytest.mark.anyio
async def test_the_responder_decides_at_temperature_zero():
    # User decision 2026-10-14: the same request gets the same route and wording.
    engine = _ToolEngine(StreamChunk(content="Dạ."))

    await VoiceResponder(engine, "fast").respond("menu")

    assert engine.kwargs["temperature"] == 0


def test_prompts_delegate_repeat_and_explain_requests():
    # User decision 2026-10-14: the Agent has the full history, so it repeats
    # and explains; the Responder answered repeats wrongly in eval runs 5 and 7.
    from openjarvis.server.voice.responder import DEFAULT_PROMPT

    shipped = Path("configs/openjarvis/prompts/voice-responder.md").read_text("utf-8")
    for prompt in (DEFAULT_PROMPT, shipped):
        delegated, _, answered = prompt.partition("Answer yourself")
        assert "repeat" in delegated.lower()
        assert answered
        assert "repeat" not in answered.lower().replace("them to say it again", "")
        assert "verbatim" not in answered.lower()
        assert "explain" not in answered.lower()


# --- late-result phrasing (spec 2026-10-17 barge-in §5.6) -------------------


@pytest.mark.anyio
async def test_report_sends_the_report_prompt_and_event_without_tools():
    engine = _ToolEngine(StreamChunk(content="Đơn đã tạo."))
    responder = VoiceResponder(engine, "fast", "Filler.", "Report wording.")

    line = await responder.report("E", "Customer: hủy đơn")

    assert line == "Đơn đã tạo."
    assert engine.messages[0].content == "Report wording."
    assert engine.messages[1].content == (
        "Conversation so far:\nCustomer: hủy đơn\nKiosk event: E"
    )
    assert "tools" not in engine.kwargs
    assert engine.kwargs["temperature"] == 0
    assert engine.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.anyio
async def test_report_folds_line_breaks_into_one_line():
    engine = _ToolEngine(StreamChunk(content="Đơn đã tạo.\nQuét QR."))

    line = await VoiceResponder(engine, "fast").report("E")

    assert line == "Đơn đã tạo. Quét QR."
    assert engine.messages[1].content == "Kiosk event: E"


def test_core_default_report_prompt_carries_no_business_wording():
    from openjarvis.server.voice.responder import DEFAULT_REPORT_PROMPT

    for word in ("menu", "cart", "order", "payment", "kiosk", "price", "dish"):
        assert word not in DEFAULT_REPORT_PROMPT.lower()


def test_preset_report_prompt_file_is_loaded(monkeypatch, tmp_path):
    from openjarvis.server.voice.responder import DEFAULT_REPORT_PROMPT

    prompt = tmp_path / "reporter.md"
    prompt.write_text("  Report like a shop assistant.\n", "utf-8")
    preset = tmp_path / "preset.toml"
    preset.write_text(
        f'[voice.responder]\nmodel = "fast"\nreport_prompt_file = "{prompt}"\n',
        "utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    assert load_responder_settings().report_prompt == "Report like a shop assistant."
    preset.write_text('[voice.responder]\nmodel = "fast"\n', "utf-8")
    assert load_responder_settings().report_prompt == DEFAULT_REPORT_PROMPT


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("3", "report_prompt_file_must_be_a_path"),
        ('"MISSING"', "report_prompt_file_unreadable"),
        ('"EMPTY"', "report_prompt_file_empty"),
    ],
)
def test_bad_report_prompt_file_is_an_error(monkeypatch, tmp_path, value, error):
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", "utf-8")
    value = value.replace("MISSING", str(tmp_path / "nope.md"))
    value = value.replace("EMPTY", str(empty))
    preset = tmp_path / "preset.toml"
    preset.write_text(
        f'[voice.responder]\nmodel = "fast"\nreport_prompt_file = {value}\n', "utf-8"
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    with pytest.raises(ValueError, match=error):
        load_responder_settings()


def test_shipped_reporter_prompt_never_claims_cancellation():
    shipped = Path("configs/openjarvis/prompts/voice-reporter.md").read_text("utf-8")
    lowered = " ".join(shipped.lower().split())

    assert "expires" in lowered  # an unpaid order expires by itself
    assert "never say it was cancelled or stopped" in lowered
    assert "do not ask the customer to repeat" in lowered


def test_shipped_kiosk_prompt_states_that_created_orders_cannot_be_cancelled():
    shipped = Path("configs/openjarvis/prompts/ordering-kiosk.md").read_text("utf-8")
    lowered = " ".join(shipped.lower().split())

    assert "cannot cancel an order it has created" in lowered
    assert "an unpaid order expires by itself" in lowered
    assert "never say it was cancelled or stopped" in lowered
    assert "cannot be stopped" in lowered
    # Ruling 16: only a checkout that started ordering; a waiting one is
    # dropped by the change, which is still carried out.
    assert "only a checkout that has started ordering cannot be stopped" in lowered
    assert "is still carried out: it drops a checkout that is still waiting" in (
        lowered
    )


def test_preset_points_the_responder_at_the_reporter_prompt(monkeypatch):
    preset = Path("configs/openjarvis/examples/ordering-kiosk-mcp.toml")
    with preset.open("rb") as handle:
        section = tomllib.load(handle)["voice"]["responder"]
    assert Path(section["report_prompt_file"]).is_file()

    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    assert "expires" in load_responder_settings().report_prompt


# --- shipped merchant wording for parallel runs (spec 2026-10-17 barge-in) --


def _shipped(name: str) -> str:
    text = Path(f"configs/openjarvis/prompts/{name}").read_text("utf-8")
    return " ".join(text.lower().split())


def test_kiosk_prompt_handles_unsettled_and_changed_checkouts():
    lowered = _shipped("ordering-kiosk.md")

    assert "`cart_not_settled`" in lowered
    assert "still being updated" in lowered
    assert "ask whether to wait" in lowered
    assert "never say an order was made" in lowered
    assert "`cart_changed_during_checkout`" in lowered
    assert "then ask whether to pay" in lowered


def test_kiosk_prompt_waits_before_correcting_in_progress_work():
    lowered = _shipped("ordering-kiosk.md")

    assert "`<requests_in_progress>`" in lowered
    assert "`await_earlier_requests`" in lowered
    assert "không lấy sinh tố nữa" in lowered
    assert "which item" in lowered


def test_responder_prompt_never_asks_what_they_want_while_work_is_in_progress():
    lowered = _shipped("voice-responder.md")

    assert "requests in progress" in lowered
    assert "say that you can hear them" in lowered
    assert "name what you are working on" in lowered
    assert "i hear you" not in lowered
    assert 'never "bạn cần gì?"' in lowered


def test_reporter_prompt_does_not_repeat_an_answered_question():
    lowered = _shipped("voice-reporter.md")

    assert "whole request" in lowered
    assert "already answered" in lowered
    assert "do not ask it again" in lowered
    assert "state what was done" in lowered


def test_preset_runs_two_agents_and_enables_await_earlier_requests():
    preset = tomllib.loads(
        Path("configs/openjarvis/examples/ordering-kiosk-mcp.toml").read_text("utf-8")
    )

    assert preset["agent"]["max_concurrent_runs"] == 2
    assert "await_earlier_requests" in preset["tools"]["enabled"].split(",")
