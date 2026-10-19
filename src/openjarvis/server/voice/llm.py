"""The OpenJarvis native Agent, as a Pipecat LLM service."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import aclosing, asynccontextmanager, nullcontext
from typing import Any, Literal

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings

from openjarvis.agents._stubs import (
    CUSTOMER_REQUEST,
    PREAMBLE_SPOKEN,
    RUN_SPEAKING,
    AgentContext,
    AgentTextDelta,
    AgentToolFinished,
    AgentToolStarted,
)
from openjarvis.core.conversation import (
    current_conversation_id,
    uncertain_speaker_scope,
)
from openjarvis.core.runs import RUN_TRACKER, voice_run_scope
from openjarvis.core.types import Conversation, Message, Role, ToolCall
from openjarvis.server.voice.runtime import VOICE_SYSTEM_PROMPT
from openjarvis.server.voice.text import normalize_speech_text
from openjarvis.server.voice.tts import FLUSH_TTS_METADATA

logger = logging.getLogger("openjarvis.server.voice")


# Prepended to the customer's words when the speaker gate could not confirm
# who said them. ToolExecutor enforces the same rule; this line only makes
# the Agent ask before it tries a tool that would be refused.
UNCERTAIN_SPEAKER_NOTE = (
    "[Speaker unconfirmed: this may not be the customer at the kiosk. "
    "Confirm with the customer before any cart, order or payment change.]"
)


# Told to the Agent after the requests still in progress (spec 2026-10-17
# barge-in §5.4). Rules any assistant shares; no business facts.
IN_PROGRESS_RULE = (
    "The requests in progress are already being handled in the background; do "
    "not perform them again. If the latest request asks about them, say they "
    "are still in progress unless background_actions shows their outcome. If "
    "it changes or removes what one of them adds, call await_earlier_requests "
    "first and then act on the real result; if it is unclear which item the "
    "customer means, ask before changing anything."
)


def inflight_prompt(in_progress: Sequence[str], latest: str) -> str:
    """The Agent's prompt: the latest request, told what is already in flight.

    A request with no words (a turn whose speaker was not confirmed) is not
    named, so the Agent is never told to wait on or avoid words it was not given.
    """
    pending = [request for request in in_progress if request]
    if not pending:
        return latest
    return (
        "<requests_in_progress>\n" + "\n".join(pending) + "\n</requests_in_progress>\n"
        f"<latest_request>{latest}</latest_request>\n"
        f"{IN_PROGRESS_RULE}"
    )


# How long the Responder may take to delegate or answer before the Agent starts
# without it. Measured 2026-10-14 (deepseek-flash, tool call): offline max
# 1.77 s; live full line max 2.12 s.
RESPONDER_DECISION_SECONDS = 2.0

# How long the fast model may take to phrase a late result before the Agent's
# event turn reports it instead (spec 2026-10-17 barge-in §5.6; target: first
# audio within 1.5 s at p50).
PHRASE_TIMEOUT_SECONDS = 3.0

# How long the session's end waits for its cancelled runs to unwind. A run
# stuck in a step that ignores cancellation must not hold the pipeline's end.
CLEANUP_WAIT_SECONDS = 5.0


class VoiceTurnState:
    """Session-local generation identity used to fence interrupted turns."""

    def __init__(self) -> None:
        self._active_turn_id: int | None = None
        self.turn_id = 0

    @property
    def active_turn_id(self) -> int | None:
        return self._active_turn_id

    def begin_turn(self) -> int:
        self.turn_id += 1
        self._active_turn_id = self.turn_id
        return self.turn_id

    def interrupt(self) -> None:
        self._active_turn_id = None

    def is_active(self, turn_id: int) -> bool:
        return self._active_turn_id == turn_id


def message_text(message: Any) -> str:
    """Read the text out of one Pipecat context message.

    Content is a plain string for text turns and a list of parts once images or
    audio are involved; only the text parts mean anything to the Agent.
    """
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


class _SpeechProjector:
    """Strip markdown from a stream while preserving word boundaries.

    normalize_speech_text removes presentation syntax but also trims each
    call's edges, so calling it per token would eat the spaces between words
    ("Xin chào" -> "Xinchào"). Instead the trailing word is held back and
    only emitted once the next token proves it is complete, so every emitted
    piece ends at a word boundary and the spaces survive. An unfinished
    delimiter can only leak if it spans several words unclosed, which the
    Agent does not do.
    """

    def __init__(self) -> None:
        self._raw = ""
        self._emitted = ""

    def push(self, text: str) -> str | None:
        self._raw += text
        normalized = normalize_speech_text(self._raw)
        if not normalized.startswith(self._emitted):
            # normalize_speech_text re-parses the whole buffer, so text already
            # spoken can change shape as the markdown around it completes —
            # markdown-it appends a "." to a heading or list item, and that "."
            # moves as the item grows. Resync to the same offset. Resetting to
            # zero instead re-emits the buffer from its first word, which in
            # one measured response spoke the same answer 13 times.
            self._emitted = normalized[: len(self._emitted)]
        candidate = normalized[len(self._emitted) :]
        last_space = candidate.rfind(" ")
        if last_space == -1:
            return None
        stable = candidate[: last_space + 1]
        self._emitted += stable
        return stable

    def finish(self) -> str | None:
        normalized = normalize_speech_text(self._raw)
        tail = normalized[len(self._emitted) :]
        self._raw = ""
        self._emitted = ""
        return tail or None


def recent_conversation(
    context: Any, *, max_messages: int = 8, max_chars: int = 1200
) -> str:
    """The exchange before the customer's latest words, oldest first, or ''.

    Bounded so the fast voice stays fast: each line keeps its end (what a
    confirmation answers), and the whole keeps its newest part.
    """
    messages = list(context.get_messages())
    while messages and messages[-1].get("role") == "user":
        messages.pop()
    lines = []
    for message in messages[-max_messages:]:
        role = message.get("role")
        text = message_text(message).strip() if role in ("user", "assistant") else ""
        if text:
            speaker = "Customer" if role == "user" else "Assistant"
            lines.append(f"{speaker}: {text[-200:]}")
    return "\n".join(lines)[-max_chars:]


def agent_input(
    context: Any,
    recall: tuple[Any, Any] | None = None,
    *,
    max_history_tokens: int = 4096,
) -> tuple[str, AgentContext]:
    """Split a Pipecat context into the Agent's prompt and its history.

    The Agent takes the latest user turn as its input and everything before it
    as conversation history, which is how every other OpenJarvis caller invokes
    it. When ``recall`` is given it is ``(backend, ContextConfig)`` and the
    same passages the chat path retrieves are prepended to the history.
    """
    messages = list(context.get_messages())
    prompt = ""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "user":
            prompt = message_text(messages[index])
            messages = messages[:index]
            break

    history = [
        Message(
            role=Role(role),
            content=message_text(message),
            name=message.get("name"),
            tool_call_id=message.get("tool_call_id"),
            tool_calls=[
                ToolCall(
                    id=call.get("id", ""),
                    name=call.get("function", {}).get("name", ""),
                    arguments=call.get("function", {}).get("arguments", "{}"),
                )
                for call in message.get("tool_calls", []) or []
            ]
            or None,
            metadata=dict(message.get("metadata", {}) or {}),
        )
        for message in messages
        if (role := message.get("role")) in Role._value2member_map_
    ]
    systems = [m for m in history if m.role == Role.SYSTEM]
    turns: list[list[Message]] = []
    for message in history:
        if message.role == Role.SYSTEM:
            continue
        if message.role == Role.USER or not turns:
            turns.append([])
        turns[-1].append(message)
    kept: list[Message] = []
    tokens = 0
    for turn in reversed(turns):
        cost = sum(
            len(m.text.split())
            + sum(len(call.arguments.split()) for call in m.tool_calls or [])
            for m in turn
        )
        # Keep the newest whole turn even when it alone exceeds the soft budget.
        if kept and tokens + cost > max_history_tokens:
            break
        kept = turn + kept
        tokens += cost
    history = systems + kept

    if recall is not None and prompt:
        backend, ctx_cfg = recall
        try:
            from openjarvis.tools.storage import context as context_module

            history = context_module.inject_context(
                prompt, history, backend, config=ctx_cfg
            )
        except Exception:
            # Recall is an enhancement; a dead memory backend must not cost
            # the user their answer.
            logger.warning("voice memory recall failed", exc_info=True)

    # The Agent answers screens by default: markdown, tables, numbered lists.
    # Spoken aloud that is syntax read out, and the projector has to strip it
    # after the fact. Telling the Agent it is speaking stops the markdown
    # being produced at all. inject_context prepends its own message, so this
    # must go on *after* recall runs — otherwise the recalled document lands
    # ahead of the instruction that says how to speak it.
    history = [Message(role=Role.SYSTEM, content=VOICE_SYSTEM_PROMPT), *history]

    return prompt, AgentContext(conversation=Conversation(messages=history))


def voice_activity_frame(
    phase: Literal["processing", "listening", "inference", "tool"],
    *,
    model: str | None = None,
    tool_name: str | None = None,
) -> OutputTransportMessageUrgentFrame:
    activity: dict[str, str] = {"type": "voice_activity", "phase": phase}
    if phase == "inference" and model:
        activity["model"] = model
    if phase == "tool" and tool_name:
        activity["tool_name"] = tool_name
    return OutputTransportMessageUrgentFrame(
        {"label": "rtvi-ai", "type": "server-message", "data": {"data": activity}}
    )


class _EventContext:
    """The session's messages plus one kiosk event, without writing to them."""

    def __init__(self, context: Any, event: str) -> None:
        self._context = context
        self._event = event

    def get_messages(self) -> list[dict[str, Any]]:
        return [*self._context.get_messages(), {"role": "user", "content": self._event}]


class OpenJarvisLLMService(LLMService):
    """Answer through the native Agent, which owns tools, memory, and policy.

    Every round's text is spoken as soon as the Agent releases it: text written
    alongside a tool call arrives before that tool starts, so TTS covers the
    tool's latency, and later rounds follow in order. The Agent releases each
    segment once; this service never buffers or replays drafts.
    """

    def __init__(
        self,
        binding: Any,
        *,
        recall: tuple[Any, Any] | None = None,
        turn_state: VoiceTurnState | None = None,
        speaker_tracker: Any | None = None,
        uncertain_allowed_tools: tuple[str, ...] = (),
        responder: Any | None = None,
        **kwargs: Any,
    ) -> None:
        # None, not unset: every field left NOT_GIVEN makes pipecat log an
        # ERROR at session start. The Agent owns every one of these knobs, so
        # the service itself has none to set.
        kwargs.setdefault(
            "settings",
            LLMSettings(
                model=None,
                system_instruction=None,
                temperature=None,
                max_tokens=None,
                top_p=None,
                top_k=None,
                frequency_penalty=None,
                presence_penalty=None,
                seed=None,
                filter_incomplete_user_turns=False,
                user_turn_completion_config=None,
            ),
        )
        super().__init__(**kwargs)
        self._binding = binding
        self._recall = recall
        self._recall_task: asyncio.Task | None = None
        self._turn_state = turn_state or VoiceTurnState()
        self._speaker_tracker = speaker_tracker
        self._uncertain_allowed_tools = uncertain_allowed_tools
        self._bot_speaking = False
        # A customer turn is between its frame and its run: stopping an event
        # turn is awaited there, and nothing else may start meanwhile.
        self._starting_run = False
        self._last_context: Any | None = None
        # Customer runs, owned by tasks that a barge-in never cancels: it only
        # fences their speech (spec 2026-10-17 parallel runs §7.1). Keyed by
        # the RunTracker run id; each run's turn id says whether it speaks.
        self._runs: dict[str, asyncio.Task[bool]] = {}
        self._run_turns: dict[str, int] = {}
        self._run_conversation = ""
        # A kiosk-event turn holds the Agent runtime until its model answers;
        # any customer activity or the session's end cuts it.
        self._event_task: asyncio.Task[Any] | None = None
        # The Responder (OpenAI's chat agent) decides first: it answers the turn
        # itself or delegates to the Agent by calling its one tool.
        self._responder = responder

    @property
    def _answering(self) -> bool:
        """True while some unfinished run still speaks, or one is being started."""
        return self._starting_run or any(
            not task.done()
            and self._turn_state.is_active(self._run_turns.get(run_id, -1))
            for run_id, task in self._runs.items()
        )

    async def stream_agent(
        self,
        prompt: str,
        context: AgentContext,
        *,
        turn_id: int | None = None,
        binding: Any | None = None,
        speech_started: asyncio.Event | None = None,
    ) -> AsyncGenerator[Frame, None]:
        """Speak each released round in order, flushing at tool boundaries.

        Each delta is projected into speech text before it leaves: the Agent
        answers the screen (markdown, bullet lists, emoji), and the VieNeu
        voice would read the syntax out loud. The projector holds back
        incomplete markdown so a partial ``**bo`` never reaches TTS, and
        flushes the remainder at a tool boundary or the end of the response.

        A fenced turn drains: the Agent stream is consumed to its end, so its
        tools and final answer complete, but nothing more is yielded.
        """
        projector = _SpeechProjector()
        round_spoken = False
        if turn_id is None:
            turn_id = self._turn_state.begin_turn()
        binding = binding or self._binding
        if self._turn_state.is_active(turn_id):
            yield voice_activity_frame("inference", model=binding.model)
        stream = binding.run_stream(prompt, context)
        # Consume in one Task so run/worker ContextVars remain attached across
        # yields. Closing the outer stream also closes its suspended binding.
        async with aclosing(stream):
            async for event in stream:
                if not self._turn_state.is_active(turn_id):
                    continue

                if isinstance(event, AgentToolStarted):
                    # The round's text is complete; speak its tail before the
                    # tool runs. A second start in the same round finds nothing.
                    speech = projector.finish()
                    if speech or round_spoken:
                        # TTS sentence aggregation holds the last sentence for
                        # lookahead text that will not come until the tool
                        # ends; ask it to synthesize what it holds now.
                        frame = LLMTextFrame(f"{speech} " if speech else " ")
                        frame.metadata[FLUSH_TTS_METADATA] = True
                        yield frame
                        round_spoken = False
                    yield voice_activity_frame("tool", tool_name=event.tool_name)
                elif isinstance(event, AgentToolFinished):
                    yield voice_activity_frame("inference", model=binding.model)
                elif isinstance(event, AgentTextDelta):
                    if speech_started is not None and event.content.strip():
                        speech_started.set()
                    speech = projector.push(event.content)
                    if speech:
                        round_spoken = True
                        yield LLMTextFrame(speech)
        speech = projector.finish()
        if speech and self._turn_state.is_active(turn_id):
            yield LLMTextFrame(speech)

    async def prepare_agent_input(self, context: Any) -> tuple[str, AgentContext]:
        """Recall off the audio loop, with one in-flight lookup per session."""
        prompt, result = agent_input(context)
        if self._recall is None or not prompt:
            return prompt, result
        started = time.perf_counter()
        outcome = "busy"
        if self._recall_task is None or self._recall_task.done():
            from openjarvis.tools.storage.context import inject_context

            backend, config = self._recall
            # Recall goes in its own message after the voice prompt, never
            # into it: it changes with each question, and the voice prompt is
            # part of the stable prompt the prompt cache has to match.
            self._recall_task = asyncio.create_task(
                asyncio.to_thread(inject_context, prompt, [], backend, config=config)
            )
            # A timed-out lookup may fail after the caller has moved on.
            self._recall_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
            try:
                recalled = await asyncio.wait_for(
                    asyncio.shield(self._recall_task), timeout=0.1
                )
                result.conversation.messages[1:1] = recalled
                outcome = "ok"
            except asyncio.TimeoutError:
                outcome = "timeout"
            except Exception:
                outcome = "error"
                logger.warning("voice memory recall failed", exc_info=True)
        if outcome != "ok":
            result.conversation.messages.insert(
                1,
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "Reference retrieval is unavailable for this turn. "
                        "Do not invent store-specific facts; use verified live "
                        "state or explain that the requested information cannot "
                        "be verified."
                    ),
                    metadata={"volatile": True},
                ),
            )
        logger.info(
            "voice_recall outcome=%s elapsed_ms=%.2f",
            outcome,
            (time.perf_counter() - started) * 1000,
        )
        return prompt, result

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Start a run for an aggregated context, or pass the frame along."""
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        if isinstance(frame, InterruptionFrame):
            # A barge-in stops speech, not work: every run's turn is fenced
            # and its Agent keeps running silently. Only an event turn is cut.
            self._turn_state.interrupt()
            self._cancel_event_turn()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._cancel_event_turn()
            self._cancel_runs()
        # First: the base class routes InterruptionFrame to
        # _handle_interruptions, which cancels in-flight tool calls. Skipping
        # it would lose barge-in while the Agent is inside a tool.
        await super().process_frame(frame, direction)

        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        self._last_context = frame.context
        latest, context = await self.prepare_agent_input(frame.context)
        if not latest:
            return
        prompt = latest
        preamble = (latest, recent_conversation(frame.context))
        authority = nullcontext()
        if self._speaker_tracker is not None:
            from openjarvis.server.voice.speaker import Verdict

            # REJECT should never get here (the stop strategy drops it), but
            # if it does it is not the customer's word: fail closed.
            # UNCERTAIN turns act with full authority, without asking.
            verdict = self._speaker_tracker.take_turn_verdict()
            if verdict is Verdict.REJECT:
                # Not the customer's request: no run names these words as in
                # progress, and the run records none as the customer's.
                latest = ""
                preamble = None  # nothing is said to words that may not be theirs
                prompt = f"{UNCERTAIN_SPEAKER_NOTE}\n\n{prompt}"
                authority = uncertain_speaker_scope(self._uncertain_allowed_tools)
        # Marked before the await: a report starting while the event turn
        # unwinds would otherwise run over this customer turn.
        self._starting_run = True
        try:
            # Free the runtime an event turn holds before asking for it.
            await self._stop_event_turn()
            # Pipecat cancels this task on the next barge-in; the run is not
            # awaited here, so it lives on in its own task.
            self._start_run(latest, prompt, context, authority, preamble)
        finally:
            self._starting_run = False

    def _start_run(
        self,
        latest: str,
        prompt: str,
        context: AgentContext,
        authority: Any,
        preamble: tuple[str, str] | None,
    ) -> asyncio.Task[bool]:
        """Answer one customer turn in a task that only the session's end cancels.

        Its turn begins now, so the newest request is the one that speaks; an
        earlier run still working is fenced and drains in the background.
        """
        conversation_id = current_conversation_id()
        if conversation_id:
            self._run_conversation = conversation_id
        run_id = RUN_TRACKER.begin(conversation_id, latest)
        # Taken here, where the run id is known, so the run is not its own
        # earlier request. A turn with no request of its own names nothing.
        in_progress = [
            request
            for request in RUN_TRACKER.in_flight(conversation_id, exclude=run_id)
            if request
        ]
        prompt = inflight_prompt(in_progress, prompt)
        turn_id = self._turn_state.begin_turn()

        async def run() -> bool:
            # Entered inside this task, so tools reached through to_thread
            # (which copies the context) see the run id.
            with voice_run_scope(run_id):
                return await self._answer(
                    prompt,
                    context,
                    authority,
                    preamble=preamble,
                    request=latest,
                    turn_id=turn_id,
                    in_progress=in_progress,
                )

        task = asyncio.create_task(run())
        self._runs[run_id] = task
        self._run_turns[run_id] = turn_id

        def finished(done: asyncio.Task[bool]) -> None:
            self._runs.pop(run_id, None)
            self._run_turns.pop(run_id, None)
            RUN_TRACKER.end(conversation_id, run_id)
            if not done.cancelled() and done.exception() is not None:
                logger.warning("voice run failed", exc_info=done.exception())

        task.add_done_callback(finished)
        return task

    def _cancel_runs(self) -> list[asyncio.Task[bool]]:
        """The session ended: cancel every run, background ones included."""
        tasks = [task for task in self._runs.values() if not task.done()]
        for task in tasks:
            task.cancel()
        RUN_TRACKER.close(self._run_conversation or current_conversation_id())
        return tasks

    async def cleanup(self) -> None:
        await super().cleanup()
        tasks = self._cancel_runs()
        if tasks:
            # Only their unwinding is awaited; their cancellation is expected.
            _, pending = await asyncio.wait(tasks, timeout=CLEANUP_WAIT_SECONDS)
            if pending:
                logger.warning(
                    "voice_runs_unwinding count=%d after %.1f s at session end",
                    len(pending),
                    CLEANUP_WAIT_SECONDS,
                )

    async def _answer(
        self,
        prompt: str,
        context: AgentContext,
        authority: Any,
        *,
        preamble: tuple[str, str] | None = None,
        request: str = "",
        binding: Any | None = None,
        turn_id: int | None = None,
        in_progress: Sequence[str] = (),
    ) -> bool:
        """Speak one turn; True when it ended normally and was not fenced.

        ``preamble`` is the customer's words and the recent conversation. With
        a Responder it answers or delegates first. At the decision deadline the
        Agent starts; a later delegation may still supply a filler before the
        Agent's first speech. Event turns pass none. ``request`` is the
        customer's words for a customer turn, recorded with its run. ``in_progress``
        is what other runs were still handling when this one began.

        A turn fenced by a barge-in or a newer request speaks nothing more, but
        a delegated run still runs its Agent to the end.
        """
        if self._bot_speaking:
            # A newer customer request is being answered: the reply still
            # being spoken is stale (live crowd retest 2026-09-28: the bot
            # read an old answer for 40 s while new results showed). Stop it
            # downstream only; the user aggregator and this turn continue.
            await self.push_frame(InterruptionFrame())
        if turn_id is None:
            turn_id = self._turn_state.begin_turn()
        if request:
            context.metadata[CUSTOMER_REQUEST] = request
        context.metadata[RUN_SPEAKING] = lambda: self._turn_state.is_active(turn_id)

        completed = False
        with authority:
            try:
                await self.push_frame(voice_activity_frame("processing"))
                await self.push_frame(LLMFullResponseStartFrame())
                await self.start_processing_metrics()
                decision = (
                    self._ask_responder(*preamble, in_progress=in_progress)
                    if preamble is not None and self._responder is not None
                    else nullcontext(("agent", "", None))
                )
                async with decision as (route, text, late_filler):
                    speaking = self._turn_state.is_active(turn_id)
                    acknowledged = bool(text) or late_filler is not None
                    if speaking and route == "agent" and acknowledged:
                        from openjarvis.server.voice.responder import AGENT_NOTE

                        # The note must reach inference before it starts;
                        # the shared marker changes only when speech is queued.
                        context.conversation.messages.insert(
                            1,
                            Message(
                                role=Role.SYSTEM,
                                content=AGENT_NOTE,
                                metadata={"volatile": True},
                            ),
                        )
                        context.metadata[PREAMBLE_SPOKEN] = threading.Event()
                    if speaking and text:
                        await self._speak_responder(text, context, turn_id)
                    if route == "answer":
                        # A fenced answer is never spoken.
                        completed = self._turn_state.is_active(turn_id)
                    else:
                        completed = await self._consume_agent(
                            prompt,
                            context,
                            turn_id,
                            binding=binding,
                            late_filler=late_filler,
                        )
            except Exception as error:  # noqa: BLE001 - surfaced as a pipeline error frame
                if self._turn_state.is_active(turn_id):
                    self._turn_state.interrupt()
                    await self.push_error(
                        error_msg=f"Error during completion: {error}", exception=error
                    )
                    # This response did not complete, so reset downstream
                    # aggregation instead of flushing its partial text as a
                    # successful reply.
                    await self.push_frame(InterruptionFrame())
                else:
                    # A fenced run's failure must not cut the run now speaking.
                    logger.warning("fenced voice run failed", exc_info=True)
            finally:
                await self.stop_processing_metrics()
        if completed:
            await self.push_frame(LLMFullResponseEndFrame())
        return completed

    async def _speak_responder(
        self, text: str, context: AgentContext, turn_id: int
    ) -> None:
        frame = LLMTextFrame(f"{text} ")
        frame.metadata[FLUSH_TTS_METADATA] = True
        frame.metadata["openjarvis_is_current"] = lambda: self._turn_state.is_active(
            turn_id
        )
        spoken = context.metadata.get(PREAMBLE_SPOKEN)
        if spoken is not None:
            spoken.set()
        await self.push_frame(frame)

    @asynccontextmanager
    async def _ask_responder(
        self,
        utterance: str,
        conversation: str,
        in_progress: Sequence[str] = (),
    ) -> AsyncIterator[tuple[str, str, asyncio.Future[str] | None]]:
        """Own the Responder stream through this turn's first speech.

        Delegation is its call to ``getNextResponseFromSupervisor``. The text
        before the call is the filler, complete once the call appears, so the
        rest of the stream (the unused argument) is cancelled. Anything but a
        usable answer routes to the Agent. A timeout leaves delegation available
        for a late filler, but a late standalone answer never replaces the Agent.
        """
        delegated: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        def on_delegate(filler: str) -> None:
            if not delegated.done():
                delegated.set_result(filler)

        started = time.monotonic()
        decided: float | None = None
        # Passed only when there is something to name.
        extra = {"in_progress": in_progress} if in_progress else {}
        task = asyncio.create_task(
            self._responder.respond(utterance, conversation, on_delegate, **extra)
        )

        def stop_response(_future=None):
            if not task.done() and not task.cancelling():
                task.cancel()

        # A tool call completes the filler; its unused argument need not stream.
        # Cancelling the late-filler wait also closes the provider stream.
        delegated.add_done_callback(stop_response)
        outcome = "cancelled"
        route, text = "agent", ""
        late_filler = None
        try:
            await asyncio.wait(
                {task, delegated},
                timeout=RESPONDER_DECISION_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            decided = time.monotonic() - started
            if delegated.done():
                text = normalize_speech_text(delegated.result())
                outcome = "delegated" if text else "delegated_no_filler"
            elif not task.done():
                outcome = "timeout"
                late_filler = delegated
            elif task.cancelled():
                # Cancelled inside the Responder, not by this turn: a failure
                # like any other, so the Agent still answers.
                outcome = "error:CancelledError"
            elif task.exception() is not None:
                outcome = f"error:{type(task.exception()).__name__}"
            else:
                reply = task.result()
                answer = (
                    normalize_speech_text(reply.text)
                    if reply is not None and reply.kind == "answer"
                    else ""
                )
                if not answer:
                    outcome = "empty"
                else:
                    route, text, outcome = "answer", answer, "answered"
            logger.warning(
                "voice_preamble model=%s outcome=%s decision_seconds=%.2f",
                self._responder.model,
                outcome,
                decided,
            )
            yield route, text, late_filler
        finally:
            delegated.cancel()
            stop_response()
            await asyncio.gather(task, return_exceptions=True)
            # Shape only, never the customer's words (WARNING: serve drops INFO).
            if decided is None:
                logger.warning(
                    "voice_preamble model=%s outcome=%s decision_seconds=%.2f",
                    self._responder.model,
                    outcome,
                    time.monotonic() - started,
                )

    async def _consume_agent(
        self,
        prompt: str,
        context: AgentContext,
        turn_id: int,
        *,
        binding: Any | None = None,
        late_filler: asyncio.Future[str] | None = None,
    ) -> bool:
        """Push one Agent turn's frames; True when the turn is still current.

        A fenced turn keeps consuming the Agent stream to its end, pushing
        nothing, so the run's tools and final answer complete.
        """
        speech_lock = asyncio.Lock()
        agent_spoke = asyncio.Event()

        async def speak_late_filler():
            started = time.monotonic()
            outcome = "agent_first"
            try:
                text = normalize_speech_text(await late_filler)
                async with speech_lock:
                    if (
                        text
                        and not agent_spoke.is_set()
                        and self._turn_state.is_active(turn_id)
                    ):
                        await self._speak_responder(text, context, turn_id)
                        outcome = "spoken"
                    else:
                        outcome = "empty_or_stale"
            finally:
                logger.warning(
                    "voice_preamble_late model=%s outcome=%s wait_seconds=%.2f",
                    self._responder.model,
                    outcome,
                    time.monotonic() - started,
                )

        late_task = (
            asyncio.create_task(speak_late_filler())
            if late_filler is not None
            else None
        )
        response = self.stream_agent(
            prompt,
            context,
            turn_id=turn_id,
            binding=binding,
            speech_started=agent_spoke,
        )
        try:
            async with aclosing(response):
                async for pushed in response:
                    if not self._turn_state.is_active(turn_id):
                        # Drain: the Agent finishes its work, unheard.
                        continue
                    if isinstance(pushed, LLMTextFrame):
                        # Serialize the first speech: no filler may follow it.
                        async with speech_lock:
                            agent_spoke.set()
                            if late_task is not None and not late_task.done():
                                late_task.cancel()
                            pushed.metadata["openjarvis_is_current"] = (
                                lambda turn_id=turn_id: self._turn_state.is_active(
                                    turn_id
                                )
                            )
                            await self.push_frame(pushed)
                    else:
                        await self.push_frame(pushed)
        finally:
            if late_task is not None:
                late_task.cancel()
                await asyncio.gather(late_task, return_exceptions=True)
        return self._turn_state.is_active(turn_id)

    async def run_event_turn(self, event: str) -> bool:
        """Answer a kiosk event (a background outcome), never as customer words.

        The event is this run's input only; the shared Pipecat context is not
        written, so later turns never read it as something the customer said.
        It never starts while a customer run speaks: that run would be fenced.
        A fenced run still draining does not hold it. Customer activity or the
        session's end cuts it; it then returns False.
        Touch-only checkout can finish before any spoken turn; use empty history
        for that notification.
        Use the configured fast voice model through the same Agent runtime;
        customer turns keep their original binding and no preamble is generated.
        """
        if self._answering or (
            self._event_task is not None and not self._event_task.done()
        ):
            return False
        _, context = agent_input(
            _EventContext(self._last_context or LLMContext(), event)
        )
        context.metadata["voice_event_turn"] = True
        binding = (
            self._binding.with_model(self._responder.model)
            if self._responder is not None
            else self._binding
        )
        task = asyncio.create_task(
            self._answer(event, context, nullcontext(), binding=binding)
        )
        self._event_task = task
        try:
            return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return False
        finally:
            if self._event_task is task:
                self._event_task = None

    async def run_phrased_report(self, event: str) -> str:
        """Speak one fast-model line for a late result of an interrupted request.

        OpenAI's standard: the result goes back to a model, which phrases it
        for the conversation as it is now (spec 2026-10-17 barge-in §5.6).
        Returns "spoken"; "cut" when a customer turn runs or customer activity
        or the session's end cuts it (the outcome stays unreported); "failed"
        when there is no Responder, it timed out or said nothing (the caller
        falls back to an event turn).
        """
        if self._answering or (
            self._event_task is not None and not self._event_task.done()
        ):
            return "cut"
        if self._responder is None:
            return "failed"
        started = time.monotonic()
        task = asyncio.create_task(self._phrase_and_speak(event))
        self._event_task = task
        try:
            outcome = await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            outcome = "cut"
        finally:
            if self._event_task is task:
                self._event_task = None
        # Shape only, never the words (WARNING: serve drops INFO).
        logger.warning(
            "voice_phrased_report outcome=%s seconds=%.2f",
            outcome,
            time.monotonic() - started,
        )
        return outcome

    async def _phrase_and_speak(self, event: str) -> str:
        conversation = recent_conversation(self._last_context or LLMContext())
        try:
            text = await asyncio.wait_for(
                self._responder.report(event, conversation), PHRASE_TIMEOUT_SECONDS
            )
        except Exception:  # noqa: BLE001 - any failure falls back to an event turn
            return "failed"
        text = normalize_speech_text(text or "")
        if not text:
            return "failed"
        if self._answering:
            # A customer run began while the line was written: beginning a turn
            # now would fence that run.
            return "cut"
        turn_id = self._turn_state.begin_turn()
        await self.push_frame(LLMFullResponseStartFrame())
        await self._speak_responder(text, AgentContext(), turn_id)
        await self.push_frame(LLMFullResponseEndFrame())
        # A barge-in that fenced this turn while it was spoken: not reported.
        return "spoken" if self._turn_state.is_active(turn_id) else "cut"

    def _cancel_event_turn(self) -> asyncio.Task[Any] | None:
        task = self._event_task
        if task is None or task.done():
            return None
        task.cancel()
        return task

    async def _stop_event_turn(self) -> None:
        task = self._cancel_event_turn()
        if task is not None:
            # Only its unwinding is awaited: no frame of this processor is
            # needed for it, and its cancellation is not this turn's.
            await asyncio.wait({task})
