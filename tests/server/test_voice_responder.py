"""A fast first sentence races the Agent's first text (chat-supervisor pattern)."""

from __future__ import annotations

import asyncio
import threading

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import EndFrame, LLMContextFrame, LLMTextFrame
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
        kind: str = "preamble",
    ):
        self._line = line
        self._after = after
        self._fail = fail
        self._kind = kind
        self.calls: list[tuple[str, str]] = []

    async def acknowledgement(
        self, utterance: str, conversation: str = ""
    ) -> ResponderReply | None:
        self.calls.append((utterance, conversation))
        await asyncio.sleep(self._after)
        if self._fail:
            raise RuntimeError("provider down")
        return ResponderReply(self._kind, self._line) if self._line else None


class _Binding:
    model = "test-model"

    def __init__(self, text: str, *, after: float = 0.0):
        self._text = text
        self._after = after
        self.context: AgentContext | None = None

    def with_model(self, model):
        binding = _Binding(self._text, after=self._after)
        binding.model = model
        return binding

    async def run_stream(self, prompt, context):
        del prompt
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
async def test_preamble_is_dropped_when_the_agent_speaks_first():
    responder = _Responder("Dạ, để em kiểm tra ạ.", after=0.3)
    binding = _Binding("Xin chào anh.")
    service = OpenJarvisLLMService(binding, responder=responder)

    frames = await _spoken(service, _user("chào em"))

    assert "".join(f.text for f in frames) == "Xin chào anh."
    assert not binding.context.metadata[PREAMBLE_SPOKEN].is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("responder", [_Responder(None), _Responder("x", fail=True)])
async def test_skip_or_failure_leaves_only_the_agent_answer(responder):
    binding = _Binding("Dạ.", after=0.1)
    service = OpenJarvisLLMService(binding, responder=responder)

    frames = await _spoken(service, _user("ừ đúng rồi"))

    assert "".join(f.text for f in frames) == "Dạ."


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


class _Engine:
    def __init__(self, *parts: str):
        self.parts = parts
        self.kwargs = None

    def reasoning_off_options(self, model):
        return {"extra_body": {"thinking": {"type": "disabled"}}}

    async def stream_full(self, messages, **kwargs):
        self.kwargs = kwargs
        self.messages = messages
        for part in self.parts:
            yield StreamChunk(content=part)
        yield StreamChunk(finish_reason="stop")


@pytest.mark.anyio
async def test_acknowledgement_is_one_line_with_reasoning_off():
    engine = _Engine("  Dạ, để em ", "xem ạ.\nextra")
    reply = await VoiceResponder(engine, "fast").acknowledgement(
        "menu có gì", "Assistant: Chào"
    )

    assert reply == ResponderReply("preamble", "Dạ, để em xem ạ.")
    assert engine.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert engine.messages[-1].content == (
        "Conversation so far:\nAssistant: Chào\nCustomer (latest): menu có gì"
    )


@pytest.mark.anyio
@pytest.mark.parametrize("parts", [("SKIP",), ("skip.",), ("",), ("  ",)])
async def test_skip_or_empty_means_say_nothing(parts):
    assert await VoiceResponder(_Engine(*parts), "fast").acknowledgement("ừ") is None


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
async def test_agent_is_told_not_to_restate_the_acknowledged_request():
    from openjarvis.server.voice.responder import AGENT_NOTE

    with_responder = _Binding("Dạ.")
    await _spoken(
        OpenJarvisLLMService(with_responder, responder=_Responder(None)),
        _user("menu"),
    )
    without = _Binding("Dạ.")
    await _spoken(OpenJarvisLLMService(without), _user("menu"))

    texts = [m.content for m in with_responder.context.conversation.messages]
    assert texts[1] == AGENT_NOTE
    assert AGENT_NOTE not in [m.content for m in without.context.conversation.messages]


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


@pytest.mark.anyio
async def test_responder_sends_the_prompt_it_was_given():
    engine = _Engine("Dạ.")
    await VoiceResponder(engine, "fast", "Custom wording.").acknowledgement("hi")

    assert engine.messages[0].content == "Custom wording."


def test_shipped_preset_prompt_file_is_the_kiosk_wording(monkeypatch):
    monkeypatch.setenv(
        "OPENJARVIS_CONFIG", "configs/openjarvis/examples/ordering-kiosk-mcp.toml"
    )

    settings = load_responder_settings()

    assert settings.model
    assert "kiosk" in settings.prompt and "SKIP" in settings.prompt


@pytest.mark.anyio
async def test_answer_prefix_marks_a_reply_that_handles_the_turn():
    reply = await VoiceResponder(
        _Engine("ANSWER: Dạ, mình nghe rõ ạ."), "fast"
    ).acknowledgement("nghe rõ không")

    assert reply == ResponderReply("answer", "Dạ, mình nghe rõ ạ.")


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
async def test_direct_answer_ends_the_turn_before_the_agent_speaks():
    agent = _SlowAgent()
    service = OpenJarvisLLMService(
        agent, responder=_Responder("Dạ, mình nghe rõ ạ.", kind="answer")
    )

    frames = await _spoken(service, _user("em nghe rõ không"))

    assert "".join(f.text for f in frames) == "Dạ, mình nghe rõ ạ. "
    assert agent.closed and not agent.finished
    # Answered, not interrupted: nothing is carried into the next turn.
    assert service._unfinished == []


@pytest.mark.anyio
async def test_direct_answer_is_dropped_once_the_agent_started_a_tool():
    agent = _SlowAgent(tool_first=True, after=0.3)
    service = OpenJarvisLLMService(
        agent,
        responder=_Responder("Dạ, mình nghe rõ ạ.", kind="answer", after=0.1),
    )

    frames = await _spoken(service, _user("cho mình xem giỏ"))

    assert "".join(f.text for f in frames) == "Câu trả lời của agent."
    assert agent.finished


@pytest.mark.anyio
async def test_direct_answer_is_dropped_when_the_agent_spoke_first():
    agent = _SlowAgent(after=0.0)
    service = OpenJarvisLLMService(
        agent,
        responder=_Responder("Dạ, mình nghe rõ ạ.", kind="answer", after=0.3),
    )

    frames = await _spoken(service, _user("chào em"))

    assert "".join(f.text for f in frames) == "Câu trả lời của agent."


@pytest.mark.anyio
async def test_a_turn_carrying_an_interrupted_request_is_never_answered():
    # Live 2026-10-13 18:05: "Iced coffee." was interrupted; the next turn's
    # garbled words got "please say it again" and the order was dropped.
    agent = _SlowAgent(after=0.3)
    service = OpenJarvisLLMService(
        agent,
        responder=_Responder("Bạn nói lại giúp mình nhé.", kind="answer"),
    )
    service._unfinished = ["Iced coffee."]

    frames = await _spoken(service, _user("ơ alo"))

    assert "".join(f.text for f in frames) == "Câu trả lời của agent."
    assert agent.finished and "Iced coffee." in agent.prompt


@pytest.mark.anyio
async def test_an_agent_that_does_not_report_tools_first_is_never_answered():
    # Its tools may already be running while it has reported nothing.
    agent = _SlowAgent(after=0.3, streams=False)
    service = OpenJarvisLLMService(
        agent, responder=_Responder("Dạ, mình nghe rõ ạ.", kind="answer")
    )

    frames = await _spoken(service, _user("em nghe rõ không"))

    assert "".join(f.text for f in frames) == "Câu trả lời của agent."
    assert agent.finished


@pytest.mark.anyio
async def test_a_blocked_answer_still_lets_a_preamble_through():
    agent = _SlowAgent(after=0.3)
    service = OpenJarvisLLMService(
        agent, responder=_Responder("Mình xem món cho bạn nhé.")
    )
    service._unfinished = ["Iced coffee."]

    frames = await _spoken(service, _user("ơ alo"))

    assert frames[0].text == "Mình xem món cho bạn nhé. "
    assert agent.finished
