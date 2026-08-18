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
branch_list()                  observe   → which branches exist
menu_search(query, branch)     observe   → what is on that branch's menu today
cart_add(variant, qty, note)   mutate    → "added, line_id=3"
cart_view()                    observe   → the real cart
  Agent judges: right variant? right price? anything missing?
order_place(type, branch)      mutate    → order_id
order_verify(order_id)         observe   → what the merchant recorded
  Agent compares its belief against the merchant's record
payment_start(id, method)      mutate    → QR reference and amount
payment_verify(id)             observe   → real payment state
  Agent decides: wait, report success, or handle failure
```

#### Tools

| Tool | Kind | Notes |
|---|---|---|
| `branch_list()` | observe | menus and orders are branch-scoped; nothing works without one |
| `menu_search(query, branch)` | observe | where recommendation happens |
| `menu_item(product)` | observe | variants, each a separately priced size |
| `cart_add(variant, quantity, note)` | mutate | acknowledgement only |
| `cart_remove(line_id)` | mutate | acknowledgement only |
| `cart_view` | observe | |
| `order_place(type, branch)` | mutate | `type` is `at-table` / `take-out` / `delivery`; does not pay |
| `order_verify` | observe | re-reads from the merchant |
| `payment_start` | mutate | requires approval |
| `payment_verify` | observe | |

**Variants, not modifiers.** A size is a separate priced SKU (a *variant*),
not an option on one product. Anything a merchant does not model — sugar level,
ice, "no straw" — arrives as free text in `note`. Real menus were checked
before this was written: a Vietnamese coffee chain's public API exposes
`product → variants[] → {size, price}` and a per-line `note` string, with no
structured modifier anywhere.

**What `note` costs.** A modelled option can be verified; free text cannot.
`order_verify` will echo `"ít đường"` back because that is the string that was
sent — it is confirmation of *transcription*, not of *preparation*. A person
reads that note and makes the drink. See "Accepted limits".

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

#### Discovery is cheapest without a browser

Three techniques, tried in this order. The first that yields a schema wins.

**1. Read the client bundle.** Most ordering sites are single-page apps whose
compiled JavaScript names its own API. Fetching one bundle yields the API base
URL, every route the client knows, and the call sites that build each request —
complete, in one request, before any browser starts.

This was measured against a live Vietnamese coffee chain: its homepage is a
521-byte SPA shell with no menu in it at all, and its 2.4 MB bundle named the
API base plus roughly ninety routes. The public menu endpoint then returned the
full catalogue unauthenticated.

**2. Probe the schema through validation errors.** A well-built API rejects an
incomplete payload by naming what is wrong. Sending deliberately incomplete
bodies walks the server through its own schema — field by field, in validation
order — and **creates nothing**, because every request fails validation.

Measured on the same API: five requests established that an order is
`{type, branch, orderItems: [{variant, quantity, note?}]}`, that `type` is one
of `at-table` / `take-out` / `delivery`, and that a real branch slug, a real
variant slug and a free-text `note` were all accepted — the payload was proven
complete while remaining one invalid field away from creating an order.

This is strictly better than observing traffic. Traffic shows one payload that
happened to be sent; probing distinguishes **required** from **optional**
fields, which observation cannot.

**3. Drive the browser.** For a server-rendered site, an obfuscated bundle, or
an API that fails closed without explaining, the Agent works through Playwright
MCP and the session's traffic is read afterwards through the MCP server's own
`browser_network_requests`.

Technique 3 is the fallback, not the default. It is the slowest, needs a real
run to observe, and reveals only the endpoints that run happened to touch.

Whether any recording may be dispatched is decided by the lifecycle below, not
by having been recorded, whichever technique produced it.

#### Two tool sets, selected by failure message

| | FAST | INTERACTIVE |
|---|---|---|
| Tools | semantic tools above | Playwright MCP: `browser_snapshot`, `browser_click`, `browser_type` |
| Requires | a **VALIDATED** `MerchantCapability`, and a fast-eligible action | nothing |
| Observation | tool result, then `*_verify` | Agent observes each step |

When no usable capability exists — none recorded, one still in CANDIDATE, one
marked BROKEN, or an action that is not fast-eligible — the semantic tool
returns:

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
    quarantine_reason: str       # which correspondence check failed; "" when validated
    recorded_at: float
    verified_count: int
```

`status` is set once at recording time by the correspondence checks and changes
afterwards only on demotion to `broken`. There is no path from `candidate` to
`validated` that involves executing the capability.

Stored as `~/.openjarvis/merchants/<merchant_id>/capability.json`. No schema
migration, human-inspectable. Credentials are resolved through
`core/credentials.py` by `credential_key`; no secret is ever written to this
file.

#### Lifecycle

A capability is never promoted by being tried. Trying it *is* the mutation:
`order_place` with a wrong recorded request has already put a drink on the
counter by the time `order_verify` reports the mismatch. The
mutation/observation doctrine prevents Jarvis from *claiming* something false;
it does not undo a side effect that already happened.

Promotion is therefore decided by static checks over the recording, before any
fast execution occurs.

```
UNKNOWN
  │  interactive execution, whose *_verify step passed
  ▼
observed network request + verified resulting state
  │  merchant_learn records
  ▼
CANDIDATE ──correspondence checks fail──▶ stays CANDIDATE
  │                                        (quarantine — never executed)
  │  checks pass
  ▼
VALIDATED
  │  FAST allowed only if the action is read-only or reversible
  ▼
fast execution
  │  mandatory independent verify
  ├── match ──────────▶ stays VALIDATED
  └── mismatch/error ─▶ BROKEN ──▶ interactive again, re-recorded
```

**CANDIDATE is a quarantine state, not a trial state.** A capability in
CANDIDATE is never dispatched. The semantic tool treats it exactly as it treats
an absent capability, returning `no_capability_for_merchant` so the Agent falls
back to the browser.

#### Correspondence checks (CANDIDATE → VALIDATED)

All are offline and decidable from the recording alone. They run inside
`merchant_learn`, so a recording is promoted or quarantined at the moment it is
made.

1. **Attribution.** The request is attributed to one action. From a bundle read
   or a schema probe that is the route itself; from a browser run it is the
   request observed between that browser tool's start and the next observation.
2. **Verified outcome.** A recording taken from a browser run requires that run
   to have passed its `*_verify` step; one from an unverified or failed run is
   discarded rather than quarantined. A schema established by probing needs no
   run, because probing never executes the action.
3. **No unbound identifier literals.** Every value in the template that
   identifies something — an item id, a variant slug, a branch slug, a price,
   an amount — must be bound to a named tool parameter. A literal in one of
   those positions means the recording baked in one specific order.

   Free-text fields are exempt. A `note` carrying `"ít đường"` is a legitimate
   parameter whose value happens to be prose, not a baked-in identifier.
   Applying the identifier rule to it would quarantine a correct capability —
   this exemption exists because the first real API checked would have failed
   the rule as originally written.
4. **No inline secrets.** Authentication is carried by `credential_key`. A
   literal token anywhere in the template blocks promotion.
5. **Single request.** The action maps to exactly one request. A step that
   needed conditional follow-up requests is not representable as a template and
   stays interactive.

#### Reversibility rule

Passing validation is necessary but not sufficient. A validated capability can
still go stale when the site changes, and the cost of that differs by orders of
magnitude between actions.

**A mutating tool is fast-eligible only if it is read-only, or if its inverse
exists in the same capability set.**

| Tool | Inverse | Fast-eligible |
|---|---|---|
| `menu_search`, `menu_item`, `cart_view`, `order_verify`, `payment_verify` | read-only | yes |
| `cart_add` | `cart_remove` | yes |
| `order_place` | `order_cancel`, when the merchant offers one | only then |
| `payment_start` | none | **never** |

`payment_start` therefore always runs interactively and always through the
approval gate, whatever its capability status. Payment happens once per order,
so the latency it costs is bounded and worth paying.

The rule is a predicate over the tool set, so it is checkable rather than
advisory.

#### Why the doctrine still matters

With promotion decided statically and irreversible actions excluded, the
remaining risk is a validated capability that has gone stale. The
mutation/observation doctrine covers exactly that case: `order_verify` is
mandatory after every mutation, so a stale capability is detected on its first
misuse, demoted to BROKEN, and the flow returns to the browser. The
verification the Agent must perform anyway is what keeps the fast path honest —
and the reversibility rule is what bounds the damage in the window before it
fires.

This holds for everything the merchant models: identifiers, quantities, prices,
totals, status. It does not extend to free text. A `note` reads back
unchanged whether or not anyone will act on it, so verification proves the
order was recorded, never that it will be prepared as asked.

#### merchant_learn

`merchant_learn` is a tool the Agent calls explicitly after a successful
`order_verify`. The `ordering` skill instructs it to. If the Agent forgets, the
next order runs interactively — slower, not wrong. That failure mode is
accepted in order to avoid building an event-correlation subsystem to automate
one tool call.

`merchant_learn` records the request, runs the correspondence checks, and
writes the result as `validated` or as `candidate` with a `quarantine_reason`.
It never executes anything. A recording whose interactive run did not pass
`*_verify` is refused outright rather than stored.

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
| **A free-text `note` cannot be verified** | `order_verify` echoes `"ít đường"` because that is the string that was sent. Nothing confirms the drink will be made that way — a person reads the note. The Agent must not claim the preparation is confirmed, only that the request was recorded. | none available. It is the merchant's data model, not ours. If a merchant ever models the option, it becomes verifiable like any other field |
| `merchant_learn` is called by the Agent | a forgotten call costs speed, not correctness | hook the EventBus to browser tool results |
| One browser context per process | matches one session at a time | changes with the concurrency limits above |
| Capabilities record request shape only, not conditional multi-step flows | such a step fails correspondence check 5 and stays interactive | this is the intended behaviour |
| `payment_start` is never fast | payment always costs an interactive round | only a merchant-side idempotency key or a reversal endpoint would change this, and neither exists today |
| Correspondence checks are structural, not semantic | a recorded request that is well-formed but wrong for a different reason still validates | the mandatory `*_verify` catches it on first use, and reversibility bounds the cost |
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
def test_candidate_capability_is_never_dispatched(fake_merchant):
    """A quarantined recording must not cause a real mutation.
    The tool reports no capability and the Agent uses the browser."""

def test_irreversible_action_never_runs_fast(fake_merchant):
    """payment_start has no inverse, so it stays interactive even when
    a validated capability for it exists."""

def test_merchant_learn_refuses_unverified_run(fake_merchant):
    """A recording from an interactive run whose *_verify failed is not
    stored at all."""

def test_broken_capability_never_yields_wrong_order(fake_merchant):
    """The merchant changes a price behind a validated capability.
    Verification catches it, the capability is demoted, and the Agent
    reports the real price."""
```
The last is the most important test in the design: it proves the speed
optimization is not paid for with correctness. The first two prove the damage
window before it fires is bounded.

```python
def test_second_order_uses_no_browser_tool(fake_merchant):
    """A validated, reversible action means Playwright is not touched."""
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

**Phase 2 — discovery without a browser**
Bundle read and validation-error schema probing, the `MerchantCapability`
store, `merchant_learn` with its correspondence checks, the reversibility
predicate, capability lookup in the semantic tools, and the demotion path.
Gates: `test_candidate_capability_is_never_dispatched` and
`test_broken_capability_never_yields_wrong_order`.

This is second, not third, because it is the cheapest technique that works and
it needs no live order to produce a capability. Probing establishes a schema
while creating nothing.

**Phase 3 — interactive execution, the fallback**
Playwright MCP available to the Agent, for merchants where phase 2 yields
nothing: server-rendered pages, obfuscated bundles, APIs that fail closed.
A real order placed through the browser, with `order_verify` reading the live
site. Gate: a real order, slow but correct.

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
