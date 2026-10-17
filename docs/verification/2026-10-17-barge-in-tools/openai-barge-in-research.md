# How OpenAI handles barge-in while a tool call is running

Research date: 2026-10-17. Question: when the user starts speaking over the assistant while a
function/tool call is still running, what does OpenAI do with that call and its result, and how does
that compare with OpenJarvis?

**Sources.** Only primary sources were used:

- OpenAI API docs, fetched as Markdown (`<page>.md`) from `developers.openai.com` on 2026-10-17. These
  include the guides and the Realtime client/server event reference. Every page is cited by URL and section heading.
- `openai/openai-agents-python` at commit `9abc9182d06dbd28330af888306031269d57a175` (package `0.24.0`,
  committed 2026-10-17T00:49Z).
- `openai/openai-agents-js` at `9687b544c8c873080e6ff7e7d294c0b353cd9ab6` (`@openai/agents-realtime`
  `0.20.0`, committed 2026-10-16).
- `openai/openai-realtime-agents` at `94c9e9116b581052655cff7b756cc5e02771cda1` (2026-01-07). Its
  `package-lock.json` pins `@openai/agents-realtime` `0.0.5`, so that npm tarball was also read.
- `openai/openai-agents-python` issue #1942 (GitHub API).
- OpenJarvis at HEAD `65d429ec`, read-only. `action_ledger.py` and `server/voice/llm.py` have no
  uncommitted changes.

Downloads were kept in the session scratch directory and were only read, never executed. File:line
citations refer to those exact revisions.

Inferences are marked **(inference)**. Points that a primary source could not confirm are listed in
[Unverified](#unverified).

---

## Summary

1. **Barge-in cancels the model's *response*, not your function.** With VAD on, the Realtime server
   detects speech (`input_audio_buffer.speech_started`). If `turn_detection.interrupt_response` is
   true, it cancels the in-progress response. That response ends with `response.done` carrying
   `status: "cancelled"` and `status_details.reason: "turn_detected"`. A client `response.cancel` gives
   `client_cancelled` instead. Function execution is client-side, and the Realtime API has no event that
   cancels a client's running function (§1.1, §1.2).
2. **A `function_call` that finished streaming before the barge-in stays a pending call. No doc says
   it is cancelled.** OpenAI's developer notes say the Realtime API "allows clients to continue a
   session while a function call is pending". GA models answer requests for the missing result with a
   placeholder ("I'm still waiting on that"). So a dangling call is allowed, not an error. The
   reference docs never say an output is mandatory. They do say you need the `call_id` "to pass a
   function call result back to the model" (§1.2, §1.3).
3. **Both Agents SDKs keep the tool running through an interruption.** Their `audio_interrupted`
   handling only stops playback, sends `conversation.item.truncate`, and (when the server does not
   auto-cancel) sends `response.cancel`. Tool tasks are cancelled only when the session closes
   (Python) or the connection changes (JS) (§2).
4. **When the tool finishes, the SDKs always send the result back.** They send
   `conversation.item.create` with a `function_call_output` for that `call_id` and, by default, a
   `response.create`. The `response.create` is queued until no response is active. Nothing checks
   whether the user has moved on. The only exceptions are JS `backgroundResult(...)`, which sends the
   output without starting a response, and session close or reconnect (§2).
5. **Only completed calls are executed.** Both SDKs start a tool only for a `function_call` item with
   `status: "completed"` on `response.output_item.done`. The reference says `.function_call_arguments.done`
   and `.output_item.done` are "also emitted when a Response is interrupted, incomplete, or
   cancelled". A call cut off mid-arguments by barge-in is therefore not run by the SDKs. The server's
   item status in that case is not documented (§1.2, §2).
6. **chatSupervisor demo: interrupting does not stop the supervisor.** The `fetch('/api/responses')`
   has no abort signal, and the session `interrupt()` never touches it. With the pinned SDK 0.0.5, the
   result comes back as `function_call_output` plus an immediate `response.create`. The filler phrase
   is a prompt rule only. The demo runs a *beta* realtime snapshot, which OpenAI's notes say lacks async
   function calling (§3).
7. **OpenAI's newest voice guidance (GPT-Live docs) states the policy outright:**
   - "Interrupting the spoken conversation leaves backend work running."
   - The application decides whether to finish or cancel.
   - Track "each application action with its own operation ID and each changed request with a task
     revision" so results for outdated requests can be discarded.
   - "Before retrying a failed tool call, check whether the original action already happened" (§5).
8. **Responses API side.** A background response can be cancelled (`POST /v1/responses/{id}/cancel`,
   idempotent), and a synchronous one only by terminating the connection. Async tools (`async: true`,
   GPT-6 Astra and later) let the model continue and accept the output later on the original `call_id`
   (§4).
9. **OpenJarvis matches the core OpenAI rule and is stricter on stale or duplicate reporting:**
   - It already follows the core rule: barge-in stops speech and the model turn, while already-dispatched
     writes finish and are reported later.
   - It is stricter: a `reported` flag, `already_told`, and the D4 hold on later writes.
   - OpenAI documents two things OpenJarvis lacks: an application-level task revision used to discard
     superseded results, and a "check whether it already happened before retrying" rule for ambiguous
     failures such as timeouts (§6, §7).

---

## 1. Realtime API (gpt-realtime family, `server_vad` / `semantic_vad`)

### 1.1 Event sequence on barge-in

| Step | Event (sender) | What the docs say | Source |
|---|---|---|---|
| 1 | `input_audio_buffer.speech_started` (server) | "Sent by the server when in `server_vad` mode to indicate that speech has been detected in the audio buffer. … The client may want to use this event to interrupt audio playback or provide visual feedback to the user." | [Server events → input_audio_buffer.speech_started](https://developers.openai.com/api/reference/resources/realtime/server-events) |
| 2 | automatic cancel (server) | `interrupt_response`: "Whether or not to automatically interrupt (cancel) any ongoing response with output to the default conversation (i.e. `conversation` of `auto`) when a VAD start event occurs. If `true` then the response will be cancelled, otherwise it will continue until complete." | [Client events → session.update → turn_detection (ServerVad)](https://developers.openai.com/api/reference/resources/realtime/client-events) |
| 2' | `response.cancel` (client, when auto-cancel is off or for push-to-talk) | "Send this event to cancel an in-progress response. The server will respond with a `response.done` event with a status of `response.status=cancelled`. If there is no response to cancel, the server will respond with an error. It's safe to call `response.cancel` even if no response is in progress, an error will be returned the session will remain unaffected." Optional `response_id`: "if not provided, will cancel an in-progress response in the default conversation." | [Client events → response.cancel](https://developers.openai.com/api/reference/resources/realtime/client-events) |
| 3 | `response.done` (server) | "Returned when a Response is done streaming. Always emitted, no matter the final state." / "Clients should check the `status` field … (`completed`) or … `cancelled`, `failed`, or `incomplete`." `status_details.reason`: "For a `cancelled` Response, one of `turn_detected` (the server VAD detected a new start of speech) or `client_cancelled` (the client sent a cancel event)." | [Server events → response.done](https://developers.openai.com/api/reference/resources/realtime/server-events) |
| 4a | `conversation.item.truncate` (client, WebSocket) | "The client should send a `conversation.item.truncate` event to remove the unplayed portion of the model's last response from the conversation." Reference: "Truncating audio will delete the server-side text transcript to ensure there is not text in the context that hasn't been heard by the user." `audio_end_ms`: "Inclusive duration up to which audio is truncated … If the audio_end_ms is greater than the actual audio duration, the server will respond with an error." `item_id`: "Only assistant message items can be truncated." | [Guide → Interruption and Truncation](https://developers.openai.com/api/docs/guides/realtime-conversations); [Client events → conversation.item.truncate](https://developers.openai.com/api/reference/resources/realtime/client-events) |
| 4b | automatic truncation, `output_audio_buffer.cleared` (server, WebRTC/SIP) | "In WebRTC and SIP connections the server manages a buffer of output audio … The server will automatically truncate unplayed audio when there's a user interruption." `output_audio_buffer.cleared`: "This happens either in VAD mode when the user has interrupted (`input_audio_buffer.speech_started`), or when the client has emitted the `output_audio_buffer.clear` event". | [Guide → Interruption and Truncation](https://developers.openai.com/api/docs/guides/realtime-conversations); [Server events → output_audio_buffer.cleared](https://developers.openai.com/api/reference/resources/realtime/server-events) |
| 5 | new response (server, `create_response`) | `create_response`: "Whether or not to automatically generate a response when a VAD stop event occurs. If `interrupt_response` is set to `false` this may fail to create a response if the model is already responding." "If both `create_response` and `interrupt_response` are set to `false`, the model will never respond automatically but VAD events will still be emitted." | [Client events → turn_detection](https://developers.openai.com/api/reference/resources/realtime/client-events) |

The guide summarises it in one sentence: "Realtime API handles interruptions when VAD is enabled, in that it
detects user speech, cancels the ongoing response, and starts a new one"
([Interruption and Truncation](https://developers.openai.com/api/docs/guides/realtime-conversations)).

There is one documentation inconsistency. The same guide says "a `response.cancelled` event will be
emitted", but the server-event reference has no `response.cancelled` event. Cancellation is expressed as
`response.done` with `status: "cancelled"` (see Unverified).

**Keeping VAD but taking control of responses:** "you can set `turn_detection.interrupt_response` and
`turn_detection.create_response` to `false` … This will retain all the behavior of VAD but not
automatically create new Responses. Clients can trigger these manually with a `response.create` event"
([Keep VAD, but disable automatic responses](https://developers.openai.com/api/docs/guides/realtime-conversations)).

**Push-to-talk:**

- WebSocket: "If there is an in-progress response from the model, cancel it by sending a `response.cancel`
  event" and "send a `conversation.item.truncate` event to remove any unplayed audio".
- WebRTC/SIP: "send an `output_audio_buffer.clear` event to clear out the unplayed audio, this truncates the
  conversation as well" ([Push-to-talk](https://developers.openai.com/api/docs/guides/realtime-conversations)).
- Reference for `output_audio_buffer.clear`: "This event should be preceded by a `response.cancel` client
  event to stop the generation of the current response."

### 1.2 A `function_call` already emitted while the client is executing it

**The documented flow.** Function execution belongs to the client
([Guide → Function calling](https://developers.openai.com/api/docs/guides/realtime-conversations)):

> "When the client detects conversation items that contain function call arguments, it will execute
> custom code using those arguments … When the custom code has been executed, the client will create new
> conversation items that contain the output of the function call, and ask the model to respond."

> "`response.output[0].call_id` | A system-generated ID for this function call - **you will need this ID
> to pass a function call result back to the model**."

> "Once we have added the conversation item containing our function call results, we again emit the
> `response.create` event from the client. This will trigger a model response using the data from the
> function call."

**What barge-in cancels.** The docs only describe cancelling a *Response* (§1.1). None of the Realtime
docs, the event reference or the "Realtime with tools" guide says that an interruption cancels a
client-side function, removes the `function_call` item, or requires (or forbids) its output.

[Realtime with tools → Configure a function tool](https://developers.openai.com/api/docs/guides/realtime-mcp)
says: "your code executes the action, and your code sends the result back with a `function_call_output`
item."

**Is a dangling `function_call` allowed?** Yes. OpenAI's developer notes on the GA Realtime API
([Developer notes on the Realtime API → Asynchronous function calling](https://developers.openai.com/blog/realtime-api);
this is OpenAI's own developer site, but blog-style rather than reference) say:

> "Whereas the Responses API forces a function response immediately after the function call, the Realtime
> API allows clients to continue a session while a function call is pending. This continuation is good for
> UX, allowing realtime conversations to continue naturally, but the model sometimes hallucinates the
> content of a nonexistent function response."

> "To mitigate this issue, the GA Responses API adds placeholder responses with content we've evaluated and
> tuned in experiments to ensure the model performs gracefully, even while awaiting a function response. If
> you ask the model for the results of a function call, it'll say something like, 'I'm still waiting on
> that.' This feature is automatically enabled for new models—no changes necessary on your end."

The same page's feature table lists "Async function calling" as ✅ for the GA model and ❌ for the beta
model. It adds: "Because the beta model lacks async function calling, pending MCP tool calls without an
output may not be treated well by the model."

**Must the client still send `function_call_output` after barge-in?** No doc makes it mandatory, and no
doc names an error for a missing output in a Realtime session. **(inference)** The output is the only way
the model learns the result. Both OpenAI SDKs always send it (§2), so sending it is the de-facto contract.
The Responses API and GPT-Live Responses delegation *do* enforce outputs (§4, §5).

**Partial calls cut by barge-in.** The reference says
[`response.function_call_arguments.done`](https://developers.openai.com/api/reference/resources/realtime/server-events)
is "Returned when the model-generated function call arguments are done streaming. Also emitted when a
Response is interrupted, incomplete, or cancelled." `response.output_item.done` carries the same sentence.

The `function_call` item has `status: "completed" | "incomplete" | "in_progress"`, described as "The status
of the item. Has no effect on the conversation." A client should therefore not treat
`function_call_arguments.done` alone as a callable request after a cancel. The SDKs gate on
`item.status == "completed"` (§2).

`response.create` reference: "Only one Response can write to the default Conversation at a time, but
otherwise multiple Responses can be created in parallel"
([Client events → response.create](https://developers.openai.com/api/reference/resources/realtime/client-events)).

### 1.3 "Async function calling" in current docs

- Realtime: the only current first-party text is the developer-notes section quoted in §1.2. There the
  model keeps talking while a call is pending, with a placeholder for the result. No tool flag is needed;
  it is "automatically enabled for new models". The `gpt-realtime` model page
  ([models/gpt-realtime](https://developers.openai.com/api/docs/models/gpt-realtime)) confirms the GA
  snapshot `gpt-realtime-2025-08-28`. The launch announcement on openai.com returned HTTP 403 and was not
  read (see Unverified).
- Responses API (separate feature): an explicit `async: true` tool flag; see §4.

### 1.4 Preambles (filler) and tool-failure wording in OpenAI's realtime prompting guide

[Prompting Realtime models → Use preambles intentionally](https://developers.openai.com/api/docs/guides/voice-prompting):

- "Preambles are short spoken updates that keep a voice agent feeling responsive while it reasons, looks
  something up, or calls a tool."
- "`gpt-realtime-2` generates preambles by default."
- Use one "you are about to call a tool that may take noticeable time". Do not use one when "the user is
  only confirming, correcting, or declining something" or "the tool call is lightweight and the user would
  not benefit from an update."
- Message channels: "tool calls happen in the commentary channel". `response.done` carries a `phase`
  (`commentary` / `final_answer`).

[Same guide → Recover from tool failures](https://developers.openai.com/api/docs/guides/voice-prompting):
"If the failure may be temporary, offer to retry once." and "Do not repeatedly call the same tool with the
same arguments after failure."

None of these preamble rules mention interruption of a running tool.

---

## 2. OpenAI Agents SDK, realtime

### 2.1 Python, `openai-agents-python` `src/agents/realtime/` (commit `9abc918`, v0.24.0)

**Defaults.**

- `DEFAULT_MODEL_SETTINGS` sets `"turn_detection": {"type": "semantic_vad", "interrupt_response": True}`
  (`openai_realtime.py:170-179`).
- `RealtimeRunConfig.async_tool_calls`: "Whether function tool calls should run asynchronously. Defaults to
  True." (`config.py:297-298`). It is read in `RealtimeSession.__init__`:
  `self._async_tool_calls = bool(self._run_config.get("async_tool_calls", True))` (`session.py:261`).
- History: issue #1942 ("Enable asynchronous function calling for Realtime session", opened 2025-10-20,
  closed 2025-10-23) reported "when a `function_call` event is received, execution pauses until the
  function completes".

**When a tool starts.**

- `OpenAIRealtimeWebSocketModel._handle_output_item` turns an item into a tool call only when
  `item.type == "function_call" and item.status == "completed"`. It then emits `RealtimeModelToolCallEvent`
  (`openai_realtime.py:1231-1254`). It is called for `response.output_item.added`/`.done` (`:1593-1597`).
- `RealtimeSession.on_event` handles `"function_call"`. With async tool calls it calls
  `_enqueue_tool_call_task` → `asyncio.create_task(self._handle_tool_call(event, ...))`, tracked in
  `self._tool_call_tasks` (`session.py:423-429`, `1922-1947`, docstring "Run tool calls in the background
  to avoid blocking realtime transport").

**What barge-in does.**

- `input_audio_buffer.speech_started` handling (`openai_realtime.py:1448-1517`) does four things:
  - emits `RealtimeModelAudioInterruptedEvent`;
  - sends `conversation.item.truncate` with `audio_end_ms` from the event or the playback tracker (built by
    `convert_interrupt`, `:2180-2191`);
  - resets the audio trackers;
  - sends `response.cancel` only when the server is *not* configured with `interrupt_response: true`
    (comment: "If server isn't configured to auto‑interrupt/cancel, cancel the response to prevent further
    audio.").
- `RealtimeSession.on_event` maps `audio_interrupted` to `RealtimeAudioInterrupted` and does nothing else
  (`session.py:460-465`).
- `RealtimeSession.interrupt()` sends `RealtimeModelSendInterrupt()` (`session.py:384-386`). That is handled
  by `_send_interrupt` (`openai_realtime.py:1165-1200`): truncate, plus `response.cancel` if auto-cancel is
  off or forced.
- **None of these paths touch `_tool_call_tasks`.**
- Tool tasks are cancelled only by `_cancel_background_tasks()`, called from `_cleanup()` when the session
  closes (`session.py:2004-2027`, `2043-2067`).

**When the tool finishes (even after a barge-in).**

- `_handle_tool_call` awaits `invoke_function_tool(...)`, runs output guardrails, then calls
  `_send_tool_output_completion(_PendingToolOutput(..., start_response=True, ...))`
  (`session.py:1279-1310`).
- The only early exits after the tool returns are `if self._closing or self._closed: return`
  (`:1284-1285`, `:1293-1294`).
- `_interrupted_response_ids` gates audio and output guardrails only (`:451`, `:1707-1740`), never tool
  outputs.
- `_send_tool_output` sends `conversation.item.create` with
  `{"type": "function_call_output", "call_id": ..., "output": ...}` (`convert_tool_output`,
  `openai_realtime.py:2169-2177`). If `start_response`, it reserves a `response.create` (`:985-1003`).
- `_ResponseCreateSequencer.wait_for_response_create_slot` holds that `response.create` until
  `not self._ongoing_response and self._response_control == "free"` (`openai_realtime.py:368-405`).
  Automatic requests are coalesced (`_auto_response_create_target_version`, `:269-286`;
  `mark_response_create_sent`, `:407-416`).
- **(inference)** A tool that finishes while the user's new turn is being answered gets its output inserted
  at once. A further model response about it follows after that turn's `response.done`.

**Errors, timeouts, duplicates.**

- By default a tool exception becomes the fixed model-visible output "An error occurred while running the
  tool. Please try again." (`tool.py:1997-2002`, `2032-2041`).
- Function tools accept `timeout_seconds` with `timeout_behavior: "error_as_result"` by default
  (`tool.py:515-518`).
- Re-delivered calls with the same `call_id` are ignored as duplicates. A call that "already executed, but
  its output was not committed" raises instead of re-running (`session.py:1169-1183`).

**SDK docs** (`docs/realtime/guide.md:254`): "When the user interrupts the assistant, the session emits
`audio_interrupted` and updates history so the server-side conversation stays aligned with what the user
actually heard." The guide says nothing about tools on interruption.

### 2.2 JavaScript, `openai-agents-js` `packages/agents-realtime` (commit `9687b54`, v0.20.0)

**When a tool starts.**

- `OpenAIRealtimeBase` emits `function_call` (with `responseId`) on `response.output_item.done`/`.added`
  when `item.type === 'function_call' && item.status === 'completed'` (`openaiRealtimeBase.ts:498-522`).
- `RealtimeSession` handles it in an `async` listener (`realtimeSession.ts:1861-1887`), then runs
  `#handleFunctionCall` → `#handleFunctionToolCall` → `#runRealtimeToolInvocation` → `#executeFunctionToolCall`.

**What barge-in does.**

- WebSocket: on `input_audio_buffer.speech_started` the transport calls
  `this.interrupt(!automaticResponseCancellationEnabled)` (`openaiRealtimeWebsocket.ts:484-488`).
  `_interrupt()` sends `response.cancel` only if asked, emits `audio_interrupted`, and sends
  `conversation.item.truncate` with an integer `audio_end_ms` (`:738-765`). `interrupt()` computes the
  elapsed playback time (`:774-785`).
- WebRTC: `interrupt()` sends `response.cancel` (if a response is ongoing) and `output_audio_buffer.clear`
  (`openaiRealtimeWebRtc.ts:800-815`).
- The session's `audio_interrupted` listener only re-emits the event (`realtimeSession.ts:1893-1898`).
  `RealtimeSession.interrupt()` ("Interrupt the session artificially for example if you want to build a
  'stop talking' button") just calls `this.#transport.interrupt()` (`:2160-2168`).
- **No path cancels a running tool.**
- In-flight results are dropped only across connections: `#isCurrentRealtimeInvocation` compares the
  invocation's `connectionGeneration` with the current one (`:925-932`). That generation is bumped in
  `connect()` and `close()` (`:2042-2046`, `:2139-2149`), not on barge-in.

**When the tool finishes.**

- `#executeFunctionToolCall` invokes the tool, runs output guardrails, then calls
  `#sendCommittedFunctionCallOutput(..., startResponse)` (`realtimeSession.ts:1356-1401`):
  - `isBackgroundResult(result)` → `startResponse = false` (comment: "Don't generate a new response, just
    send the result");
  - otherwise `true`.
- `sendFunctionCallOutput` sends `conversation.item.create` / `function_call_output` and, if
  `startResponse`, `this.requestResponse()` (`openaiRealtimeBase.ts:961-999`). The WebSocket transport
  routes that through a `ResponseCreateSequencer` that waits while a response is ongoing
  (`openaiRealtimeWebsocket.ts:645-672`; `responseCreateSequencer.ts`).
- `backgroundResult` is defined in `tool.ts:11-33`.
- Approval: if `needsApproval` resolves true, the session emits `tool_approval_requested` and parks the call
  until `approve`/`reject` (`realtimeSession.ts:1202-1311`).

**SDK docs** (`docs/src/content/docs/guides/voice-agents/build.mdx`):

- l.124: "On the WebSocket transport, the SDK listens for `input_audio_buffer.speech_started`, truncates
  the assistant audio to what the user actually heard, and emits an `audio_interrupted` event."
- l.203: "While the tool is executing the agent will not be able to process new requests from the user. One
  way to improve the experience is by telling your agent to announce when it is about to execute a tool or
  say specific phrases to buy the agent some time to execute the tool." This conflicts with the non-blocking
  listener above and the GA "pending call" behaviour (see Unverified).
- l.205: "If a function tool should finish without immediately triggering another model response, return
  `backgroundResult(output)` … This sends the tool output back to the session while leaving response
  triggering under your control."
- l.209: with the default timeout behaviour `error_as_result` "the timeout message is sent as tool output";
  with `raise_exception` "the session … does not send tool output for that call."
- l.238: "While the voice agent is waiting for approval for the tool call, the agent will not be able to
  process new requests from the user."
- l.274: "Responses truncated by interruption do not retain a final transcript."

---

## 3. `openai-realtime-agents` demo, chatSupervisor (commit `94c9e91`)

**Setup.**

- `RealtimeSession` with `OpenAIRealtimeWebRTC` and model `gpt-4o-realtime-preview-2025-06-03`
  (`src/app/hooks/useRealtimeSession.ts:128-145`).
- Turn detection is `server_vad` with `create_response: true`. `interrupt_response` is not set
  (`src/app/App.tsx:263-277`).
- Dependency `@openai/agents ^0.0.5`; the lock file resolves `@openai/agents-realtime 0.0.5`.

**Filler phrase.** This is a prompt rule only (`agentConfigs/chatSupervisor/index.ts:64-67`): "Before
calling getNextResponseFromSupervisor, you MUST ALWAYS say something to the user … Filler phrases must NOT
indicate whether you can or cannot fulfill an action". Sample fillers are at `:76-82`. The README measures
"~2s between the end of 'give me a moment to check on that.' being spoken aloud and the start of the
'Thanks for waiting. Your last bill...'" (README l.38).

**The long-running call.** `getNextResponseFromSupervisor.execute`
(`agentConfigs/chatSupervisor/supervisorAgent.ts:256-318`):

- builds a request from `details.context.history`;
- calls `fetchResponsesMessage`, a plain `fetch('/api/responses', {method:'POST', ...})` with
  `parallel_tool_calls: false` and **no `AbortSignal`** (`:150-167`);
- loops through `handleToolCalls` until a text answer appears (`:186-254`);
- returns `{ nextResponse: finalText }`.

The server route calls `openai.responses.create({...body, stream: false})` (`src/app/api/responses/route.ts:31-43`).
The demo's `src/` tree contains no `AbortController` or `signal`.

**Interrupt handling.**

- The app's `interrupt()` is `sessionRef.current?.interrupt()` (`useRealtimeSession.ts:165-167`). It is used
  when push-to-talk starts and before sending typed text (`App.tsx:289`, `:302`).
- For voice barge-in over WebRTC, the server cancels and truncates (§1.1).
- `useHandleSessionHistory.ts` only adds breadcrumbs on `agent_tool_start` / `agent_tool_end` (`:70-86`).
- In SDK 0.0.5, `RealtimeSession#handleFunctionToolCall` awaits `tool.invoke(...)` and then calls
  `sendFunctionCallOutput(toolCall, stringResult, true)` unconditionally (`dist/realtimeSession.mjs:173-216`).
  That sends `function_call_output` **and an immediate `response.create`**; there is no sequencer in that
  version (`dist/openaiRealtimeBase.mjs:356-384`).
- The 0.0.5 WebRTC `interrupt()` only sends `response.cancel` + `output_audio_buffer.clear`
  (`dist/openaiRealtimeWebRtc.mjs:239-249`).

**Answer.** Interrupting does **not** stop the supervisor's fetch. The result still comes back, is added as
`function_call_output`, and a `response.create` is sent, so the chat agent is asked to voice it.

**(inference)** If the user's barge-in turn is still generating at that moment, the API's rule that "only
one Response can write to the default Conversation at a time" means the result is either voiced after that
turn or the `response.create` is rejected. The runtime outcome was not observed. The demo's beta snapshot
also lacks async function calling per OpenAI's notes, so the chat agent's handling of the pending call
while the user talks is the weaker, pre-GA behaviour.

---

## 4. Responses API, as it bears on "user interrupts while tools run"

- **Background mode** ([Background mode → Cancelling a background response](https://developers.openai.com/api/docs/guides/background)):
  - "You can also cancel an in-flight response" via `POST /v1/responses/{id}/cancel`;
  - "Cancelling twice is idempotent - subsequent calls simply return the final `Response` object";
  - Limits: "To cancel a synchronous response, terminate the connection".
- **Async tool calling** ([Async tool calling](https://developers.openai.com/api/docs/guides/async-tool-calling)):
  - "Set `async: true` on a function or custom tool definition to let the model continue working after
    issuing that call, before your application returns the output";
  - "Your application still executes the tool. Async tools don't move execution to OpenAI or manage your
    background jobs";
  - "If other conversation turns happen before the job finishes, update `latest_response_id` to continue
    from the latest response while keeping the original tool `call_id`";
  - "If your application supports dismissing or timing out a question, return an explicit no-answer result
    so the model can decide how to proceed";
  - Compatibility: "supported by GPT-6 Astra and later models".
- **Strictness of outputs.** The developer notes say "the Responses API forces a function response
  immediately after the function call" (§1.2). In GPT-Live Responses delegation, "Missing results produce
  `tool_outputs_required`, or `function_call_outputs_required` when only function results are missing.
  Duplicate results and mismatched result types are rejected"
  ([Delegation and tools → Return tool results and approvals](https://developers.openai.com/api/docs/guides/live-delegation)).

---

## 5. Additional primary source: GPT-Live delegation guidance (newest OpenAI voice docs)

GPT-Live is a different product from `gpt-realtime`. These docs do not describe Realtime API behaviour, but
they are OpenAI's most explicit statement of the intended semantics for "user talks while backend/tool work
runs". The relevant quotes:

- [Getting started with GPT-Live](https://developers.openai.com/api/docs/guides/live): "Backend work can
  continue when the caller interrupts the assistant; your application decides whether to finish or cancel
  it."
- [Delegation and tools → Keep updates accurate and useful](https://developers.openai.com/api/docs/guides/live-delegation):
  - "When the user changes a request, update the active task in your application … ignore results from the
    outdated Friday request. Manage any cancellation in the backend, and confirm it succeeded before telling
    the user that the work was canceled. Interrupting the spoken conversation leaves backend work running."
  - "Before retrying a failed tool call, check whether the original action already happened. For example, a
    lost response should not cause a second booking. If the outcome is unclear, say so and offer the next
    useful step."
- [Delegation and tools → Verify the complete interaction](https://developers.openai.com/api/docs/guides/live-delegation):
  - "The backend can finish while the spoken answer is interrupted, so test these outcomes separately."
  - "Track each application action with its own operation ID and each changed request with a task revision,
    alongside the GPT-Live delegation ID. Use those records to recognize completed work after reconnects or
    retries and to discard results for outdated requests."
- [Delegation and tools → Return the result](https://developers.openai.com/api/docs/guides/live-delegation),
  suggested backend prompt: "Report an action as complete after the tool or service confirms success. If the
  outcome is unclear, state that and explain what needs to be checked."
- [Migrate to GPT-Live → Adapt your backend prompt](https://developers.openai.com/api/docs/guides/live-migration):
  - "Preserve the rules for work already running when the caller interrupts or the call is transferred or
    ended. Stopping speech leaves backend work running. If the caller asks to cancel, the backend and
    application need to handle that cancellation and report its outcome to Live."
  - From *Resolve conflicting instructions*: "if one instruction says to continue a booking and another says
    to stop all work when the caller interrupts, decide which behavior the application should provide."
- [Migrate to GPT-Live → Preserve context and apply corrections](https://developers.openai.com/api/docs/guides/live-migration):
  - "Give the task a new version number, such as revision 2, so your application can recognize results from
    the earlier request."
  - "If you decline a pending function call because the user changed the request, return a result that
    explains it was skipped or cancelled, matching what actually happened. Submit a result for every required
    call before continuing the backend."
- [Migrate to GPT-Live → From a text agent or chained pipeline → Route updates and corrections](https://developers.openai.com/api/docs/guides/live-migration)
  (the section closest to OpenJarvis's architecture): "Decide whether to request cancellation of the Thursday
  lookup, change it, or let it finish and discard its result. Track the status of the lookup before
  reporting it as cancelled. Handle that backend decision even if the user's interruption has already
  stopped the assistant's speech."
- [Prompting GPT-Live → Interruptions](https://developers.openai.com/api/docs/guides/live-prompting):
  "Handle changes to a task separately from interruptions to speech. 'Stop talking' asks the assistant to
  yield; 'Cancel my booking' asks the backend to take an action."
- [Server-side controls → Run checks alongside the conversation](https://developers.openai.com/api/docs/guides/voice-server-controls):
  - "Cancel application-owned jobs where your backend supports cancellation, and discard late results from
    blocked or superseded requests."
  - From *Assign one owner for each action*: "If both connections receive a function-call event, execute the
    function once."
  - From *Test the intervention*: "Test … a trigger while a tool is running, and late results from canceled
    work."
- [Migrate to GPT-Live → Validate the migration](https://developers.openai.com/api/docs/guides/live-migration):
  - "Interruptions: Speak while the assistant is talking or working. Verify the conversation, audio
    playback, and backend task state independently."
  - "If a booking request loses its response, check whether the booking succeeded before retrying."

---

## 6. OpenAI vs OpenJarvis

OpenJarvis sources (HEAD `65d429ec`, read-only):

- `src/openjarvis/server/voice/llm.py`;
- `src/openjarvis/agents/action_ledger.py`;
- `src/openjarvis/agents/_stubs.py` (`AgentWorkerLease`);
- `src/openjarvis/tools/_stubs.py` (`ToolExecutor.execute`);
- `src/openjarvis/server/voice/background.py`;
- `src/openjarvis/mcp/client.py`;
- spec `docs/superpowers/specs/2026-10-10-background-actions-design.md` (D2–D4, §5.4–5.5, §9).

| Aspect | OpenAI Realtime API + Agents SDKs | chatSupervisor demo | GPT-Live guidance | OpenJarvis |
|---|---|---|---|---|
| **What barge-in cancels** | The in-progress *Response* (auto with `interrupt_response`, else client `response.cancel`). The unheard audio is truncated (§1.1). SDKs emit `audio_interrupted` and truncate (§2). | Same, via the server over WebRTC; manual `interrupt()` for push-to-talk and typed text (§3). | Speech only: "Stopping speech leaves backend work running" (§5). | `InterruptionFrame` → `VoiceTurnState.interrupt()` fences the turn, and any event turn is cancelled (`llm.py:488-499`, `101-121`). Pipecat cancels the processing task, so the Agent's model turn and Responder stream end. |
| **Fate of an in-flight tool** | Keeps running. SDK tool tasks are cancelled only on session close (Py) or reconnect (JS) (§2). | Keeps running: the supervisor `fetch` has no abort (§3). | Keeps running; the app decides finish or cancel, and confirms a cancel before saying so (§5). | Keeps running for dispatched writes (spec D2 "already acting → finish and report"; `ActionLedger` docstring `action_ledger.py:3-6`). `AgentWorkerLease.run_sync` shields the worker (`_stubs.py:134-147`), and a dispatched tool's nested steps finish (`tools/_stubs.py:30-33`, `157-159`). Two exceptions: a *queued* write of a cut run leaves the queue (`tools/_stubs.py:160-167`), and an external MCP `tools/call` registers for caller cancellation and is dropped (`mcp/client.py:177-187`, `agents/_stubs.py:216-228`). |
| **Is the tool output sent back to the model?** | Yes, always: `function_call_output` on the same `call_id`, then `response.create` queued until idle (JS `backgroundResult` skips the response) (§2). | Yes, `function_call_output` plus an immediate `response.create` (SDK 0.0.5) (§3). | Yes, as a result per required call ("Submit a result for every required call"); app-reviewed with client delegation (§5). | Not to the cancelled turn. The outcome goes into `ActionLedger` keyed `run_id:call_id`, with status `succeeded` / `failed` / `unknown`, a `customer_message` or an error (`action_ledger.py:65-140`). The next run sees it as `background_actions` (`background_for`, `:142-163`). |
| **How the result reaches the user** | A model response generated after the barge-in turn's response finishes (inference from the sequencer). The GA model says "still waiting" in between (§1.2). | The chat agent voices the supervisor text (§3). | GPT-Live paraphrases `session.commentary.append` / tool results; progress via `session.thinking.append` (§5). | (a) Folded into the next customer turn: the carried prompt `<earlier_unfinished_request>` / `<latest_request>` (`llm.py:71-92`) plus `background_actions` with `already_told` (`action_ledger.py:142-163`). (b) With no turn running, `BackgroundReporter.on_idle` speaks a verified `customer_message`, or runs `run_event_turn` for failed or unknown outcomes, only when nobody is talking (`background.py:79-104`; `llm.py:803-842`; spec D3). |
| **Stale-result protection** | None tied to barge-in. Py exits only if the session is closing (`session.py:1284-1294`); JS drops only results from an older connection generation (`realtimeSession.ts:925-932`). | None. | Task revision + operation ID; "discard results for outdated requests"; "discard late results from blocked or superseded requests" (§5). | A turn-id fence on frames (`llm.py:781-782`, `634-636`); a prompt rule "If the latest request contradicts the earlier one, follow the latest" (`llm.py:89-91`, model-enforced, spec §9 risk 4); a 5 s write gate (`WRITE_WAIT_SECONDS`, `tools/_stubs.py:26-28`); the D4 hold — a later write is refused while an unseen failed or unknown outcome exists (`_earlier_action_unsettled`, `tools/_stubs.py:195-224`). No application-level task revision. |
| **Filler / preamble** | Model-generated preambles (`gpt-realtime-2` by default) in the commentary channel; guidance on when not to use them (§1.4). | Prompted filler phrase before every supervisor call (§3). | GPT-Live keeps talking while the backend works (full duplex). | A separate Responder model; delegation is its `getNextResponseFromSupervisor` call, and the text before it is the filler. A 2.0 s decision deadline, then the Agent starts. A late filler is allowed only before the Agent's first text (`llm.py:65-68`, `642-729`, `743-764`). |
| **Side-effect safety / idempotency** | SDK `call_id` duplicate detection; no re-run of an executed call whose output was not committed (`session.py:1169-1183`). Nothing about retries of ambiguous outcomes. | None (read-only demo tools). | "check whether the original action already happened" before retry; "execute the function once"; report completion only after confirmed success (§5). | The `reported` flag is the single source of truth, marked before speaking (`background.py:93-100`); `already_told` for carried requests; the generic rule "Actions it already started are listed in background_actions; do not repeat them" (`llm.py:89-91`); a `pending_operation` result instead of a second dispatch (`tools/_stubs.py:172-183`); `outcome_unknown` → status `unknown` (`action_ledger.py:114-116`). |

**The 2026-10-17 11:23:53–58 case, mapped onto the OpenAI semantics (inference).** As described: the
add-to-cart HTTP call timed out at 5 s, the customer interrupted at 4 s, and the failure was reported next
turn ("Lượt gọi món bị lỗi; thử lại nhé?").

- **Agents SDK:** the tool task would also have kept running past the barge-in. Its timeout or error string
  would then be sent as `function_call_output`, with a `response.create` queued behind the customer's new
  response. So OpenAI's reference behaviour would also surface the failure after the interrupting turn.
  OpenJarvis's choice to report it in the next turn is consistent with that.
- **GPT-Live guidance:** it adds that an HTTP timeout on a write is an *unclear* outcome. The app should
  check whether the item was in fact added before offering a retry, and say the outcome is unclear rather
  than "failed".

---

## 7. Gaps / what OpenJarvis could adopt

Each item cites the primary source that supports it. Mapping to OpenJarvis is **(inference)**.

1. **Classify write timeouts as "unknown", and check before retrying.**
   - Source: "Before retrying a failed tool call, check whether the original action already happened … If
     the outcome is unclear, say so" ([live-delegation](https://developers.openai.com/api/docs/guides/live-delegation));
     "If a booking request loses its response, check whether the booking succeeded before retrying"
     ([live-migration](https://developers.openai.com/api/docs/guides/live-migration)).
   - OpenJarvis already has the `unknown` status (`outcome_unknown`, `action_ledger.py:114-116`).
   - Whether the add-to-cart HTTP timeout path sets `outcome_unknown` was not verified. If it reports
     `failed`, the "thử lại nhé?" retry can double-add an item the merchant did accept **(inference)**.
     The reconciliation step itself (re-read the cart before re-adding) belongs in skill/merchant
     configuration per repo rules.
2. **Add an application-level task revision to discard superseded results.**
   - Source: "Give the task a new version number … so your application can recognize results from the
     earlier request" ([live-migration](https://developers.openai.com/api/docs/guides/live-migration)); "Track
     each application action with its own operation ID and each changed request with a task revision … to
     discard results for outdated requests"
     ([live-delegation](https://developers.openai.com/api/docs/guides/live-delegation)).
   - OpenJarvis keys outcomes by `run_id:call_id` (an operation ID equivalent), but "latest wins" is left to
     the model (`llm.py:89-91`; spec §9 risk 4).
3. **Keep "stop talking" separate from "cancel the task", and say "cancelled" only after confirmation.**
   - Source: [live-prompting → Interruptions](https://developers.openai.com/api/docs/guides/live-prompting);
     "confirm it succeeded before telling the user that the work was canceled"
     ([live-delegation](https://developers.openai.com/api/docs/guides/live-delegation)).
   - OpenJarvis D2 already finishes acting work on barge-in. What is missing is an explicit customer-requested
     cancel path for a running write, reported only after the backend confirms it **(inference)**.
4. **Check the mixed cancellation semantics.** OpenAI's model is "backend work continues; app decides". In
   OpenJarvis an external MCP `tools/call` *is* dropped on barge-in (`mcp/client.py:177-187`) while other
   dispatched tools finish (`tools/_stubs.py:157-159`).
   - If a dropped MCP call can have had a side effect, the ledger should record it as `unknown` rather than
     not-run **(inference; not verified which tools are external MCP)**.
   - Source for the principle: "Check whether a running operation actually stopped"
     ([live-migration → Validate the migration](https://developers.openai.com/api/docs/guides/live-migration)).
5. **Add a test scenario OpenAI calls out.** "a trigger while a tool is running, and late results from
   canceled work" ([voice-server-controls](https://developers.openai.com/api/docs/guides/voice-server-controls));
   "Speak while the assistant is talking or working. Verify the conversation, audio playback, and backend
   task state independently" ([live-migration](https://developers.openai.com/api/docs/guides/live-migration)).
   A bounded scripted case (slow add-to-cart, barge-in at 4 s, tool timeout at 5 s) would assert the ledger
   status, the carried prompt and what was played, separately.
6. **Tool-failure and preamble wording**, as merchant prompt configuration and not runtime code:
   - "If the failure may be temporary, offer to retry once" and "Do not repeatedly call the same tool with the
     same arguments after failure" ([voice-prompting → Recover from tool failures](https://developers.openai.com/api/docs/guides/voice-prompting));
   - skip a preamble when "the user is only confirming, correcting, or declining something" or "the tool call
     is lightweight" ([voice-prompting → Preambles](https://developers.openai.com/api/docs/guides/voice-prompting)).
7. **Not worth adopting (inference).**
   - The SDKs' automatic `response.create` after a late tool result starts a model turn as soon as the
     current response ends. OpenJarvis's idle-only reporting (spec D3, "never speak over the customer") is
     stricter.
   - The SDKs have no stale-result check, which OpenJarvis's fences and D4 hold already exceed.
   - JS `backgroundResult` corresponds to OpenJarvis's "fold into the next turn" path.

---

## Unverified

- **Server item status after barge-in.** Whether the server marks a `function_call` item `incomplete`
  (rather than `completed`) when a barge-in cancels the response mid-arguments. The reference only says
  `.function_call_arguments.done` / `.output_item.done` are "also emitted when a Response is interrupted,
  incomplete, or cancelled". The SDK safety relies on `status == "completed"`.
- **`response.create` while a response is active.** What the Realtime server does then (error code or
  queueing) is undocumented beyond "Only one Response can write to the default Conversation at a time". The
  current SDKs avoid it with sequencers. The demo's SDK 0.0.5 sends it immediately, and that runtime outcome
  was not observed.
- **Missing outputs in a Realtime session.** Whether a `function_call` that never receives a
  `function_call_output` ever causes an error or degraded behaviour later. The docs are silent, and the
  developer notes only say pending calls are allowed and get a placeholder.
- **The `response.cancelled` event.** The realtime-conversations guide names it, but it does not exist in
  the server-event reference, which uses `response.done` with `status: "cancelled"`.
- **The gpt-realtime launch announcement** (`openai.com/index/introducing-gpt-realtime`) returned HTTP 403.
  Its "asynchronous function calling" wording was seen only in a search snippet and is not relied on; the
  developer-notes page is cited instead.
- **Developer-notes wording.** The notes say "the GA Responses API adds placeholder responses"; context
  suggests the GA *Realtime* API, but this was not verified. The developer-notes page is a first-party
  developer blog, not reference documentation.
- **The JS docs' blocking claim** ("While the tool is executing the agent will not be able to process new
  requests from the user"), given the non-blocking `function_call` listener and GA pending-call behaviour.
  Not tested at runtime.
- **Default value of `interrupt_response`.** The reference does not state it. The VAD guide's example sets
  it to `true`, and the Python SDK default sets it to `true`. The demo leaves it unset.
- **The demo's runtime behaviour** (whether the supervisor result is voiced after a barge-in, or rejected).
  Not run.
- **OpenJarvis, add-to-cart path. Resolved after the report, from `traces.db` `run_events` and the skill.**
  - In run `8941d266…` the step that timed out was the skill's first step, the menu read
    (`http_request` `GET …/menu/specific/public`, `timeout: 5`). It failed after 5.48 s with
    `Request timed out after 5s`, and the skill returned `source_unavailable` (`steps: 1`).
  - `run_interrupted` was logged at +4.06 s, before the timeout.
  - The second step, `display_cart`, never ran, and that step only changes the kiosk's local cart
    (`skills/trendcoffee-add-to-cart.toml:50-57`). Nothing was written to the merchant, so `failed` was
    correct and the retry could not add the items twice.
  - `outcome_unknown` is set for skills only when a checkout write was attempted
    (`skills/tool_adapter.py:354-357`). This matches item 1 of §7 for the one call that does write.
  - The only external MCP server is Playwright, configured `presentation_only: true`
    (`ordering-kiosk-mcp.toml:229`). Dropping its call on barge-in therefore has no business side effect.
- **GPT-Live scope.** GPT-Live is a different product (`gpt-live-1`). Its guidance is cited as OpenAI's
  stated design intent, not as Realtime API behaviour.
- **Not reviewed:** OpenAI cookbook notebooks (for example the voice-agent evaluation cookbook), and the
  `openai-python` / `openai-node` client libraries.
