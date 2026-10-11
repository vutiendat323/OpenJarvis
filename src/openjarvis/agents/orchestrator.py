"""OrchestratorAgent — multi-turn agent with tool-calling loop.

Supports two modes:

- **function_calling** (default): Uses OpenAI-format tool definitions and
  parses ``tool_calls`` from the engine response.
- **structured**: Uses a THOUGHT/TOOL/INPUT/FINAL_ANSWER text format
  (like ReAct) with a canonical system prompt from the orchestrator
  prompt registry.  This is the format used by the SFT/GRPO training
  pipelines, making the Orchestrator a distinctive trainable agent type.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import copy
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any, List, Optional

from openjarvis.agents._stubs import (
    AgentContext,
    AgentResult,
    AgentRunCompleted,
    AgentStreamEvent,
    AgentTextDelta,
    AgentToolFinished,
    AgentToolStarted,
    ToolUsingAgent,
    check_agent_cancelled,
    run_agent_sync_worker,
)
from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.core.conversation import (
    agent_turn_scope,
    current_conversation_id,
    current_turn_nonce,
)
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.engine._stubs import InferenceEngine, merge_tool_call_fragments
from openjarvis.tools._stubs import BaseTool
from openjarvis.tools.result_projection import project_tool_content

logger = logging.getLogger("openjarvis.agents.orchestrator")


def _log_round(
    *,
    path: str,
    turn: int,
    model: str,
    finish_reason: str,
    visible_chars: int,
    tool_names: list[str],
    usage: dict[str, Any] | None = None,
    reasoning_chars: int | None = None,
    continuation: int = 0,
) -> None:
    """One diagnostic line per model round: shape only, never customer text.

    A turn that loops leaves no other trace of why: the bus payload carries
    neither the finish reason nor whether the round produced any visible text.
    WARNING, like the kiosk tick: `jarvis serve` drops openjarvis INFO lines.
    """
    logger.warning(
        "agent_round path=%s turn=%d model=%s finish=%s completion_tokens=%s "
        "visible_chars=%d reasoning_chars=%s tool_calls=%s continuation=%d",
        path,
        turn,
        model,
        finish_reason or "-",
        (usage or {}).get("completion_tokens"),
        visible_chars,
        "-" if reasoning_chars is None else reasoning_chars,
        ",".join(tool_names) or "-",
        continuation,
    )


def _log_final_rejected(turn: int, intent_state: Any, visible_chars: int) -> None:
    logger.warning(
        "agent_final_rejected turn=%d declared=%s reached=%s terminal=%s "
        "target_attempted=%s visible_chars=%d",
        turn,
        intent_state.declared,
        intent_state.reached,
        intent_state.terminal_reason,
        intent_state.target_attempted,
        visible_chars,
    )


_PROGRESS_ARGUMENT = "_progress"


def _with_progress_argument(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Require a short spoken clause on every tool call.

    Asked for in the prompt alone, models skipped it and the customer heard
    nothing until the turn's last round. As a required argument it is written,
    from the customer's own request, together with the call itself.
    """
    result = copy.deepcopy(tools)
    for entry in result:
        schema = entry["function"].setdefault("parameters", {"type": "object"})
        schema.setdefault("properties", {})[_PROGRESS_ARGUMENT] = {
            "type": "string",
            "description": (
                "Short clause for the user about what you are doing for their "
                "current request, spoken aloud while this call runs. Never "
                "state or imply the result. Empty string after the turn's "
                "first call."
            ),
        }
        if _PROGRESS_ARGUMENT not in schema.setdefault("required", []):
            schema["required"].append(_PROGRESS_ARGUMENT)
    return result


def _progress_clause(tool_calls: list[ToolCall]) -> str:
    """The first nonempty ``_progress`` among a round's calls."""
    for call in tool_calls:
        try:
            arguments = json.loads(call.arguments or "{}")
        except ValueError:
            continue
        value = (
            arguments.get(_PROGRESS_ARGUMENT) if isinstance(arguments, dict) else None
        )
        if isinstance(value, str) and value.strip():
            clause = value.strip()
            if not clause.startswith(("{", "[")):
                return clause
    return ""


def _without_progress(call: ToolCall) -> ToolCall:
    try:
        arguments = json.loads(call.arguments or "{}")
    except ValueError:
        return call
    if not isinstance(arguments, dict) or _PROGRESS_ARGUMENT not in arguments:
        return call
    arguments.pop(_PROGRESS_ARGUMENT)
    return ToolCall(call.id, call.name, json.dumps(arguments, ensure_ascii=False))


@AgentRegistry.register("orchestrator")
class OrchestratorAgent(ToolUsingAgent):
    """Multi-turn agent that routes between tools and the LLM.

    Implements a tool-calling loop:
    1. Send messages with tool definitions to the engine.
    2. If the response contains tool_calls, execute them and loop.
    3. If no tool_calls, return the final answer.
    4. Stop after ``max_turns`` iterations.

    In **structured** mode the agent instead uses a
    ``THOUGHT: / TOOL: / INPUT: / FINAL_ANSWER:`` text protocol
    identical to the format used by the orchestrator SFT/GRPO
    training pipelines.
    """

    agent_id = "orchestrator"
    # Run state is local; shared tools are protected by the runtime tool gate.
    # Runtime checks the concrete class dictionary, so subclasses must audit
    # and explicitly opt in rather than accidentally inheriting this promise.
    supports_run_preemption = True
    _default_temperature = 0.7
    _default_max_tokens = 1024
    _default_max_turns = 10

    def __init__(
        self,
        engine: InferenceEngine,
        model: str,
        *,
        tools: Optional[List[BaseTool]] = None,
        bus: Optional[EventBus] = None,
        max_turns: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        mode: str = "function_calling",
        system_prompt: Optional[str] = None,
        intent_normalization: bool = False,
        progress_speech: bool = False,
        reasoning_deadline_seconds: float = 0.0,
        prompt_builder: Optional[Any] = None,
        parallel_tools: bool = True,
        interactive: bool = False,
        confirm_callback=None,
        loop_guard_config: Optional[Any] = None,
        capability_policy: Any = None,
    ) -> None:
        if intent_normalization and mode != "function_calling":
            raise ValueError("intent_normalization requires function_calling mode")
        super().__init__(
            engine,
            model,
            tools=tools,
            bus=bus,
            max_turns=max_turns,
            temperature=temperature,
            max_tokens=max_tokens,
            interactive=interactive,
            confirm_callback=confirm_callback,
            prompt_builder=prompt_builder,
            loop_guard_config=loop_guard_config,
            capability_policy=capability_policy,
        )
        self._mode = mode
        self._system_prompt = system_prompt
        self._parallel_tools = parallel_tools
        self._intent_normalization = intent_normalization
        self._progress_speech = progress_speech
        self._reasoning_deadline = max(0.0, float(reasoning_deadline_seconds))

    def _intent_state(self, messages):
        if not self._intent_normalization:
            return None
        from openjarvis.agents.intent import IntentState

        state = IntentState(self._tools)
        messages.insert(0, Message(role=Role.SYSTEM, content=state.instruction()))
        return state

    @staticmethod
    def _mark_run_completed() -> None:
        # A run the customer interrupted told them nothing, even when a sync
        # fallback worker still reaches a normal ending after the consumer left.
        try:
            check_agent_cancelled()
        except asyncio.CancelledError:
            return
        conversation_id = current_conversation_id()
        if conversation_id:
            ACTION_LEDGER.mark_run_completed(conversation_id, current_turn_nonce())

    def _is_read_only_tool(self, name: str) -> bool:
        tool = self._executor.get_tool(name)
        return tool is not None and tool.spec.metadata.get("read_only") is True

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        with agent_turn_scope():
            if self._mode == "structured":
                result = self._run_structured(input, context, **kwargs)
            else:
                result = self._run_function_calling(input, context, **kwargs)
            self._mark_run_completed()
            return result

    async def run_stream(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AgentStreamEvent]:
        # Consumers may resume the generator from different tasks. Keep the
        # nonce's creation, execution and cleanup in one copied context.
        turn_context = contextvars.copy_context()
        stream = self._run_stream_scoped(input, context, **kwargs)
        try:
            while True:
                try:
                    event = await asyncio.create_task(
                        anext(stream),
                        context=turn_context,
                    )
                except StopAsyncIteration:
                    break
                yield event
        finally:
            await asyncio.create_task(stream.aclose(), context=turn_context)

    async def _run_stream_scoped(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AgentStreamEvent]:
        with agent_turn_scope():
            completed = False
            async for event in self._run_stream_in_turn(input, context, **kwargs):
                completed = isinstance(event, AgentRunCompleted)
                yield event
            # An interrupted consumer never resumes the generator, so only a
            # run that ended normally gets here with ``completed``.
            if completed:
                self._mark_run_completed()

    async def _run_stream_in_turn(
        self,
        input: str,
        context: AgentContext | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[AgentStreamEvent]:
        if self._mode == "structured":
            async for event in super().run_stream(input, context, **kwargs):
                yield event
            return

        # One agent instance serves every caller, so the model for this run is
        # a local — never an attribute swap, which a concurrent run would see.
        model = kwargs.pop("model", None) or self._model

        supports_semantic_reasoning = getattr(
            self._engine,
            "supports_semantic_reasoning_stream",
            None,
        )
        if not callable(supports_semantic_reasoning) or not bool(
            supports_semantic_reasoning(model)
        ):
            async for event in super().run_stream(
                input, context, model=model, **kwargs
            ):
                yield event
            return

        self._emit_turn_start(input)
        loop_guard = self._new_loop_guard()
        messages = self._build_messages(
            input,
            context,
            system_prompt=self._system_prompt,
        )
        runtime_message = None
        intent_state = self._intent_state(messages)
        openai_tools = self._executor.get_openai_tools() if self._tools else []
        if intent_state is not None:
            openai_tools = intent_state.schemas(openai_tools)
        if self._progress_speech and openai_tools:
            openai_tools = _with_progress_argument(openai_tools)
        all_tool_results: list[ToolResult] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        turn_spoke = False
        # Set once a round spent its whole budget reasoning; stays on this turn.
        reasoning_off: dict[str, Any] = {}
        # Only worth arming when the engine can actually answer without it.
        off_options_fn = getattr(self._engine, "reasoning_off_options", None)
        reasoning_deadline = (
            self._reasoning_deadline
            if callable(off_options_fn) and off_options_fn(model)
            else 0.0
        )

        for turns in range(1, self._max_turns + 1):
            if loop_guard:
                messages = loop_guard.compress_context(messages)
            runtime_message = self._inject_runtime_context(
                messages, runtime_message, intent_state
            )

            gen_kwargs: dict[str, Any] = {}
            if openai_tools:
                gen_kwargs["tools"] = openai_tools

            visible_parts: list[str] = []
            continuations = 0

            while True:
                tool_call_fragments: dict[int, dict[str, Any]] = {}
                visible_text_filter = _VisibleTextFilter()
                round_content: list[str] = []
                finish_reason = ""
                round_usage: dict[str, Any] = {}
                response_items: list[dict[str, Any]] = []
                reasoning_chars = 0
                visible_before = sum(len(part) for part in visible_parts)

                self._emit_stream_inference_start(model=model)
                stream = self._engine.stream_full(
                    messages,
                    model=model,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    **gen_kwargs,
                    **reasoning_off,
                )
                output_seen = False
                reasoning_started: float | None = None
                async with aclosing(stream):
                    while True:
                        wait: float | None = None
                        if (
                            reasoning_deadline
                            and reasoning_started is not None
                            and not output_seen
                            and not reasoning_off
                        ):
                            wait = max(
                                0.0,
                                reasoning_started
                                + reasoning_deadline
                                - time.monotonic(),
                            )
                        try:
                            if wait is None:
                                chunk = await anext(stream)
                            else:
                                chunk = await asyncio.wait_for(anext(stream), wait)
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            if wait is None:
                                raise
                            finish_reason = "reasoning_deadline"
                            break
                        if chunk.reasoning_content and reasoning_started is None:
                            reasoning_started = time.monotonic()
                        if chunk.content or chunk.tool_calls:
                            output_seen = True
                        if chunk.tool_calls:
                            merge_tool_call_fragments(
                                tool_call_fragments,
                                chunk.tool_calls,
                            )
                        if chunk.reasoning_content:
                            reasoning_chars += len(chunk.reasoning_content)
                        if chunk.content:
                            round_content.append(chunk.content)
                            for visible in visible_text_filter.feed(chunk.content):
                                visible_parts.append(visible)
                                # Tools may still arrive in later chunks. Hold
                                # speech until the decision resolves; without
                                # tools, preserve immediate text streaming.
                                if not openai_tools and intent_state is None:
                                    yield AgentTextDelta(visible)
                        if chunk.finish_reason:
                            finish_reason = chunk.finish_reason
                        if chunk.usage:
                            round_usage.update(chunk.usage)
                        if chunk.response_items:
                            response_items.extend(chunk.response_items)

                for trailing_visible in visible_text_filter.finish():
                    visible_parts.append(trailing_visible)
                    if not openai_tools and intent_state is None:
                        yield AgentTextDelta(trailing_visible)

                self._emit_stream_inference_end(
                    model=model,
                    content="".join(round_content),
                    tool_calls=tool_call_fragments,
                    finish_reason=finish_reason,
                    usage=round_usage,
                )
                total_prompt_tokens += round_usage.get("prompt_tokens", 0)
                total_completion_tokens += round_usage.get("completion_tokens", 0)
                _log_round(
                    path="stream",
                    turn=turns,
                    model=model,
                    finish_reason=finish_reason,
                    visible_chars=(
                        sum(len(part) for part in visible_parts) - visible_before
                    ),
                    tool_names=[
                        fragment["function"]["name"]
                        for _, fragment in sorted(tool_call_fragments.items())
                    ],
                    usage=round_usage,
                    reasoning_chars=reasoning_chars,
                    continuation=continuations,
                )

                if (
                    finish_reason in ("length", "reasoning_deadline")
                    and not tool_call_fragments
                    and not reasoning_off
                    and sum(len(part) for part in visible_parts) == visible_before
                ):
                    # Nothing but reasoning fit the budget. Reasoning again
                    # (the continuation nudge below) repeats the wait; ask the
                    # engine to answer this round, and the turn, without it.
                    options = (
                        off_options_fn(model) if callable(off_options_fn) else None
                    )
                    if options:
                        reasoning_off = options
                        logger.warning(
                            "agent_reasoning_exhausted path=stream turn=%d "
                            "model=%s reason=%s reasoning_chars=%d "
                            "retry=reasoning_off",
                            turns,
                            model,
                            finish_reason,
                            reasoning_chars,
                        )
                        continue

                if (
                    tool_call_fragments
                    or finish_reason != "length"
                    or continuations >= 2
                ):
                    break

                messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content="".join(visible_parts),
                    )
                )
                messages.append(
                    Message(
                        role=Role.USER,
                        content="Continue from where you left off.",
                    )
                )
                continuations += 1

            tool_calls = _build_tool_calls(tool_call_fragments)
            if tool_calls:
                round_text = "".join(visible_parts)
                messages.append(
                    Message(
                        role=Role.ASSISTANT,
                        content=round_text,
                        tool_calls=tool_calls,
                        metadata=(
                            {"response_items": response_items} if response_items else {}
                        ),
                    )
                )
                # The round is known to carry tool calls, so its text is speech
                # alongside them, never the final answer. Release it now; the
                # tools start right after without waiting for playback. Text in
                # the structured answer format is never speech, so it is held.
                if round_text.strip() and not round_text.lstrip().startswith(
                    ("{", "[")
                ):
                    yield AgentTextDelta(round_text)
                    turn_spoke = True
                elif self._progress_speech and not turn_spoke:
                    # Only while nothing has been said this turn: one clause,
                    # written from the request, covers every later round.
                    progress = _progress_clause(tool_calls)
                    if progress:
                        yield AgentTextDelta(progress)
                        turn_spoke = True
                previous_tool_results = len(all_tool_results)
                for tool_call in tool_calls:
                    yield AgentToolStarted(tool_call.name)
                await self._execute_function_tool_calls_async(
                    messages,
                    tool_calls,
                    all_tool_results,
                    loop_guard,
                    intent_state,
                )
                new_tool_results = all_tool_results[previous_tool_results:]
                for tool_result in new_tool_results:
                    yield AgentToolFinished(
                        tool_result.tool_name,
                        ok=tool_result.success,
                    )
                if any(
                    _requires_pending_approval(result) for result in new_tool_results
                ):
                    self._emit_turn_end(turns=turns, pending_approval=True)
                    yield AgentRunCompleted(
                        AgentResult(
                            content="",
                            tool_results=all_tool_results if intent_state else [],
                            turns=turns if intent_state else 0,
                            metadata={
                                "pending_approval": True,
                                **(intent_state.metadata() if intent_state else {}),
                            },
                        )
                    )
                    return
                content = round_text
                completed = self._completed_display_batch(tool_calls, new_tool_results)
                if completed:
                    if any("customer_message" in r.metadata for r in new_tool_results):
                        content = self._display_customer_message(new_tool_results)
                        if content:
                            yield AgentTextDelta(content)
                    # Without a tool message the round text was already released.
                    metadata = {
                        "prompt_tokens": total_prompt_tokens,
                        "completion_tokens": total_completion_tokens,
                        "total_tokens": total_prompt_tokens + total_completion_tokens,
                        **(intent_state.metadata() if intent_state else {}),
                    }
                    self._emit_turn_end(turns=turns, content_length=len(content))
                    yield AgentRunCompleted(
                        AgentResult(
                            content=content,
                            tool_results=all_tool_results,
                            turns=turns,
                            metadata=metadata,
                        )
                    )
                    return
                continue

            content = "".join(visible_parts)
            if intent_state is not None:
                resolved = intent_state.finish(content)
                if resolved is None:
                    _log_final_rejected(turns, intent_state, len(content))
                    messages.append(
                        Message(
                            role=Role.SYSTEM,
                            content=intent_state.final_correction(content),
                        )
                    )
                    continue
                content = resolved
            if (openai_tools or intent_state is not None) and content:
                yield AgentTextDelta(content)
            metadata = {
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "total_tokens": total_prompt_tokens + total_completion_tokens,
                **(intent_state.metadata() if intent_state else {}),
            }
            self._emit_turn_end(turns=turns, content_length=len(content))
            yield AgentRunCompleted(
                AgentResult(
                    content=content,
                    tool_results=all_tool_results,
                    turns=turns,
                    metadata=metadata,
                )
            )
            return

        metadata = {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            **(intent_state.metadata() if intent_state else {}),
        }
        logger.warning(
            "agent_max_turns path=stream turns=%d tool_results=%d "
            "declared=%s reached=%s",
            self._max_turns,
            len(all_tool_results),
            intent_state.declared if intent_state else None,
            intent_state.reached if intent_state else None,
        )
        result = self._max_turns_result(
            all_tool_results,
            self._max_turns,
            content="",
            metadata=metadata,
        )
        if intent_state is not None:
            # A customer hears this turn: never the English stub. Report the
            # goal's own verified message once it landed, otherwise nothing.
            result.content = (
                self._reached_customer_message(all_tool_results)
                if intent_state.reached
                else ""
            )
        if result.content:
            yield AgentTextDelta(result.content)
        yield AgentRunCompleted(result)

    def _emit_stream_inference_start(self, *, model: str | None = None) -> None:
        check_agent_cancelled()
        if self._bus and not getattr(
            self._engine,
            "_publishes_stream_events",
            False,
        ):
            self._bus.publish(
                EventType.INFERENCE_START,
                {
                    "model": model or self._model,
                    "engine": getattr(self._engine, "engine_id", ""),
                },
            )

    def _emit_stream_inference_end(
        self,
        *,
        model: str | None = None,
        content: str,
        tool_calls: dict[int, dict[str, Any]],
        finish_reason: str,
        usage: dict[str, Any],
    ) -> None:
        if self._bus and not getattr(
            self._engine,
            "_publishes_stream_events",
            False,
        ):
            self._bus.publish(
                EventType.INFERENCE_END,
                {
                    "model": model or self._model,
                    "usage": usage,
                    "content": content,
                    "tool_calls": list(tool_calls.values()),
                    "finish_reason": finish_reason,
                },
            )

    # ------------------------------------------------------------------
    # Structured mode (THOUGHT/TOOL/INPUT/FINAL_ANSWER)
    # ------------------------------------------------------------------

    def _run_structured(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)
        loop_guard = self._new_loop_guard()

        # Build system prompt
        if self._system_prompt:
            sys_prompt = self._system_prompt
        else:
            from openjarvis.learning.intelligence.orchestrator.prompt_registry import (
                build_system_prompt,
            )

            sys_prompt = build_system_prompt(tools=self._tools)

        messages = self._build_messages(input, context, system_prompt=sys_prompt)

        all_tool_results: list[ToolResult] = []
        turns = 0

        for _turn in range(self._max_turns):
            turns += 1

            if loop_guard:
                messages = loop_guard.compress_context(messages)

            result = self._generate(messages)
            content = result.get("content", "")

            parsed = self._parse_structured_response(content)

            # FINAL_ANSWER -> done
            if parsed["final_answer"]:
                self._emit_turn_end(turns=turns)
                return AgentResult(
                    content=parsed["final_answer"],
                    tool_results=all_tool_results,
                    turns=turns,
                )

            # TOOL -> execute
            if parsed["tool"]:
                messages.append(Message(role=Role.ASSISTANT, content=content))

                tool_call = ToolCall(
                    id=f"orch_{turns}",
                    name=parsed["tool"],
                    arguments=self._normalize_structured_tool_input(
                        parsed["tool"],
                        parsed["input"],
                    ),
                )
                tool_result = self._executor.execute(tool_call)
                all_tool_results.append(tool_result)

                if tool_result.success:
                    observation = f"Observation: {tool_result.content}"
                else:
                    observation = (
                        f"Observation: Tool '{tool_result.tool_name}' failed: "
                        f"{tool_result.content}"
                    )
                messages.append(Message(role=Role.USER, content=observation))
                continue

            # Neither -> treat content as final answer
            self._emit_turn_end(turns=turns)
            return AgentResult(
                content=content,
                tool_results=all_tool_results,
                turns=turns,
            )

        # Max turns exceeded
        return self._max_turns_result(all_tool_results, turns)

    def _normalize_structured_tool_input(
        self,
        tool_name: str,
        raw_input: str,
    ) -> str:
        """Map unambiguous structured text input to a string parameter."""
        if not raw_input:
            return "{}"

        try:
            parsed_input = json.loads(raw_input)
        except json.JSONDecodeError:
            invalid_json = True
            string_value = raw_input
        else:
            invalid_json = False
            if isinstance(parsed_input, dict):
                return raw_input
            # INPUT is a text protocol. A non-object JSON value such as 42,
            # true, null, or [1, 2] may still be the intended text for a tool's
            # string parameter. Quoted JSON strings are decoded to remove only
            # their surrounding quotes; other values retain their source text.
            string_value = parsed_input if isinstance(parsed_input, str) else raw_input

        tool_spec = None
        for candidate in reversed(self._tools):
            candidate_spec = candidate.spec
            if candidate_spec.name == tool_name:
                tool_spec = candidate_spec
                break
        if tool_spec is None:
            return raw_input

        parameters = tool_spec.parameters
        parameter_container_type = parameters.get("type")
        if parameter_container_type not in (None, "object"):
            return raw_input

        properties = parameters.get("properties", {})
        required = parameters.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return raw_input

        if len(required) == 1 and required[0] in properties:
            parameter_name = required[0]
        elif not required and len(properties) == 1:
            parameter_name = next(iter(properties))
        else:
            return raw_input

        parameter_schema = properties[parameter_name]
        if not isinstance(parameter_schema, dict):
            return raw_input
        parameter_type = parameter_schema.get("type")
        accepts_string = parameter_type == "string" or (
            isinstance(parameter_type, list) and "string" in parameter_type
        )
        if not accepts_string:
            return raw_input

        allow_object_text = (
            tool_spec.metadata.get("structured_allow_object_text") is True
        )
        starts_like_object = raw_input.lstrip("\ufeff \t\r\n").startswith("{")
        if invalid_json and starts_like_object and not allow_object_text:
            return raw_input

        return json.dumps({parameter_name: string_value})

    @staticmethod
    def _parse_structured_response(text: str) -> dict:
        """Parse THOUGHT/TOOL/INPUT/FINAL_ANSWER from model output."""
        result = {
            "thought": "",
            "tool": "",
            "input": "",
            "final_answer": "",
        }

        thought_match = re.search(
            r"THOUGHT:\s*(.+?)(?=\nTOOL:|\nFINAL[_ ]?ANSWER:|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            result["thought"] = thought_match.group(1).strip()

        final_match = re.search(
            r"FINAL[_ ]?ANSWER:\s*(.+)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if final_match:
            result["final_answer"] = final_match.group(1).strip()
            return result

        tool_match = re.search(r"TOOL:\s*(.+)", text, re.IGNORECASE)
        if tool_match:
            result["tool"] = tool_match.group(1).strip()

        input_match = re.search(
            r"INPUT:\s*(.+?)(?=\nTHOUGHT:|\nTOOL:|\nFINAL|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if input_match:
            result["input"] = input_match.group(1).strip()

        return result

    # ------------------------------------------------------------------
    # Function-calling mode (original behaviour)
    # ------------------------------------------------------------------

    def _run_function_calling(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)
        loop_guard = self._new_loop_guard()

        # Build initial messages
        messages = self._build_messages(
            input,
            context,
            system_prompt=self._system_prompt,
        )
        runtime_message = None
        intent_state = self._intent_state(messages)

        # Get OpenAI-format tool definitions
        openai_tools = self._executor.get_openai_tools() if self._tools else []
        if intent_state is not None:
            openai_tools = intent_state.schemas(openai_tools)
        if self._progress_speech and openai_tools:
            openai_tools = _with_progress_argument(openai_tools)
        all_tool_results: list[ToolResult] = []
        turns = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for _turn in range(self._max_turns):
            turns += 1

            if loop_guard:
                messages = loop_guard.compress_context(messages)
            runtime_message = self._inject_runtime_context(
                messages, runtime_message, intent_state
            )

            # Build generate kwargs
            gen_kwargs: dict[str, Any] = {}
            if openai_tools:
                gen_kwargs["tools"] = openai_tools

            result = self._generate(messages, **gen_kwargs)

            # Accumulate token usage
            usage = result.get("usage", {})
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

            content = result.get("content", "")
            raw_tool_calls = result.get("tool_calls", [])
            _log_round(
                path="sync",
                turn=turns,
                model=self._effective_model(),
                finish_reason=result.get("finish_reason", ""),
                visible_chars=len(self._strip_think_tags(content or "")),
                tool_names=[tc.get("name", "") for tc in raw_tool_calls],
                usage=usage,
            )

            # No tool calls -> check continuation, then final answer
            if not raw_tool_calls:
                content = self._check_continuation(result, messages)
                content = self._strip_think_tags(content)
                if intent_state is not None:
                    resolved = intent_state.finish(content)
                    if resolved is None:
                        _log_final_rejected(turns, intent_state, len(content))
                        messages.append(
                            Message(
                                role=Role.SYSTEM,
                                content=intent_state.final_correction(content),
                            )
                        )
                        continue
                    content = resolved
                metadata = {
                    "prompt_tokens": total_prompt_tokens,
                    "completion_tokens": total_completion_tokens,
                    "total_tokens": total_prompt_tokens + total_completion_tokens,
                    **(intent_state.metadata() if intent_state else {}),
                }
                self._emit_turn_end(turns=turns, content_length=len(content))
                return AgentResult(
                    content=content,
                    tool_results=all_tool_results,
                    turns=turns,
                    metadata=metadata,
                )

            # Build ToolCall objects from raw dicts
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("name", ""),
                    arguments=tc.get("arguments", "{}"),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]

            # Append assistant message with tool calls
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=content,
                    tool_calls=tool_calls,
                    metadata={"response_items": result["response_items"]}
                    if result.get("response_items")
                    else {},
                )
            )

            previous_tool_results = len(all_tool_results)
            self._execute_function_tool_calls(
                messages,
                tool_calls,
                all_tool_results,
                loop_guard,
                intent_state,
            )
            new_tool_results = all_tool_results[previous_tool_results:]
            final_content = self._strip_think_tags(content)
            completed = self._completed_display_batch(tool_calls, new_tool_results)
            if completed:
                if any("customer_message" in r.metadata for r in new_tool_results):
                    final_content = self._display_customer_message(new_tool_results)
                metadata = {
                    "prompt_tokens": total_prompt_tokens,
                    "completion_tokens": total_completion_tokens,
                    "total_tokens": total_prompt_tokens + total_completion_tokens,
                    **(intent_state.metadata() if intent_state else {}),
                }
                self._emit_turn_end(turns=turns, content_length=len(final_content))
                return AgentResult(
                    content=final_content,
                    tool_results=all_tool_results,
                    turns=turns,
                    metadata=metadata,
                )
        # Max turns exceeded
        logger.warning(
            "agent_max_turns path=sync turns=%d tool_results=%d declared=%s reached=%s",
            self._max_turns,
            len(all_tool_results),
            intent_state.declared if intent_state else None,
            intent_state.reached if intent_state else None,
        )
        final_content = self._strip_think_tags(content) if content else ""
        if intent_state is not None and not intent_state.reached:
            final_content = ""
        metadata = {
            "max_turns_exceeded": True,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            **(intent_state.metadata() if intent_state else {}),
        }
        self._emit_turn_end(turns=turns, max_turns_exceeded=True)
        return AgentResult(
            content=final_content or "Maximum turns reached without a final answer.",
            tool_results=all_tool_results,
            turns=turns,
            metadata=metadata,
        )

    def _inject_runtime_context(
        self,
        messages: list[Message],
        previous: Message | None = None,
        intent_state: Any | None = None,
    ) -> Message | None:
        """Expose compact tool-owned state needed before the first tool call."""
        if previous is not None:
            messages[:] = [message for message in messages if message is not previous]
        runtime_context: dict[str, Any] = {}
        for tool in self._tools:
            provider = getattr(tool, "agent_context", None)
            if not callable(provider):
                continue
            supplied = provider()
            if isinstance(supplied, dict):
                runtime_context.update(supplied)
        conversation_id = current_conversation_id()
        if conversation_id:
            background = ACTION_LEDGER.background_for(
                conversation_id, current_turn_nonce()
            )
            if background:
                runtime_context["background_actions"] = background
        if not runtime_context:
            return
        runtime_context["turn_nonce"] = current_turn_nonce()
        if intent_state is not None:
            intent_state.runtime_context = runtime_context
        message = Message(
            role=Role.SYSTEM,
            content=(
                "Current tool-owned runtime state. Copy verified identifiers, "
                "revisions and turn_nonce into arguments where a tool contract "
                "requires them. They are validated by code and may expire when "
                "this turn or the tool-owned state changes. background_actions, "
                "when present, are outcomes of the customer's earlier requests "
                "that may finish after they spoke again: mention each finished "
                "one once in your reply and never repeat its action; a running "
                "one is not done yet. Ones marked already_told were already told "
                "to the customer: do not mention them again and never repeat "
                "their action. Never proceed to payment past a failed or "
                "unknown background action; tell the customer and ask first."
                "\n<runtime_context>"
                + json.dumps(runtime_context, ensure_ascii=False, separators=(",", ":"))
                + "</runtime_context>"
            ),
        )
        user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index].role == Role.USER
            ),
            len(messages),
        )
        messages.insert(user_index, message)
        return message

    def _execute_function_tool_calls(
        self,
        messages: list[Message],
        tool_calls: list[ToolCall],
        all_tool_results: list[ToolResult],
        loop_guard: Any | None,
        intent_state: Any | None = None,
    ) -> None:
        ordered_results = self._collect_function_tool_results(
            tool_calls, loop_guard, intent_state
        )
        self._append_function_tool_results(
            messages,
            all_tool_results,
            ordered_results,
        )

    async def _execute_function_tool_calls_async(
        self,
        messages: list[Message],
        tool_calls: list[ToolCall],
        all_tool_results: list[ToolResult],
        loop_guard: Any | None,
        intent_state: Any | None = None,
    ) -> None:
        ordered_results = await run_agent_sync_worker(
            self._collect_function_tool_results,
            tool_calls,
            loop_guard,
            intent_state,
        )
        self._append_function_tool_results(
            messages,
            all_tool_results,
            ordered_results,
        )

    def _collect_function_tool_results(
        self,
        tool_calls: list[ToolCall],
        loop_guard: Any | None,
        intent_state: Any | None = None,
    ) -> list[tuple[ToolCall, ToolResult]]:
        if self._progress_speech:
            tool_calls = [_without_progress(call) for call in tool_calls]
        deferred: list[ToolCall] = []
        if intent_state is not None:
            try:
                tool_calls = intent_state.prepare(tool_calls, defer_targets=True)
                deferred = list(intent_state.deferred)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "agent_dispatch_blocked tools=%s declared=%s reached=%s "
                    "target_attempted=%s reason=%s",
                    ",".join(call.name for call in tool_calls) or "-",
                    intent_state.declared,
                    intent_state.reached,
                    intent_state.target_attempted,
                    str(exc)[:120],
                )
                # The reason is what lets the model change course; a generic
                # refusal was retried until max_turns in a live turn.
                if intent_state.reached:
                    advice = (
                        "The requested outcome is already done: do not call "
                        "tools for it again; reply to the customer now."
                    )
                elif intent_state.declared is not None:
                    advice = (
                        f"Keep _intent={intent_state.declared} on every call this "
                        "turn, prerequisite reads included; call the target "
                        "alone once they are verified."
                    )
                else:
                    advice = "Preserve the declared goal and validate prerequisites."
                return [
                    (
                        call,
                        ToolResult(
                            tool_name=call.name,
                            success=False,
                            content=f"intent_dispatch_blocked: {exc}. {advice}",
                            metadata=intent_state.metadata(),
                        ),
                    )
                    for call in tool_calls
                ]

        def execute(tc: ToolCall) -> ToolResult:
            if loop_guard:
                verdict = loop_guard.check_call(
                    tc.name,
                    tc.arguments,
                    polling=self._tool_is_polling(tc.name),
                )
                if verdict.blocked:
                    return ToolResult(
                        tool_name=tc.name,
                        content=f"Loop guard: {verdict.reason}",
                        success=False,
                    )
            # Runs on the lease-kept worker thread: the outcome is recorded
            # even when the customer interrupted and this turn is gone.
            conversation_id = current_conversation_id()
            run_id = current_turn_nonce()
            record = bool(conversation_id) and not self._is_read_only_tool(tc.name)
            if record:
                ACTION_LEDGER.start(
                    conversation_id, call_id=tc.id, tool=tc.name, run_id=run_id
                )
            try:
                result = self._executor.execute(tc)
            except BaseException:
                # A BaseException here comes from the pre-dispatch cancellation
                # checks (ToolExecutor converts a cancellation raised after the
                # tool started into an outcome_unknown result), so nothing ran.
                if record:
                    ACTION_LEDGER.finish(
                        conversation_id,
                        call_id=tc.id,
                        run_id=run_id,
                        result=ToolResult(
                            tool_name=tc.name,
                            content="not dispatched",
                            success=False,
                            metadata={"dispatched": False},
                        ),
                    )
                raise
            if record:
                ACTION_LEDGER.finish(
                    conversation_id, call_id=tc.id, run_id=run_id, result=result
                )
            if loop_guard:
                loop_guard.note_result(tc.name, tc.arguments, success=result.success)
            return result

        if self._parallel_tools and len(tool_calls) > 1:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(tool_calls)
            ) as pool:
                futures = {
                    pool.submit(contextvars.copy_context().run, execute, tc): tc
                    for tc in tool_calls
                }
                results = {id(tc): future.result() for future, tc in futures.items()}
            ordered = [(tc, results[id(tc)]) for tc in tool_calls]
        else:
            ordered = [(tc, execute(tc)) for tc in tool_calls]
        if intent_state is not None:
            for call, result in ordered:
                intent_state.observe(call, result)
        if deferred:
            # The prerequisites above ran and were observed; the target waits
            # a round so its arguments are chosen with their results in view.
            held = [
                (
                    call,
                    ToolResult(
                        tool_name=call.name,
                        success=False,
                        content=(
                            "intent_target_deferred: its prerequisites ran in "
                            "this round. Call the target alone next, keeping "
                            f"_intent={intent_state.declared}."
                        ),
                        metadata=intent_state.metadata(),
                    ),
                )
                for call in deferred
            ]
            ordered = held + ordered
        return ordered

    def _display_customer_message(self, tool_results: list[ToolResult]) -> str:
        """Use acknowledged display text only after the whole batch succeeds."""
        return self._strip_think_tags(
            "\n".join(
                message.strip()
                for result in tool_results
                if isinstance(message := result.metadata.get("customer_message"), str)
                and message.strip()
            )
        )

    def _reached_customer_message(self, tool_results: list[ToolResult]) -> str:
        """The latest successful tool's customer message, or empty."""
        for result in reversed(tool_results):
            message = result.metadata.get("customer_message")
            if result.success and isinstance(message, str) and message.strip():
                return self._strip_think_tags(message)
        return ""

    def _completed_display_batch(
        self,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> bool:
        """Whether every successful tool in the batch acknowledges completion."""
        if len(tool_calls) != len(tool_results) or not tool_results:
            return False
        if any(
            result.metadata.get("continue_agent") is True for result in tool_results
        ):
            return False
        display_tools = {
            tool.spec.name
            for tool in self._tools
            if tool.spec.metadata.get("displays", False)
        }
        return all(result.success for result in tool_results) and all(
            call.name in display_tools
            or result.metadata.get("completed_display") is True
            or result.metadata.get("complete_turn") is True
            for call, result in zip(tool_calls, tool_results)
        )

    @staticmethod
    def _append_function_tool_results(
        messages: list[Message],
        all_tool_results: list[ToolResult],
        ordered_results: list[tuple[ToolCall, ToolResult]],
    ) -> None:
        for tc, tool_result in ordered_results:
            all_tool_results.append(tool_result)
            model_content = (
                project_tool_content(tool_result.content)
                if tc.name == "http_request"
                else tool_result.content
            )
            messages.append(
                Message(
                    role=Role.TOOL,
                    content=(
                        f"Tool execution success={str(tool_result.success).lower()}\n"
                        f"{model_content}"
                    ),
                    tool_call_id=tc.id,
                    name=tc.name,
                )
            )


class _VisibleTextFilter:
    _OPEN_TAG = "<think>"
    _CLOSE_TAG = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside_think = False
        self._emitted_visible_text = False

    def feed(self, content: str) -> list[str]:
        visible = self._feed_visible(content)
        return [visible] if visible else []

    def _feed_visible(self, content: str) -> str:
        self._buffer += content
        visible_parts: list[str] = []

        while self._buffer:
            lowered = self._buffer.casefold()
            if self._inside_think:
                close_index = lowered.find(self._CLOSE_TAG)
                if close_index < 0:
                    keep = _partial_tag_suffix_length(
                        lowered,
                        self._CLOSE_TAG,
                    )
                    self._buffer = self._buffer[-keep:] if keep else ""
                    break
                self._buffer = self._buffer[close_index + len(self._CLOSE_TAG) :]
                self._inside_think = False
                continue

            open_index = lowered.find(self._OPEN_TAG)
            close_index = lowered.find(self._CLOSE_TAG)
            if close_index >= 0 and (open_index < 0 or close_index < open_index):
                visible_parts.clear()
                self._buffer = self._buffer[close_index + len(self._CLOSE_TAG) :]
                continue

            if open_index >= 0:
                visible_parts.append(self._buffer[:open_index])
                self._buffer = self._buffer[open_index + len(self._OPEN_TAG) :]
                self._inside_think = True
                continue

            partial_open = _partial_tag_suffix_length(lowered, self._OPEN_TAG)
            partial_close = _partial_tag_suffix_length(lowered, self._CLOSE_TAG)
            keep = max(partial_open, partial_close)
            if partial_close and not self._emitted_visible_text:
                break
            if keep:
                visible_parts.append(self._buffer[:-keep])
                self._buffer = self._buffer[-keep:]
            else:
                visible_parts.append(self._buffer)
                self._buffer = ""
            break

        visible = "".join(visible_parts)
        if visible:
            self._emitted_visible_text = True
        return visible

    def finish(self) -> list[str]:
        visible_parts: list[str] = []
        if self._inside_think:
            self._buffer = ""
            return visible_parts
        visible = self._buffer
        self._buffer = ""
        if visible.casefold() in {
            self._OPEN_TAG[:length] for length in range(1, len(self._OPEN_TAG))
        } | {self._CLOSE_TAG[:length] for length in range(1, len(self._CLOSE_TAG))}:
            return visible_parts
        if visible:
            visible_parts.append(visible)
        return visible_parts


def _partial_tag_suffix_length(text: str, tag: str) -> int:
    for length in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:length]):
            return length
    return 0


def _build_tool_calls(
    fragments: dict[int, dict[str, Any]],
) -> list[ToolCall]:
    return [
        ToolCall(
            id=fragment["id"] or f"call_{index}",
            name=fragment["function"]["name"],
            arguments=fragment["function"]["arguments"] or "{}",
        )
        for index, fragment in sorted(fragments.items())
    ]


def _requires_pending_approval(value: Any) -> bool:
    metadata = (
        value.get("metadata")
        if isinstance(value, dict)
        else getattr(value, "metadata", None)
    )
    return isinstance(metadata, dict) and bool(metadata.get("pending_approval"))


__all__ = ["OrchestratorAgent"]
