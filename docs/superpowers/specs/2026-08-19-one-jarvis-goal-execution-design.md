# One Jarvis, Many Surfaces — Goal Execution Design

Date: 2026-08-19
Status: Approved for planning
Branch: `native/runtime`
Baseline: `a97c64c67bcb9ade1600e0cb39a793d6e15db7af` (`origin/main`)

## Purpose

Make Chat and Voice share one logical intelligence — same tools, same memory,
same policy, same conversation thread — and give that intelligence the ability
to execute multi-step goals (catalog exploration, ordering, payment) with the
LLM reasoning inside every step.

Jarvis stays a general assistant. Casual conversation, explanations and
recommendations run through the same loop as ordering, with no mode switch and
no routing branch.

## Confirmed constraints

These were decided during design and are not open in planning.

1. **One conversation session at a time per server process.** The existing
   process-wide serialization is acceptable.
2. **The first merchant is third-party**, reachable only through its website.
   There is no partner API.
3. **Chat and Voice share one thread.** Kiosk is not a conversation surface —
   it is a policy layer (microphone gate, vision, FSM) and keeps a fresh thread
   per customer.
4. **Display is an iframe of plain HTML inside the existing Kiosk page.**

## Current architecture, as found

### Three entry paths reimplement the same three steps

| Path | Entry | Memory recall | System prompt | Session persistence |
|---|---|---|---|---|
| CLI / channels | `JarvisSystem.ask()` → `QueryOrchestrator` | `inject_context` + `load_configured_facts` | `config.agent.default_system_prompt` | `SessionStore`, key `<channel>:<conversation_id>` |
| Chat HTTP | `/v1/chat/completions` | `inject_context` + `memory_service.list_facts()` | `_ensure_identity_prompt` | none |
| Voice | `native_agent_runtime.bind()` | `_memory_recall` (no facts) | `VOICE_SYSTEM_PROMPT` | `SessionStore`, key `voice:<chat_thread_id>` |

Sources: `system/orchestrator.py:44-64`, `server/routes.py:120-185`,
`server/voice/runtime.py:52-71`.

Three implementations have already diverged, producing three live defects:

- Voice has no identity grounding, so "who are you?" is answered differently
  than in Chat.
- Voice never receives configured facts, so personal preferences known to Chat
  are invisible to Voice.
- Chat persists no session at all, so a Voice conversation is written to a
  thread that Chat never reads.

### What is already correct

- `OrchestratorAgent.run_stream` (`agents/orchestrator.py:107-300`) is a real
  reason → tool → observation → reason loop, with streaming deltas, cooperative
  cancellation, `LoopGuard`, and a `pending_approval` halt.
- `NativeAgentRuntime` (`agents/runtime.py`) provides a run lease, two-phase
  persistence staging, cancellation and a capability snapshot. Its lock is held
  per *run*, not per session, so a waiting caller blocks for one agent turn.
- Sharing a single `BaseAgent` instance across callers is safe by design; the
  per-run model is a local, never an attribute swap
  (`agents/orchestrator.py:118-120`).
- Kiosk carries no intelligence: a pure FSM plus three fixed Vietnamese TTS
  cues (`kiosk/effects.py:13-17`).
- The Kiosk browser already holds an authenticated loopback WebSocket at
  `/v1/agents/events` (`server/ws_bridge.py:67`, `server/auth_middleware.py:30`).
- The Playwright MCP preset exists, with an invariant test pinning exactly one
  browser subprocess per system
  (`tests/integration/test_runtime_invariants.py:115`).

### WorkflowEngine is not the goal-execution backbone

Read from `workflow/engine.py`:

- Workflow state is `Dict[str, str]` (`engine.py:64`) — strings only.
- `WorkflowEdge.condition` is declared (`types.py:38`) but never read by the
  engine. Routing is purely a topological sort over `execution_stages()`, so
  every node runs.
- `CONDITION` nodes `eval()` an expression and write `"true"`/`"false"` into
  outputs; nothing branches on the result.
- Node input is the concatenated text of all predecessors
  (`_get_node_input:193`). There is no input binding.
- There is no checkpoint or resume.
- Execution is synchronous `ThreadPoolExecutor`, incompatible with the async
  streaming and cancellation the Voice path depends on.

The decisive objection is not the quality of the implementation. A DAG fixes
node order before the run begins, while the requirement is that the Agent
chooses the next action from an observation. Upgrading WorkflowEngine into a
graph whose edges an Agent selects would rebuild a graph-orchestration
framework inside the fork and would compete with `OrchestratorAgent` for
decision authority.

WorkflowEngine is therefore left untouched and stays off the goal path. It
remains suitable for batch DAG pipelines.

## Design

### Principle

Nothing in this design decides what to do next except
`OrchestratorAgent.run_stream`. Tools return data, Skills supply guidance,
stores hold state. There is no orchestrator above the Agent.

### 1. Shared context composition

A single function replaces three recall implementations.

```python
# openjarvis/turn.py

def compose_context(
    query: str,
    messages: list[Message],
    *,
    backend: MemoryBackend | None,
    config: JarvisConfig,
    system_prompt: str | None = None,
) -> list[Message]:
    """Assemble the message list every surface sends to the Agent."""
```

It performs, in this order: identity prompt resolution (today's
`_ensure_identity_prompt`), fact loading (`load_configured_facts`), and memory
injection (`inject_context`), then prepends `system_prompt` when given.

The ordering is load-bearing. `inject_context` prepends a message of its own,
so a surface prompt applied before it would end up behind the recalled
document — the instruction describing how to answer would arrive after the
material it governs. `server/voice/llm.py:133-139` records this constraint for
the voice path; `compose_context` makes it apply to every caller.

Callers:

- `QueryOrchestrator.ask` — replaces its inline block.
- `/v1/chat/completions`, on the server-agent branch — replaces its inline block.
- `server/voice/llm.py:agent_input` — replaces `_memory_recall`, passing
  `VOICE_SYSTEM_PROMPT` as `system_prompt`.

The `request_body.tools` branch of `/v1/chat/completions` is **not** routed
through this function. That branch is raw OpenAI-compatible passthrough; the
existing guard against routing it through a server-side agent stays as written.

There is no `TurnService` class and no turn-orchestration module. The ordering
of load → compose → bind → stream → persist is left to each surface. This is a
deliberate trade: the duplication that actually caused defects was context
composition, and that is what is being removed.

### 2. Runtime ownership

`NativeAgentRuntime` moves from `app.state` into the system it belongs to.

`SystemBuilder` cannot construct it. At `build()` time there is no agent to
wrap: the builder resolves `agent_name` only (`system/builder.py:258,336`), and
the canonical long-lived Agent is constructed afterwards by the serving
composition, which assigns it back (`cli/serve.py:320-339`). That assignment is
the single site where `system.agent` is ever set; `sdk.py` does not use the
field, constructing per call instead.

Ownership therefore belongs to `JarvisSystem`, but construction follows the
Agent rather than the builder:

```
SystemBuilder → JarvisSystem → serving composition
                                    ↓
                        construct canonical long-lived Agent
                                    ↓
                              system.agent
                                    ↓
                          system.agent_runtime
```

This is expressed as a derived property rather than a second assignment, so the
runtime cannot disagree with the agent it wraps:

- `system/core.py`: add an `agent_runtime` property that returns `None` when
  `self.agent` is `None`, and otherwise builds a `NativeAgentRuntime` once and
  caches it in `self.__dict__`. `JarvisSystem` already uses exactly this idiom
  for `_get_orchestrator` (`core.py:127-134`) and already exposes derived
  bundles as properties (`core.py:93-125`).
- The `DataSourceConfigurationSnapshot` currently assembled in
  `server/app.py:311-321` moves into a private method on `JarvisSystem`, where
  its only inputs — `memory_backend`, `config.memory` and
  `config.agent.context_from_memory` — already live.
- `server/app.py:306-325`: stop constructing one; read `system.agent_runtime`.
- `cli/serve.py`: unchanged.

Only the construction site moves. `agents/runtime.py` itself is not edited, and
the class keeps exactly its four responsibilities: a run lease, a two-phase
persistence stage, a cancellation scope and a capability snapshot. It must not
acquire routing, composition or decision responsibilities.

One consequence needs care. `_staged_persistence` is a dictionary on the
runtime, and the runtime is now reachable from every surface rather than from
Voice alone. Today only Voice passes a `persistence_key`, and Chat passes
`None`, so nothing collides. Any future caller that stages a turn must prefix
its key with a surface identifier, or one surface will settle another's staged
writes.

### 3. Goal execution

There is no generic goal-state layer. The cart is the state; the order is the
state. A freeform state object whose schema the LLM invents cannot be verified
against anything, so it would not serve the purpose that motivated it.

#### Doctrine: mutation and observation never share a tool

- A tool that changes something returns a minimal acknowledgement. It does not
  report what is now true.
- Learning what is now true requires a separate observing tool.

A single `commerce_checkout()` violates this by construction, because it would
mutate and report in one call. The doctrine is what structurally prevents it.

The resulting order flow forces a reasoning step between every action:

```
cart_add(item, options)   mutate    → "added, line_id=3"
cart_view()               observe   → the real cart
  Agent judges: right item? right price? anything missing?
order_place()             mutate    → order_id
order_verify(order_id)    observe   → what the merchant says the order is
  Agent compares its belief against the merchant's answer
payment_start(id, method) mutate    → QR reference and amount
payment_verify(id)        observe   → real payment state
  Agent decides: wait, report success, or handle failure
```

#### Tools

| Tool | Kind | Notes |
|---|---|---|
| `menu_search(query)` | observe | where recommendation happens |
| `menu_item(id)` | observe | detail, options, price |
| `cart_add`, `cart_remove` | mutate | acknowledgement only |
| `cart_view` | observe | |
| `order_place` | mutate | does not pay |
| `order_verify` | observe | re-reads from the merchant |
| `payment_start` | mutate | requires approval |
| `payment_verify` | observe | |

Tool internals may be fully deterministic — HTTP calls, DOM parsing, browser
actions. What is forbidden is combining a mutation and its observation.

Each tool declares `metadata["mutates"]` or `metadata["observes"]`, never both.

#### Approval

`payment_start` declares `required_capabilities=["payment"]` and returns a
pending-approval `ToolResult`. The machinery already exists and is reused
unchanged: `_requires_pending_approval` halts the loop
(`agents/orchestrator.py:254`), `NativeAgentRuntime` holds derived writes until
confirmation (`agents/runtime.py:166`), and `VoiceSession` carries the voice
approval window (`server/voice/session.py:178`).

#### Turn budget

`config.agent.max_turns` is raised to 30. A browser-driven order can exceed the
current default of 10 and would strand mid-order. `LoopGuard` remains the real
brake against runaway loops.

#### Generality

Ordering tools are loaded through `SkillManager.get_skill_tools()` and sit
alongside `web_search`, `calculator`, `file_read` and MCP tools. The system
prompt mentions no domain. Casual conversation runs the same loop with zero
tool calls. No code branch routes into a commerce flow, which is what keeps
Jarvis from becoming a commerce bot.

### 4. FAST and INTERACTIVE execution

#### Discovery is a by-product of the first interactive run

There is no separate crawl phase. The first order at an unknown merchant runs
interactively through Playwright MCP. The network traffic of that session is
read afterwards through the MCP server's own `browser_network_requests` and
recorded as a capability candidate. Subsequent orders use it.

#### Two tool sets, selected by failure message

| | FAST | INTERACTIVE |
|---|---|---|
| Tools | semantic tools above | Playwright MCP: `browser_snapshot`, `browser_click`, `browser_type` |
| Requires | a stored `MerchantCapability` | nothing |
| Observation | tool result, then `*_verify` | Agent observes each step |

When no capability exists, the semantic tool returns:

```python
ToolResult(success=False, content=(
    "no_capability_for_merchant '<id>'. "
    "Use browser tools to complete this, then call merchant_learn."
))
```

The Agent reads that and switches. There is no router, no intent classifier and
no execution-mode branch in the call path.

`config.merchants.execution_mode` (`auto` | `fast` | `interactive`) controls
only which tools are *registered*, never how a call is dispatched.

#### Capability record

```python
@dataclass(frozen=True, slots=True)
class MerchantCapability:
    merchant_id: str
    action: str
    method: str
    url_template: str
    body_template: dict
    credential_key: str          # a key name, never a secret value
    status: Literal["candidate", "validated", "broken"]
    recorded_at: float
    verified_count: int
```

Stored as `~/.openjarvis/merchants/<merchant_id>/capability.json`. No schema
migration, human-inspectable. Credentials are resolved through
`core/credentials.py` by `credential_key`; no secret is ever written to this
file.

#### Lifecycle

```
unknown ──interactive run + merchant_learn──▶ candidate
                                                  │
                                 next order tries FAST
                                                  ▼
                                          order_verify()
                                        ╱                ╲
                                   matches            mismatch or HTTP error
                                      │                        │
                                      ▼                        ▼
                                 validated                  broken
                                                               │
                                        "capability_broken, use browser tools"
                                                               │
                                               interactive again, re-recorded
```

A broken capability cannot silently produce a wrong order, because the
mutation/observation doctrine makes `order_verify` mandatory after every
mutation. The verification the Agent must perform anyway is what validates the
fast path. This coupling is intentional: the doctrine in section 3 is what
makes section 4 safe.

#### merchant_learn

`merchant_learn` is a tool the Agent calls explicitly after a successful
`order_verify`. The `ordering` skill instructs it to. If the Agent forgets, the
next order runs interactively — slower, not wrong. That failure mode is
accepted in order to avoid building an event-correlation subsystem to automate
one tool call.

### 5. Display surface

#### Channel

The Kiosk browser already holds an authenticated loopback WebSocket to
`/v1/agents/events`. Display updates travel over it.

```
Agent calls display_menu(items)
  → bus.publish(EventType.DISPLAY_UPDATE, payload)
  → ws_bridge forwards (one entry added to _AGENT_EVENTS)
  → the open page renders
```

No new browser is launched and no new transport is added.

#### Page

`KioskPage.tsx` keeps voice and kiosk state, and embeds
`<iframe src="/display">`. `server/static/display.html` is plain HTML, CSS and
JavaScript: it opens its own WebSocket and renders menu, cart and QR.

Served over `http://` from the existing static mount. `file://` is not used
because its `null` origin breaks the loopback WebSocket authorization.

This keeps merchant-facing presentation editable as a static file, with no
React rebuild, while leaving the working voice integration untouched.

#### Tools

```python
display_menu(items)       # [{id, name, price, available, image_url, note}]
display_cart(lines, total)
display_qr(payment_id)
display_clear()
```

Two rules are mandatory:

1. **The LLM never emits HTML.** It supplies structured data; the page renders
   it with fixed templates. Anything else is an XSS surface and an unpredictable
   layout.
2. **`display_qr` accepts an identifier, never a payload.** The page resolves
   the QR content from the payment record. The LLM therefore cannot fabricate a
   QR code directing payment elsewhere, even under prompt injection from
   crawled merchant content.

Merchant-supplied text is untrusted and is escaped on render.

Display tools are exempt from the mutation/observation doctrine. They do not
touch merchant state; they draw.

#### Who decides what is shown

`menu_search` does not push to the display. The Agent calls `display_menu`
separately, because choosing which three of forty items to show is exactly the
kind of meaningful decision this design keeps with the Agent. As with
`merchant_learn`, a forgotten call degrades presentation, not correctness.

#### Catalog cache

Crawled catalog data is cached at
`~/.openjarvis/merchants/<merchant_id>/catalog.json`, beside the capability
record, so the display renders immediately instead of waiting for a crawl.

`image_url` points at the merchant. Images are not downloaded; losing network
loses thumbnails, not names and prices.

## Kiosk

Kiosk is unchanged: a pure FSM, three fixed TTS cues, state published on the
bus. It gains no tools, no intelligence and no view of the Agent.

One property must hold: a cart must never leak between customers. Kiosk creates
a fresh thread per voice session, and cart and order state are scoped by
`thread_id`, so cleanup isolates customers with no new code.

Thread policy differs by surface, and this is correct rather than
contradictory:

| Surface | Thread | Reason |
|---|---|---|
| Kiosk | new each session | public terminal; state must not leak between customers |
| Chat and Voice on a personal device | stable and shared | conversational continuity |

## Boundaries

These must hold throughout implementation.

- No second composition root. No `CommerceSystem`, `OrderingSystem` or
  `CheckoutEngine`.
- Nothing decides the next action except `OrchestratorAgent.run_stream`.
- `NativeAgentRuntime` acquires no new responsibility.
- `WorkflowEngine` is not modified and stays off the goal path.
- Kiosk acquires no intelligence.
- `agents/orchestrator.py`, `agents/runtime.py` and `agents/_stubs.py` are not
  modified. They carry the heaviest fork drift already (+561, +181, +284).

## Accepted limits

Each is a deliberate simplification with a known ceiling and an upgrade path.

| Limit | Consequence | Upgrade path |
|---|---|---|
| `JarvisSystem.ask()` stays synchronous and outside the shared turn ordering | CLI does not share a thread with Chat and Voice | add an async `ask_stream()` alongside it |
| `NativeAgentRuntime._lock` stays process-wide | one agent run at a time | must change together with `VoiceSessionService._lease_session_id` and the `_RENDERER` global |
| `max_turns` is global, not per goal | no per-goal budget | a `ContextVar`, mirroring the existing `_RUN_MODEL` |
| `agent_runtime` caches on first access | reassigning `system.agent` afterwards leaves a stale runtime | there is one assignment site, and it runs before first use; add invalidation only if a second appears |
| `merchant_learn` is called by the Agent | a forgotten call costs speed, not correctness | hook the EventBus to browser tool results |
| One browser context per process | matches one session at a time | changes with the concurrency limits above |
| Capabilities record request shape only, not conditional multi-step flows | a changed site flow fails verification and is relearned | this is the intended behaviour |
| No capability TTL | a stale capability survives until verification breaks it | add a TTL if sites change more often than orders occur |
| Thumbnails are hotlinked | no images offline | proxy through `/api/merchants/<id>/image` |
| Display is output only | no touch selection | a tap must enter the Pipecat context as a user turn; separate work |

Replaying a third-party site's internal API is a relationship between the
operator and the merchant. This design does not adjudicate it, and the operator
should confirm it with the merchant before enabling the fast path in
production.

## Verification

Each item is a test that fails today or fails if the design is violated.

**Shared composition**

```python
def test_chat_and_voice_compose_identical_context(system):
    """Two profiles may differ only by system prompt."""
```
Fails today: Voice lacks identity prompt and facts.

**Goal loop**

```python
def test_no_tool_both_mutates_and_reports():
    """Every ordering tool declares mutates XOR observes."""

def test_order_flow_requires_agent_reasoning_between_steps(fake_merchant):
    """Ordering and paying takes at least four inference turns.
    One turn would mean a giant tool exists."""
```

**Fast path safety**

```python
def test_broken_capability_never_yields_wrong_order(fake_merchant):
    """The merchant changes a price behind the cached capability.
    Verification catches it, the capability is demoted, and the Agent
    reports the real price."""
```
This is the most important test in the design: it proves the speed
optimization is not paid for with correctness.

```python
def test_second_order_uses_no_browser_tool(fake_merchant):
    """A validated capability means Playwright is not touched."""
```

**Display safety**

```python
def test_display_qr_rejects_llm_supplied_payload():
    """display_qr accepts payment_id only."""

def test_display_menu_escapes_merchant_content(page):
    """A dish named <script>… renders as text."""
```

**Boundaries**

```python
def test_no_second_composition_root():
    """Only JarvisSystem owns engine, tools and executor together."""

def test_hot_files_unchanged_by_this_work():
    """Drift boundary checked by git, not by promise."""
```

## Implementation order

Every phase ends with something runnable and verifiable. Payment is last
because it is the irreversible step.

**Phase 0 — foundation, no new features**
`compose_context()` and `agent_runtime` on `JarvisSystem`. Closes the three
live defects. Gate: chat and voice compose identical context apart from the
system prompt.

**Phase 1 — goal loop against a fake merchant**
The `ordering` skill, semantic tools over a fake merchant fixture, the
mutation/observation rule and its test, the raised turn budget, and
`display_menu` / `display_cart` with the iframe. Display lands early because
seeing the loop is the cheapest way to debug it. Gate: the reasoning-turn count
test.

**Phase 2 — interactive execution**
Playwright MCP available to the Agent. A real order placed through the browser,
with `order_verify` reading the live site. Gate: a real order, slow but correct.

**Phase 3 — fast execution**
`MerchantCapability` store, `merchant_learn`, capability lookup in the semantic
tools, and the demotion path. Gate:
`test_broken_capability_never_yields_wrong_order`.

**Phase 4 — payment**
`payment_start`, `payment_verify`, the approval gate, and `display_qr`.

Phase 0 stands alone: it changes no behaviour a user asked for, closes three
existing defects, and is the only phase that touches upstream composition. It
should be planned and landed before phases 1 through 4 are planned in detail,
so the shared foundation is proven before anything is built on it.

## Scope summary

| | New | Upstream edits |
|---|---|---|
| Phase 0 | `openjarvis/turn.py` (~40 lines) | `system/core.py` ~+18 (property and snapshot helper); `system/orchestrator.py` −20/+1; `server/app.py` −15; `server/voice/{runtime,llm}.py` ~−20. `system/builder.py` and `cli/serve.py` unchanged |
| Phases 1–4 | `openjarvis/merchants/`, `ordering` skill, tools, `server/static/display.html`, one iframe in `KioskPage.tsx` | config keys `agent.max_turns`, `merchants.execution_mode`; one `EventType`; one entry in `_AGENT_EVENTS` |

New drift is concentrated in new modules. Upstream edits are confined to
`system/*` (three files, roughly thirty net lines) plus small additive changes
in the server and kiosk layers.
