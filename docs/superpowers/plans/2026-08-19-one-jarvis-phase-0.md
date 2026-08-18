# One Jarvis Phase 0 — Shared Context Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Chat, Voice and CLI assemble their Agent context through one
function, and give `JarvisSystem` ownership of the agent runtime, closing three
live defects without adding any user-facing feature.

**Architecture:** A single pure-ish function `compose_context()` in a new
module `openjarvis/turn.py` performs identity grounding, fact loading and
memory injection in one fixed order. The three existing call sites delete their
inline blocks and call it. Separately, `NativeAgentRuntime` stops being built in
`server/app.py` and becomes a cached derived property on `JarvisSystem`, so it
can never disagree with the agent it wraps.

**Tech Stack:** Python 3.11+, pytest, FastAPI, dataclasses.

**Spec:** `docs/superpowers/specs/2026-08-19-one-jarvis-goal-execution-design.md`

## Global Constraints

- Do not modify `agents/orchestrator.py`, `agents/runtime.py` or
  `agents/_stubs.py`. They carry the heaviest fork drift (+561, +181, +284).
- Do not modify `system/builder.py` or `cli/serve.py`. The builder resolves
  `agent_name` only; the canonical Agent is constructed after `build()` by
  `cli/serve.py:320-339`, which is the single site that ever sets
  `system.agent`.
- Do not modify `workflow/engine.py`.
- The `request_body.tools` branch of `/v1/chat/completions` must keep routing to
  `_handle_stream_tools` / `_handle_direct`. It is raw OpenAI-compatible
  passthrough (guard for issue #414); never route it through a server-side
  agent.
- Run tests with `.venv/bin/pytest` from the repo root
  `/home/robber/Work/jarvis/OpenJarvis-v2`.
- Branch is `native/runtime`. Do not push, merge, reset, or stage broadly.
  Commit only the files each task names.

## Background an engineer needs

**Where the three implementations live today.**

| Path | File | Lines |
|---|---|---|
| CLI / channels | `src/openjarvis/system/orchestrator.py` | 44-64 |
| Chat HTTP | `src/openjarvis/server/routes.py` | 118-185 |
| Voice | `src/openjarvis/server/voice/runtime.py` | 52-71, consumed at `server/voice/llm.py:120-131` |

**The two identity sources that disagree.** `_ensure_identity_prompt`
(`server/routes.py:58`) builds a prompt with `SystemPromptBuilder`, which
includes the persona files SOUL.md / MEMORY.md / USER.md. It is called from four
sites: `routes.py:144, 415, 719, 848` — all engine-direct paths. The agent
paths (which Voice uses, and which chat uses when an agent is configured) get
`resolve_agent_system_prompt(config.agent)` instead
(`cli/serve.py:334`), which reads only inline `agent.system_prompt` or
`agent.system_prompt_path`. No `prompt_builder` is wired anywhere in
`cli/serve.py` or `system/builder.py`. That divergence is defect 1.

**Facts.** `load_configured_facts(config)` (`memory/store.py:208`) returns
durable facts. `system/orchestrator.py:57` uses it. `server/routes.py:135` uses
`memory_service.list_facts()` instead. `server/voice/runtime.py` passes no facts
at all. That is defect 2.

**Ordering constraint that must be preserved.** `inject_context`
(`tools/storage/context.py:107`) *prepends* a system message tagged
`metadata={"memory_context": True}` (`context.py:73`). A surface prompt applied
before `inject_context` would end up behind the recalled document. The voice
path records this at `server/voice/llm.py:133-139`. `compose_context` must apply
`system_prompt` **after** `inject_context`, so it lands in front of the recall
message.

**Why a doubled system message is safe.** `BaseAgent._build_messages`
(`agents/_stubs.py:348-408`) folds any context message tagged `memory_context`
into the effective system prompt and drops it from the history list
(`_stubs.py:384-403`). Other system messages in the context are passed through
as ordinary history. Both are existing behaviour; this plan does not change it.

## File Structure

| File | Responsibility |
|---|---|
| `src/openjarvis/turn.py` (new) | `compose_context()` — the one context assembly used by every surface. One module, no package, no class. |
| `tests/turn/test_compose_context.py` (new) | Unit tests for the function in isolation. |
| `tests/turn/test_surface_parity.py` (new) | The cross-surface invariant: Chat and Voice compose identically apart from the surface prompt. |
| `src/openjarvis/system/orchestrator.py` | Loses its inline recall block; calls `compose_context`. |
| `src/openjarvis/server/routes.py` | Loses its inline recall block; calls `compose_context`. |
| `src/openjarvis/server/voice/runtime.py` | Loses `_memory_recall`; keeps `VOICE_SYSTEM_PROMPT` and the audio tuning constants. |
| `src/openjarvis/server/voice/llm.py` | `agent_input` calls `compose_context`. |
| `src/openjarvis/server/voice/routes.py` | Stops assembling a `recall` tuple; passes config and backend. |
| `src/openjarvis/system/core.py` | Gains the `agent_runtime` derived property and its snapshot helper. |
| `src/openjarvis/server/app.py` | Stops constructing a runtime; reads `system.agent_runtime`. |

---

### Task 1: `compose_context()`

**Files:**
- Create: `src/openjarvis/turn.py`
- Create: `tests/turn/test_compose_context.py`

**Interfaces:**
- Consumes: `inject_context`, `ContextConfig` from
  `openjarvis.tools.storage.context`; `load_configured_facts` from
  `openjarvis.memory.store`; `SystemPromptBuilder` from
  `openjarvis.prompt.builder`; `Message`, `Role` from `openjarvis.core.types`.
- Produces:
  ```python
  def compose_context(
      query: str,
      messages: list[Message],
      *,
      backend: Any | None,
      config: Any,
      facts: Sequence[Any] | None = None,
      system_prompt: str | None = None,
      recall: bool = True,
  ) -> list[Message]
  ```
  Returns a new list. Never mutates `messages`.

- [ ] **Step 1: Create the test directory and write the first failing test**

Create `tests/turn/test_compose_context.py`:

```python
"""compose_context is the one context assembly every surface uses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjarvis.core.types import Message, Role
from openjarvis.turn import compose_context


def _config(*, context_from_memory: bool = False, default_system_prompt: str = ""):
    """A config stub with only the attributes compose_context reads."""
    return SimpleNamespace(
        agent=SimpleNamespace(
            context_from_memory=context_from_memory,
            default_system_prompt=default_system_prompt,
        ),
        memory=SimpleNamespace(
            enabled=False,
            context_top_k=5,
            context_min_score=0.0,
            context_max_tokens=2048,
        ),
        memory_files=None,
        system_prompt=None,
    )


def test_returns_a_new_list_and_does_not_mutate_input():
    original = [Message(role=Role.USER, content="hi")]
    result = compose_context(
        "hi", original, backend=None, config=_config(), facts=()
    )
    assert result is not original
    assert original == [Message(role=Role.USER, content="hi")]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/turn/test_compose_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openjarvis.turn'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/openjarvis/turn.py`:

```python
"""One context assembly, shared by every surface.

Chat, Voice and the CLI each used to build the Agent's message list their own
way, and the three had already drifted: only one applied persona-based identity
grounding, only two loaded durable facts, and each gated recall differently.
This module holds the single ordering they all use now.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from openjarvis.core.types import Message, Role

logger = logging.getLogger(__name__)


def compose_context(
    query: str,
    messages: list[Message],
    *,
    backend: Any | None,
    config: Any,
    facts: Optional[Sequence[Any]] = None,
    system_prompt: Optional[str] = None,
    recall: bool = True,
) -> list[Message]:
    """Assemble the message list a surface hands to the Agent.

    The order is load-bearing. ``inject_context`` prepends a message of its
    own, so a surface prompt applied before it would end up *behind* the
    recalled document -- the instruction describing how to answer would arrive
    after the material it governs. Surface prompt and identity are therefore
    prepended afterwards, identity outermost.

    Parameters
    ----------
    query:
        The user's latest turn, used as the retrieval query.
    messages:
        Existing conversation messages. Never mutated.
    backend:
        Memory backend to retrieve from, or ``None``.
    config:
        The loaded ``JarvisConfig``.
    facts:
        Durable facts. ``None`` means load them from *config*; pass an explicit
        sequence (including ``()``) to override.
    system_prompt:
        The surface's own instruction, e.g. ``VOICE_SYSTEM_PROMPT``.
    recall:
        ``False`` skips retrieval entirely, for callers such as
        ``JarvisSystem.ask(context=False)``.
    """
    caller_supplied_system = any(m.role == Role.SYSTEM for m in messages)
    identity = None if caller_supplied_system else _identity_prompt(config)

    result = list(messages)

    if recall and getattr(getattr(config, "agent", None), "context_from_memory", False):
        result = _inject(query, result, backend, config, facts)

    if system_prompt:
        result = [Message(role=Role.SYSTEM, content=system_prompt), *result]

    if identity:
        result = [Message(role=Role.SYSTEM, content=identity), *result]

    return result


def _identity_prompt(config: Any) -> str:
    """Resolve the persona-grounded identity prompt, or "" when unavailable.

    Without this the model answers from its training identity ("I'm Claude",
    "I am Qwen") -- issue #540. Resolution failure degrades to no grounding
    rather than costing the caller their answer, but is logged.
    """
    try:
        from openjarvis.prompt.builder import SystemPromptBuilder

        builder = SystemPromptBuilder(
            agent_template=getattr(config.agent, "default_system_prompt", "") or "",
            memory_files_config=getattr(config, "memory_files", None),
            system_prompt_config=getattr(config, "system_prompt", None),
        )
        return builder.build() or ""
    except Exception:
        logger.debug(
            "Identity prompt resolution failed; composing without grounding",
            exc_info=True,
        )
        return ""


def _inject(
    query: str,
    messages: list[Message],
    backend: Any | None,
    config: Any,
    facts: Optional[Sequence[Any]],
) -> list[Message]:
    """Retrieve and prepend memory context. Degrades to *messages* on failure."""
    try:
        from openjarvis.tools.storage.context import ContextConfig, inject_context

        if facts is None:
            from openjarvis.memory.store import load_configured_facts

            facts = load_configured_facts(config)

        memory = config.memory
        return inject_context(
            query,
            messages,
            backend,
            config=ContextConfig(
                top_k=memory.context_top_k,
                min_score=memory.context_min_score,
                max_context_tokens=memory.context_max_tokens,
            ),
            facts=facts,
        )
    except Exception:
        # Recall is an enhancement; a dead memory backend must not cost the
        # user their answer.
        logger.warning("Memory context injection failed", exc_info=True)
        return messages


__all__ = ["compose_context"]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/turn/test_compose_context.py -v`
Expected: PASS

- [ ] **Step 5: Add the remaining behaviour tests**

Append to `tests/turn/test_compose_context.py`:

```python
def test_identity_is_prepended_when_no_system_message_present(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build",
        lambda self: "You are Jarvis.",
    )
    result = compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=None,
        config=_config(),
        facts=(),
    )
    assert result[0].role == Role.SYSTEM
    assert result[0].content == "You are Jarvis."


def test_identity_is_skipped_when_the_caller_supplied_one(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build",
        lambda self: "You are Jarvis.",
    )
    caller = [
        Message(role=Role.SYSTEM, content="You are a pirate."),
        Message(role=Role.USER, content="hi"),
    ]
    result = compose_context("hi", caller, backend=None, config=_config(), facts=())
    assert [m.content for m in result] == ["You are a pirate.", "hi"]


def test_identity_failure_degrades_to_no_grounding(monkeypatch):
    def _boom(self):
        raise RuntimeError("persona file unreadable")

    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", _boom
    )
    result = compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=None,
        config=_config(),
        facts=(),
    )
    assert [m.role for m in result] == [Role.USER]


def test_surface_prompt_lands_in_front_of_the_recalled_document(monkeypatch):
    """The instruction on how to answer must precede the material it governs."""
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    def _fake_inject(query, messages, backend, *, config=None, facts=()):
        recalled = Message(
            role=Role.SYSTEM,
            content="RECALLED",
            metadata={"memory_context": True},
        )
        return [recalled, *messages]

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _fake_inject
    )
    result = compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=object(),
        config=_config(context_from_memory=True),
        facts=(),
        system_prompt="Speak plainly.",
    )
    assert [m.content for m in result] == ["Speak plainly.", "RECALLED", "hi"]


def test_recall_false_skips_retrieval_entirely(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    def _must_not_run(*args, **kwargs):
        raise AssertionError("retrieval ran despite recall=False")

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _must_not_run
    )
    result = compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=object(),
        config=_config(context_from_memory=True),
        facts=(),
        recall=False,
    )
    assert [m.content for m in result] == ["hi"]


def test_context_from_memory_off_skips_retrieval(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    def _must_not_run(*args, **kwargs):
        raise AssertionError("retrieval ran despite context_from_memory=False")

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _must_not_run
    )
    result = compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=object(),
        config=_config(context_from_memory=False),
        facts=(),
    )
    assert [m.content for m in result] == ["hi"]


def test_facts_none_loads_them_from_config(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )
    seen = {}

    def _fake_inject(query, messages, backend, *, config=None, facts=()):
        seen["facts"] = facts
        return messages

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _fake_inject
    )
    monkeypatch.setattr(
        "openjarvis.memory.store.load_configured_facts", lambda config: ["a fact"]
    )
    compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=object(),
        config=_config(context_from_memory=True),
    )
    assert seen["facts"] == ["a fact"]


def test_retrieval_failure_degrades_to_the_original_messages(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("backend is dead")

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _boom
    )
    result = compose_context(
        "hi",
        [Message(role=Role.USER, content="hi")],
        backend=object(),
        config=_config(context_from_memory=True),
        facts=(),
    )
    assert [m.content for m in result] == ["hi"]
```

- [ ] **Step 6: Run the full test file**

Run: `.venv/bin/pytest tests/turn/test_compose_context.py -v`
Expected: all 9 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/openjarvis/turn.py tests/turn/test_compose_context.py
git commit -m "feat(turn): one context assembly for every surface

Chat, Voice and the CLI each built the Agent's message list their own way
and the three had drifted. compose_context fixes the ordering in one
place: identity, then surface prompt, then recall, so the instruction on
how to answer precedes the material it governs.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Route `JarvisSystem.ask()` through `compose_context`

**Files:**
- Modify: `src/openjarvis/system/orchestrator.py:36-67`
- Test: `tests/system/test_orchestrator_composition.py` (create)

**Interfaces:**
- Consumes: `compose_context` from Task 1.
- Produces: no new public names. `QueryOrchestrator.ask` keeps its signature.

- [ ] **Step 1: Write the failing test**

Create `tests/system/test_orchestrator_composition.py`:

```python
"""ask() composes context through the shared function, not its own block."""

from __future__ import annotations

from types import SimpleNamespace

from openjarvis.core.types import Message, Role
from openjarvis.system.orchestrator import QueryOrchestrator


class _FakeEngine:
    def __init__(self):
        self.messages = None

    def generate(self, messages, **kwargs):
        self.messages = messages
        return {"content": "ok", "usage": {}}


def _system(engine):
    """A minimal OrchestratorDeps stand-in (see system/protocols.py)."""
    return SimpleNamespace(
        config=SimpleNamespace(
            intelligence=SimpleNamespace(temperature=0.7, max_tokens=100),
            agent=SimpleNamespace(
                context_from_memory=False,
                default_system_prompt="",
            ),
            memory=SimpleNamespace(
                enabled=False,
                context_top_k=5,
                context_min_score=0.0,
                context_max_tokens=2048,
            ),
            memory_files=None,
            system_prompt=None,
        ),
        engine=engine,
        engine_key="fake",
        model="fake-model",
        agent_name="none",
        tools=[],
        memory_backend=None,
        bus=None,
    )


def test_ask_grounds_identity_through_compose_context(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build",
        lambda self: "You are Jarvis.",
    )
    engine = _FakeEngine()
    QueryOrchestrator(_system(engine)).ask("who are you?")

    assert engine.messages[0].role == Role.SYSTEM
    assert engine.messages[0].content == "You are Jarvis."
    assert engine.messages[-1].content == "who are you?"


def test_ask_with_context_false_skips_retrieval(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    def _must_not_run(*args, **kwargs):
        raise AssertionError("retrieval ran despite context=False")

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _must_not_run
    )
    engine = _FakeEngine()
    system = _system(engine)
    system.config.agent.context_from_memory = True
    QueryOrchestrator(system).ask("hi", context=False)

    assert [m.content for m in engine.messages] == ["hi"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/system/test_orchestrator_composition.py -v`
Expected: `test_ask_grounds_identity_through_compose_context` FAILS — the first
message is the user turn, because `ask()` never applied identity grounding.

- [ ] **Step 3: Replace the inline block**

In `src/openjarvis/system/orchestrator.py`, replace lines 42-66 (from
`messages = [Message(...)]` through the `except Exception ... logger.warning`
that closes the memory-injection block) with:

```python
        from openjarvis.turn import compose_context

        messages = compose_context(
            query,
            [Message(role=Role.USER, content=query)],
            backend=s.memory_backend,
            config=s.config,
            recall=context,
        )
```

Delete the now-unused imports of `load_configured_facts`, `ContextConfig` and
`inject_context` from inside `ask` — they were local imports inside the removed
`try` block, so nothing at module scope changes.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/system/test_orchestrator_composition.py -v`
Expected: both PASS

- [ ] **Step 5: Run the existing orchestrator and system tests for regressions**

Run: `.venv/bin/pytest tests/system/ tests/sdk/ -v`
Expected: PASS. If a pre-existing test asserts that `ask()` produces exactly one
message, it is asserting the defect; update it to allow the identity system
message and note that in the commit body.

- [ ] **Step 6: Commit**

```bash
git add src/openjarvis/system/orchestrator.py tests/system/test_orchestrator_composition.py
git commit -m "refactor(system): ask() composes through the shared function

The CLI path never applied persona-based identity grounding, so 'who are
you?' was answered from the model's training identity. Routing through
compose_context gives it the same grounding the engine-direct HTTP paths
already had.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Route `/v1/chat/completions` through `compose_context`

**Files:**
- Modify: `src/openjarvis/server/routes.py:118-185`
- Test: `tests/server/test_chat_composition.py` (create)

**Interfaces:**
- Consumes: `compose_context` from Task 1.
- Produces: no new public names. `_ensure_identity_prompt` stays in place — it
  is still called from `routes.py:415, 719, 848` and those sites are out of
  scope for this task.

- [ ] **Step 1: Write the failing test**

Create `tests/server/test_chat_composition.py`:

```python
"""The chat endpoint composes context through the shared function."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjarvis.server.models import ChatCompletionRequest, ChatMessage


def _app_config(*, context_from_memory: bool = True):
    return SimpleNamespace(
        agent=SimpleNamespace(
            context_from_memory=context_from_memory,
            default_system_prompt="",
        ),
        memory=SimpleNamespace(
            enabled=False,
            context_top_k=5,
            context_min_score=0.0,
            context_max_tokens=2048,
        ),
        memory_files=None,
        system_prompt=None,
    )


def test_chat_passes_service_facts_to_compose_context(monkeypatch):
    """The endpoint's facts come from memory_service, not from config."""
    from openjarvis.server import routes

    seen = {}

    def _fake_compose(query, messages, **kwargs):
        seen.update(kwargs)
        seen["query"] = query
        return messages

    monkeypatch.setattr(routes, "compose_context", _fake_compose)

    request_body = ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="user", content="what do I like?")],
    )
    memory_service = SimpleNamespace(list_facts=lambda: ["likes latte"])

    routes._compose_request_messages(
        request_body,
        config=_app_config(),
        memory_backend=None,
        memory_service=memory_service,
    )

    assert seen["query"] == "what do I like?"
    assert seen["facts"] == ["likes latte"]


def test_chat_composition_is_a_no_op_without_a_user_message(monkeypatch):
    from openjarvis.server import routes

    def _must_not_run(*args, **kwargs):
        raise AssertionError("composed with no user turn to retrieve on")

    monkeypatch.setattr(routes, "compose_context", _must_not_run)

    request_body = ChatCompletionRequest(
        model="m",
        messages=[ChatMessage(role="assistant", content="hello")],
    )
    routes._compose_request_messages(
        request_body,
        config=_app_config(),
        memory_backend=None,
        memory_service=None,
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/server/test_chat_composition.py -v`
Expected: FAIL — `AttributeError: module 'openjarvis.server.routes' has no
attribute '_compose_request_messages'`

- [ ] **Step 3: Extract the block into a named function and call the shared one**

In `src/openjarvis/server/routes.py`, add this function immediately after
`_ensure_identity_prompt` (which ends at line 111):

```python
def _compose_request_messages(
    request_body,
    *,
    config,
    memory_backend,
    memory_service,
) -> None:
    """Rewrite ``request_body.messages`` with the shared context assembly.

    Extracted from the endpoint body so it is testable without a live app, and
    routed through ``compose_context`` so this path cannot drift from Voice and
    the CLI again. Facts come from ``memory_service`` here rather than from
    config, because the server owns a live service the CLI does not have.
    """
    if config is None or not request_body.messages:
        return

    query_text = ""
    for message in reversed(request_body.messages):
        if message.role == "user" and message.content:
            query_text = message.content
            break
    if not query_text:
        return

    facts = memory_service.list_facts() if memory_service is not None else []
    composed = compose_context(
        query_text,
        _to_messages(request_body.messages),
        backend=memory_backend,
        config=config,
        facts=facts,
    )

    from openjarvis.server.models import ChatMessage

    request_body.messages = [
        ChatMessage(
            role=message.role.value,
            content=message.content,
            name=message.name,
            tool_calls=[
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                }
                for tool_call in (message.tool_calls or [])
            ]
            or None,
            tool_call_id=getattr(message, "tool_call_id", None),
        )
        for message in composed
    ]
```

Add the import at module scope, beside the other `openjarvis` imports at the
top of `routes.py`:

```python
from openjarvis.turn import compose_context
```

Then in `chat_completions`, replace the whole block from line 118
(`if (\n        config is not None`) through the closing
`logging.getLogger("openjarvis.server").debug("Memory context injection failed", ...)`
at line 185 with:

```python
    _compose_request_messages(
        request_body,
        config=config,
        memory_backend=memory_backend,
        memory_service=getattr(request.app.state, "memory_service", None),
    )
```

Note that `compose_context` applies its own `context_from_memory` gate, so the
outer `if config.agent.context_from_memory` that used to wrap this block is no
longer needed and must not be re-added — keeping it would skip identity
grounding whenever memory recall is off, which is the behaviour being fixed.

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/server/test_chat_composition.py -v`
Expected: both PASS

- [ ] **Step 5: Run the existing server tests for regressions**

Run: `.venv/bin/pytest tests/server/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/openjarvis/server/routes.py tests/server/test_chat_composition.py
git commit -m "refactor(server): chat composes through the shared function

Identity grounding was gated behind context_from_memory here, so turning
recall off also silently dropped the persona prompt. compose_context
applies its own gate, and the endpoint block becomes a named, testable
function.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Route Voice through `compose_context`

**Files:**
- Modify: `src/openjarvis/server/voice/runtime.py:52-74`
- Modify: `src/openjarvis/server/voice/llm.py:95-141, 168-197`
- Modify: `src/openjarvis/server/voice/routes.py:266-274, 324-343`
- Test: `tests/server/test_voice_composition.py` (create)

**Interfaces:**
- Consumes: `compose_context` from Task 1.
- Produces: `agent_input(context, recall)` changes its second parameter's
  meaning. It is now:
  ```python
  def agent_input(
      context: Any,
      recall: tuple[Any, Any] | None = None,
  ) -> tuple[str, AgentContext]
  ```
  where `recall` is `(memory_backend, config)` rather than
  `(memory_backend, ContextConfig)`. `OpenJarvisLLMService.__init__` keeps its
  `recall=` keyword with the same new meaning.

- [ ] **Step 1: Write the failing test**

Create `tests/server/test_voice_composition.py`:

```python
"""Voice composes context through the shared function, facts included."""

from __future__ import annotations

from types import SimpleNamespace

from openjarvis.core.types import Role


def _config():
    return SimpleNamespace(
        agent=SimpleNamespace(
            context_from_memory=True,
            default_system_prompt="",
        ),
        memory=SimpleNamespace(
            enabled=False,
            context_top_k=5,
            context_min_score=0.0,
            context_max_tokens=2048,
        ),
        memory_files=None,
        system_prompt=None,
    )


class _PipecatContext:
    """The shape agent_input reads: a get_messages() returning dicts."""

    def __init__(self, messages):
        self._messages = messages

    def get_messages(self):
        return self._messages


def test_voice_grounds_identity_like_chat(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build",
        lambda self: "You are Jarvis.",
    )
    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context",
        lambda query, messages, backend, **kwargs: messages,
    )

    from openjarvis.server.voice.llm import agent_input

    prompt, context = agent_input(
        _PipecatContext([{"role": "user", "content": "who are you?"}]),
        (None, _config()),
    )

    assert prompt == "who are you?"
    contents = [m.content for m in context.conversation.messages]
    assert "You are Jarvis." in contents


def test_voice_loads_facts_from_config(monkeypatch):
    """Chat knew the user's preferences and Voice did not. Both do now."""
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )
    monkeypatch.setattr(
        "openjarvis.memory.store.load_configured_facts",
        lambda config: ["likes latte"],
    )
    seen = {}

    def _fake_inject(query, messages, backend, *, config=None, facts=()):
        seen["facts"] = facts
        return messages

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _fake_inject
    )

    from openjarvis.server.voice.llm import agent_input

    agent_input(
        _PipecatContext([{"role": "user", "content": "what do I like?"}]),
        (None, _config()),
    )

    assert seen["facts"] == ["likes latte"]


def test_voice_system_prompt_precedes_the_recalled_document(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    def _fake_inject(query, messages, backend, *, config=None, facts=()):
        from openjarvis.core.types import Message

        return [
            Message(
                role=Role.SYSTEM,
                content="RECALLED",
                metadata={"memory_context": True},
            ),
            *messages,
        ]

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _fake_inject
    )

    from openjarvis.server.voice.llm import agent_input
    from openjarvis.server.voice.runtime import VOICE_SYSTEM_PROMPT

    _, context = agent_input(
        _PipecatContext([{"role": "user", "content": "hi"}]),
        (None, _config()),
    )

    contents = [m.content for m in context.conversation.messages]
    assert contents.index(VOICE_SYSTEM_PROMPT) < contents.index("RECALLED")


def test_voice_without_recall_still_gets_its_surface_prompt(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build", lambda self: ""
    )

    from openjarvis.server.voice.llm import agent_input
    from openjarvis.server.voice.runtime import VOICE_SYSTEM_PROMPT

    _, context = agent_input(
        _PipecatContext([{"role": "user", "content": "hi"}]), None
    )

    contents = [m.content for m in context.conversation.messages]
    assert VOICE_SYSTEM_PROMPT in contents
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/server/test_voice_composition.py -v`
Expected: `test_voice_grounds_identity_like_chat` and
`test_voice_loads_facts_from_config` FAIL — Voice applies neither today.

If `pipecat` is not installed in this environment, these tests will fail on
import instead. In that case add `pytest.importorskip("pipecat")` at the top of
the file and record in the commit body that the file is skipped without the
`voice` extra.

- [ ] **Step 3: Delete `_memory_recall`**

In `src/openjarvis/server/voice/runtime.py`, delete the `_memory_recall`
function (lines 52-71) and change the trailing export to:

```python
__all__ = ["VOICE_SYSTEM_PROMPT"]
```

Leave `VOICE_SYSTEM_PROMPT` and every audio-tuning constant in the file
untouched.

- [ ] **Step 4: Rewrite `agent_input` to call the shared function**

In `src/openjarvis/server/voice/llm.py`, replace the body of `agent_input`
after the history list is built (lines 120-141) with:

```python
    from openjarvis.server.voice.runtime import VOICE_SYSTEM_PROMPT
    from openjarvis.turn import compose_context

    if recall is not None:
        backend, config = recall
        history = compose_context(
            prompt,
            history,
            backend=backend,
            config=config,
            system_prompt=VOICE_SYSTEM_PROMPT,
        )
    else:
        history = [
            Message(role=Role.SYSTEM, content=VOICE_SYSTEM_PROMPT),
            *history,
        ]

    return prompt, AgentContext(conversation=Conversation(messages=history))
```

Update the docstring's second paragraph to read:

```
    When ``recall`` is given it is ``(memory_backend, config)`` and the whole
    context is assembled by ``compose_context`` -- the same identity grounding,
    facts and retrieval every other surface gets. The voice instruction is
    passed as the surface prompt so it lands in front of any recalled document.
```

Delete the now-unused `VOICE_SYSTEM_PROMPT` import at the top of the file
(line 28) if it is only referenced inside `agent_input`; keep it if any other
function in the module uses it.

- [ ] **Step 5: Pass config instead of ContextConfig from the route**

In `src/openjarvis/server/voice/routes.py`, replace lines 266-274 with:

```python
    # The Agent's whole context -- identity, facts, retrieval -- is assembled by
    # compose_context, which owns the config gates. The route only supplies the
    # two things it holds.
    app_config = getattr(request.app.state, "config", None)
    memory_backend = getattr(request.app.state, "memory_backend", None)
    recall = (memory_backend, app_config) if app_config is not None else None
```

Nothing else in that function changes: `recall` is already passed to
`build_voice_pipeline(..., recall=recall)` at line 336 and reaches
`OpenJarvisLLMService(binding, recall=recall)` in
`server/voice/pipeline.py:77`.

- [ ] **Step 6: Update the `OpenJarvisLLMService` docstring for the new tuple**

In `src/openjarvis/server/voice/llm.py`, in `OpenJarvisLLMService.__init__`,
change the `recall` parameter's inline meaning by editing the class docstring to
add:

```
    ``recall`` is ``(memory_backend, config)`` or ``None``; it is handed
    straight to ``agent_input``.
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `.venv/bin/pytest tests/server/test_voice_composition.py -v`
Expected: all four PASS

- [ ] **Step 8: Run the existing voice tests for regressions**

Run: `.venv/bin/pytest tests/server/test_voice_routes.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/openjarvis/server/voice/runtime.py src/openjarvis/server/voice/llm.py src/openjarvis/server/voice/routes.py tests/server/test_voice_composition.py
git commit -m "fix(voice): compose context the way every other surface does

Voice had no identity grounding and never loaded durable facts, so it
answered 'who are you?' differently from Chat and could not recall a
preference Chat knew. _memory_recall is replaced by the shared assembly.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `agent_runtime` as a derived property on `JarvisSystem`

**Files:**
- Modify: `src/openjarvis/system/core.py`
- Modify: `src/openjarvis/server/app.py:306-325`
- Modify: `src/openjarvis/server/voice/routes.py:242-245`
- Test: `tests/system/test_agent_runtime_property.py` (create)

**Interfaces:**
- Produces:
  ```python
  JarvisSystem.agent_runtime -> Optional[NativeAgentRuntime]
  ```
  Returns `None` when `self.agent` is `None`. Otherwise builds once and caches
  in `self.__dict__["_agent_runtime"]`, mirroring `_get_orchestrator`
  (`system/core.py:127-134`).

- [ ] **Step 1: Write the failing test**

Create `tests/system/test_agent_runtime_property.py`:

```python
"""The runtime follows the agent, so the two can never disagree."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from openjarvis.core.events import EventBus
from openjarvis.system.core import JarvisSystem


def _config():
    return SimpleNamespace(
        agent=SimpleNamespace(context_from_memory=True),
        memory=SimpleNamespace(
            context_top_k=7,
            context_min_score=0.25,
            context_max_tokens=1024,
        ),
    )


def _system(**kwargs):
    return JarvisSystem(
        config=_config(),
        bus=EventBus(),
        engine=MagicMock(),
        engine_key="fake",
        model="fake-model",
        **kwargs,
    )


def test_no_agent_means_no_runtime():
    assert _system().agent_runtime is None


def test_runtime_wraps_the_agent_and_is_cached():
    agent = MagicMock()
    system = _system(agent=agent)

    runtime = system.agent_runtime

    assert runtime is not None
    assert runtime.agent is agent
    assert system.agent_runtime is runtime


def test_data_source_snapshot_comes_from_the_system():
    agent = MagicMock()
    backend = MagicMock()
    backend.backend_id = "sqlite"
    system = _system(agent=agent, memory_backend=backend)

    snapshot = system.agent_runtime.bind(model="m").snapshot
    sources = snapshot.data_source_configuration

    assert sources.enabled is True
    assert sources.backend_id == "sqlite"
    assert sources.top_k == 7
    assert sources.min_score == 0.25
    assert sources.max_context_tokens == 1024


def test_snapshot_is_disabled_without_a_backend():
    system = _system(agent=MagicMock())
    sources = system.agent_runtime.bind(model="m").snapshot.data_source_configuration
    assert sources.enabled is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/system/test_agent_runtime_property.py -v`
Expected: FAIL — `AttributeError: 'JarvisSystem' object has no attribute
'agent_runtime'`

- [ ] **Step 3: Add the property to `JarvisSystem`**

In `src/openjarvis/system/core.py`, add to the `TYPE_CHECKING` import block:

```python
    from openjarvis.agents.runtime import NativeAgentRuntime
```

Then insert this property immediately after the `scheduling` property (which
ends at line 125), before `_get_orchestrator`:

```python
    @property
    def agent_runtime(self) -> Optional[NativeAgentRuntime]:
        """The realtime execution runtime for this system's canonical Agent.

        Derived rather than assigned, so the runtime cannot disagree with the
        agent it wraps. ``SystemBuilder`` cannot build it: at ``build()`` time
        only ``agent_name`` is resolved, and the canonical long-lived Agent is
        constructed afterwards by the serving composition, which assigns it to
        ``self.agent``. Cached the same way ``_get_orchestrator`` caches.
        """
        if self.agent is None:
            return None
        runtime = self.__dict__.get("_agent_runtime")
        if runtime is None:
            from openjarvis.agents.runtime import NativeAgentRuntime

            runtime = NativeAgentRuntime(
                self.agent,
                data_source_configuration=self._data_source_snapshot(),
            )
            self.__dict__["_agent_runtime"] = runtime
        return runtime

    def _data_source_snapshot(self):
        """Describe this system's retrieval configuration for the snapshot."""
        from openjarvis.agents.runtime import DataSourceConfigurationSnapshot

        memory = getattr(self.config, "memory", None)
        return DataSourceConfigurationSnapshot(
            enabled=bool(
                self.memory_backend is not None
                and getattr(
                    getattr(self.config, "agent", None), "context_from_memory", False
                )
            ),
            backend_id=str(getattr(self.memory_backend, "backend_id", "")),
            top_k=int(getattr(memory, "context_top_k", 0)),
            min_score=float(getattr(memory, "context_min_score", 0.0)),
            max_context_tokens=int(getattr(memory, "context_max_tokens", 0)),
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/system/test_agent_runtime_property.py -v`
Expected: all four PASS

- [ ] **Step 5: Stop `create_app` from building a second runtime**

`create_app` has no `system` parameter (`server/app.py:228-250`), and it must
not gain one: `cli/serve.py:497` already does `app.state.system = system`
immediately after the call, before the server starts serving. So the fix is to
delete the eager assignment rather than to re-point it.

In `src/openjarvis/server/app.py`, delete lines 306-325 entirely — the
`from openjarvis.agents.runtime import (...)` block, the `memory` and
`data_sources` locals, and the `app.state.native_agent_runtime = (...)`
assignment. Put this comment where they were:

```python
    # No runtime is built here. JarvisSystem owns it as a derived property, and
    # cli/serve.py attaches the system to app.state right after this call.
    # Building one here would give Voice a different lease and a different
    # staging dict from every other caller.
```

- [ ] **Step 6: Resolve the runtime from the system in the voice route**

In `src/openjarvis/server/voice/routes.py`, replace lines 242-245:

```python
    sessions = getattr(request.app.state, "voice_session_service", None)
    runtime = getattr(request.app.state, "native_agent_runtime", None)
    if sessions is None or runtime is None:
        raise HTTPException(status_code=503, detail="voice_agent_unavailable")
```

with:

```python
    sessions = getattr(request.app.state, "voice_session_service", None)
    system = getattr(request.app.state, "system", None)
    runtime = system.agent_runtime if system is not None else None
    if sessions is None or runtime is None:
        raise HTTPException(status_code=503, detail="voice_agent_unavailable")
```

- [ ] **Step 7: Write the test that pins the single source**

Append to `tests/system/test_agent_runtime_property.py`:

```python
def test_create_app_builds_no_runtime_of_its_own():
    """A second runtime would mean a second lease and a second staging dict."""
    from unittest.mock import MagicMock

    from openjarvis.server.app import create_app

    app = create_app(engine=MagicMock(), model="m", agent=MagicMock())

    assert getattr(app.state, "native_agent_runtime", None) is None
```

- [ ] **Step 8: Run the app wiring and voice route tests**

Run: `.venv/bin/pytest tests/system/test_agent_runtime_property.py tests/server/test_app_wiring.py tests/server/test_voice_routes.py -v`
Expected: PASS. Any existing test that asserts on
`app.state.native_agent_runtime` is asserting the old two-source wiring; update
it to build a `JarvisSystem`, set `app.state.system`, and assert on
`system.agent_runtime` instead.

- [ ] **Step 9: Commit**

```bash
git add src/openjarvis/system/core.py src/openjarvis/server/app.py src/openjarvis/server/voice/routes.py tests/system/test_agent_runtime_property.py
git commit -m "refactor(system): the agent runtime follows the agent

Built in server/app.py, the runtime was visible only to Voice, so Chat
and the CLI could not share its lease or its staging. A derived property
on JarvisSystem keeps it from ever disagreeing with system.agent, and
leaves SystemBuilder and serve.py untouched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The cross-surface parity invariant

**Files:**
- Create: `tests/turn/test_surface_parity.py`

**Interfaces:**
- Consumes: `compose_context` from Task 1, `VOICE_SYSTEM_PROMPT` from
  `openjarvis.server.voice.runtime`.
- Produces: nothing. This is the regression gate for the whole phase.

- [ ] **Step 1: Write the invariant test**

Create `tests/turn/test_surface_parity.py`:

```python
"""Chat and Voice may differ by their surface prompt and by nothing else.

This is the gate for Phase 0. Any future surface that assembles context its
own way will fail here rather than drifting quietly for months, which is how
the three implementations this phase replaced came to disagree.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjarvis.core.types import Message, Role
from openjarvis.turn import compose_context

VOICE_SYSTEM_PROMPT = pytest.importorskip(
    "openjarvis.server.voice.runtime"
).VOICE_SYSTEM_PROMPT


def _config():
    return SimpleNamespace(
        agent=SimpleNamespace(
            context_from_memory=True,
            default_system_prompt="",
        ),
        memory=SimpleNamespace(
            enabled=False,
            context_top_k=5,
            context_min_score=0.0,
            context_max_tokens=2048,
        ),
        memory_files=None,
        system_prompt=None,
    )


@pytest.fixture(autouse=True)
def _stub_composition(monkeypatch):
    monkeypatch.setattr(
        "openjarvis.prompt.builder.SystemPromptBuilder.build",
        lambda self: "You are Jarvis.",
    )
    monkeypatch.setattr(
        "openjarvis.memory.store.load_configured_facts",
        lambda config: ["likes latte"],
    )

    def _fake_inject(query, messages, backend, *, config=None, facts=()):
        return [
            Message(
                role=Role.SYSTEM,
                content=f"RECALLED facts={list(facts)}",
                metadata={"memory_context": True},
            ),
            *messages,
        ]

    monkeypatch.setattr(
        "openjarvis.tools.storage.context.inject_context", _fake_inject
    )


def _compose(system_prompt):
    return compose_context(
        "who are you?",
        [Message(role=Role.USER, content="who are you?")],
        backend=object(),
        config=_config(),
        system_prompt=system_prompt,
    )


def test_chat_and_voice_differ_only_by_the_surface_prompt():
    chat = [m.content for m in _compose(None)]
    voice = [m.content for m in _compose(VOICE_SYSTEM_PROMPT)]

    assert voice == [voice[0], VOICE_SYSTEM_PROMPT, *chat[1:]]
    assert [c for c in voice if c != VOICE_SYSTEM_PROMPT] == chat


def test_both_surfaces_get_identity_grounding():
    for composed in (_compose(None), _compose(VOICE_SYSTEM_PROMPT)):
        assert composed[0].content == "You are Jarvis."


def test_both_surfaces_get_the_same_facts():
    chat = next(m.content for m in _compose(None) if m.content.startswith("RECALLED"))
    voice = next(
        m.content
        for m in _compose(VOICE_SYSTEM_PROMPT)
        if m.content.startswith("RECALLED")
    )
    assert chat == voice == "RECALLED facts=['likes latte']"
```

- [ ] **Step 2: Run the invariant test**

Run: `.venv/bin/pytest tests/turn/test_surface_parity.py -v`
Expected: all three PASS. If any fails, an earlier task is incomplete — fix
that task rather than relaxing this test.

- [ ] **Step 3: Run the whole affected surface**

Run:
```bash
.venv/bin/pytest tests/turn/ tests/system/ tests/server/ tests/sdk/ -v
```
Expected: PASS

- [ ] **Step 4: Run the runtime invariants**

Run: `.venv/bin/pytest tests/integration/test_runtime_invariants.py -v -m integration`
Expected: PASS, or the same skips as before this phase. This suite spawns a real
Playwright MCP subprocess; if it was already failing on this machine before
Phase 0, note that and do not treat it as a regression.

- [ ] **Step 5: Commit**

```bash
git add tests/turn/test_surface_parity.py
git commit -m "test: pin chat and voice to one context assembly

The three implementations this phase replaced drifted apart quietly over
months. This fails loudly the moment a surface starts assembling its own
context again.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Phase 0 completion checklist

Phase 0 is done when all of the following hold. Do not declare it complete on a
green test run alone.

- [ ] `tests/turn/test_surface_parity.py` passes.
- [ ] `grep -rn "_memory_recall" src/` returns nothing.
- [ ] `grep -rn "inject_context" src/openjarvis/system/orchestrator.py
      src/openjarvis/server/routes.py src/openjarvis/server/voice/` returns
      nothing — every caller goes through `compose_context`.
- [ ] `grep -rn "NativeAgentRuntime(" src/` matches only
      `src/openjarvis/system/core.py` — there is exactly one construction site.
- [ ] `git diff --stat a97c64c..HEAD -- src/openjarvis/agents/` shows no change
      to `orchestrator.py`, `runtime.py` or `_stubs.py` beyond what the baseline
      already carried.
- [ ] `git diff --stat a97c64c..HEAD -- src/openjarvis/system/builder.py
      src/openjarvis/cli/serve.py` shows no change from this phase.
- [ ] A real Voice session answers "bạn là ai?" with the same identity Chat
      gives. This is a manual check; automated tests cover composition, not the
      model's answer.
- [ ] The working tree is clean.
