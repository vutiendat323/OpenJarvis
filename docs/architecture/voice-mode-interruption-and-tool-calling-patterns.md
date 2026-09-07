# Voice Mode Interruption & Tool Execution Architecture: Industry Patterns & Best Practices

## Executive Summary

In conversational voice agents (Voice Mode / Full-Duplex Speech-to-Speech), handling user barge-in (interruptions) during the **"LLM Reasoning & Tool Execution"** phase is one of the most critical and complex architectural challenges. While interrupting TTS playback is straightforward (flushing audio buffers), interrupting while the LLM is generating tokens or when external tools are executing network requests requires careful coordination of **microphone state, concurrency locks, cancellation tokens, and transaction idempotency**.

This document details how Big Tech (OpenAI Realtime API, Google Gemini Multimodal Live, ElevenLabs) and premier open-source voice frameworks (LiveKit Agents, Pipecat AI) solve these challenges, citing primary sources, and outlines concrete recommendations for OpenJarvis.

---

## 1. Microphone State During "LLM Reasoning & Tool Calls"

### The Question: Should the microphone be turned off / muted during LLM reasoning and tool calls?

### The Industry Standard: **NO. The microphone is NEVER hardware-muted or disabled.**

### Detailed Rationale & Primary Sources:

1. **Full-Duplex Contract (OpenAI Realtime API & Google Gemini Live)**:
   * **Source**: [OpenAI Realtime API Specification](https://platform.openai.com/docs/guides/realtime-websocket) & [Google GenAI Gemini Live Multimodal API](https://ai.google.dev/gemini-api/docs/multimodal-live).
   * Both APIs maintain continuous, uninterrupted bidirectional audio streaming over WebRTC or WebSockets.
   * In OpenAI Realtime API, the client streams audio continuously using `input_audio_buffer.append`. The server's Voice Activity Detection (VAD) monitors input constantly, even when the server is in the middle of executing a function call or generating tokens (`response.create`).
   * If the client were to mute the microphone:
     * The conversational loop degrades into half-duplex Walkie-Talkie mode.
     * The customer cannot say natural conversational corrections like *"Khoan đã"*, *"À nhầm, cho mình hủy"*, *"Đợi mình tí"*, or *"Thôi lấy món khác"*.
     * In noisy kiosk environments, hard-muting creates jarring threshold pops and breaks acoustic echo cancellation (AEC).

2. **Acoustic Echo Cancellation (AEC) & VAD State**:
   * **Source**: [WebRTC AEC3 Architecture](https://webrtc.googlesource.com/src/+/refs/heads/main/modules/audio_processing/aec3/).
   * Turning the microphone stream on and off causes the WebRTC AEC adaptive filter to reset its echo model, leading to speaker echo feedback loops.

3. **What ACTUALLY changes during Reasoning?**:
   * The microphone remains **physically live** and streaming PCM audio to the VAD layer (Silero VAD, WebRTC VAD, or Cloud Server VAD).
   * Only the **state machine interpretation** of incoming speech changes: instead of appending text to an idle prompt, detected speech triggers an **Interruption Event** that preempts or reprioritizes the in-flight reasoning task.

---

## 2. Handling Barge-in: TTS Playback vs. LLM Reasoning & Tool Calls

Conversational interruptions fall into two distinct phases with different mechanics:

### Phase A: Interruption During Speech Playback (Speaking Phase)
* **Mechanics**:
  1. User starts speaking (`VADUserStartedSpeakingFrame` in Pipecat, `input_audio_buffer.speech_started` in OpenAI).
  2. The audio output buffer on the client is immediately cleared (packet drop).
  3. The TTS generation queue is canceled and flushed.
  4. The assistant turn transcript in the conversation history is truncated to the exact millisecond the user spoke (`conversation.item.truncate`).
* **Complexity**: Low. Purely output-side cancellation.

### Phase B: Interruption During LLM Reasoning & Tool Execution (Thinking/Processing Phase)
* **Mechanics**:
  1. The user stopped speaking turn 1, and the system entered `Processing...`.
  2. While the LLM is generating tokens or an asynchronous tool (e.g. `http_request`) is running, the user speaks again (turn 2).
  3. The system must decide:
     * Does the pending LLM response get aborted?
     * What happens to the running tool?
     * How is the conversation transcript updated?
* **Complexity**: High. Involves asynchronous cancellation, concurrency locks, and potential database/API inconsistency.

---

## 3. Side-Effecting (Non-Idempotent) vs. Read-Only Tool Calls

Production systems partition tools into two strict categories:

### Category 1: Read-Only / Idempotent Tools
* **Examples**: `display_menu`, `GET /products`, `GET /catalogs`, `db_query`.
* **Handling Strategy**: **Instant Preemptive Cancellation**.
  * Handled via `asyncio.Task.cancel()` or `AbortController`.
  * The tool process is killed immediately; no side-effects exist.
  * Discard any returned payload; proceed directly to process the user's new utterance.

### Category 2: Side-Effecting / Mutating Tools
* **Examples**: `POST /orders/public`, `POST /payment/initiate/public`, charging credit cards, deducting inventory.
* **Handling Strategy**: **Atomic Execution with Deferred Context Injection**.
  * **Primary Principle**: Never kill a mutating HTTP request mid-flight with a raw network SIGKILL, because the remote server might have already processed the transaction, leaving the local agent in a desynchronized "ghost order" state.
  * **Pattern (LiveKit Agents & Enterprise Voice)**:
    1. **Detach and Await**: The in-flight mutating tool is detached from the current user response loop and allowed to finish atomically in the background.
    2. **Mute Output**: The assistant's planned verbal confirmation (e.g., *"Đơn hàng của bạn đã được đặt..."*) is suppressed because the user has already interrupted with a new request.
    3. **Context Reconciliation**: When the tool finishes, its result (e.g., `order_slug: tc-12345`) is placed into the agent's memory/evidence store. The agent processes the user's interrupting utterance with full awareness that the order was placed, allowing natural dialogue:
       * *User*: *"Khoan, đổi sang ly lớn giùm mình!"*
       * *Agent*: *"Dạ đơn hàng vừa được tạo, em sẽ cập nhật đổi ly sang size lớn cho bạn ngay ạ."*

---

## 4. Architectural Patterns in Leading Frameworks

### A. OpenAI Realtime API (`response.cancel`)
* **Source**: [OpenAI Realtime API Protocol](https://platform.openai.com/docs/guides/realtime-websocket#cancelling-a-response).
* When a user speaks while a response is being generated:
  * Server automatically emits `response.cancelled`.
  * If a client-side tool is executing, the client sends `response.cancel` to tell the server to discard the pending response turn.
  * The newly received audio is buffered into a fresh turn (`conversation.item.create`).

### B. Pipecat AI (Frame Pipeline & Interruption Propagation)
* **Source**: `pipecat.processors.aggregators.llm_response_universal` and `pipecat.services.google.gemini_live.llm`.
* **Frame Flow**:
  * User audio triggers `VADUserStartedSpeakingFrame`.
  * The user aggregator calls `broadcast_interruption()`, sending an `InterruptionFrame` downstream and upstream.
  * `VieNeuTTSService` and output transports receive `InterruptionFrame` and instantly purge their audio buffers.
  * In the LLM service, any running generation loop checks `asyncio.current_task().cancelled()` or task cancellation signals and exits early.

### C. LiveKit Agents (`livekit-agents` v0.8+)
* **Source**: [LiveKit Agents Repository](https://github.com/livekit/agents).
* **Architecture**:
  * Employs `FunctionContext` with explicit `@llm.ai_callable(interruptible=False)` flags.
  * If a tool is marked `interruptible=False`, an incoming user speech event does NOT cancel the tool task. The agent waits for the critical tool to return, merges the result into conversation context, and immediately answers the interrupting question.
  * If marked `interruptible=True`, the Python coroutine receives `asyncio.CancelledError` and terminates within 5ms.

---

## 5. Root Cause Analysis of the 41-Second Lockup in OpenJarvis

In the trace recorded at 17:44:24 (`thanh toán đơn giúp mình`):
1. **Lock Contention**:
   ```text
   WARNING openjarvis.agents.runtime: TEMP-DIAG _run_stream: waiting for lock, input='thanh toán đơn giúp mình'
   WARNING openjarvis.agents.runtime: TEMP-DIAG _run_stream: LOCK ACQUIRED after 0.000s, input='thanh toán đơn giúp mình'
   ... [41.137s elapsed] ...
   WARNING openjarvis.agents.runtime: TEMP-DIAG _run_stream: LOCK RELEASED elapsed=41.137s
   ```
2. **The Mechanism**:
   * `_run_stream` holds a single monolithic `asyncio.Lock` over the entire turn: DeepSeek API call + HTTP tool calls + multi-turn ReAct iterations.
   * DeepSeek Cloud experienced high latency or multi-step reasoning overhead (~40s).
   * While `_run_stream` held the lock, the user spoke again at second 41 (`VADUserStartedSpeakingFrame`).
   * Pipecat broadcast an `InterruptionFrame`, but because the backend was blocked inside a non-interruptible network call without an active task cancellation hook, the lock could not be released until the 41s HTTP/LLM call completed.
   * During this entire 41 seconds, the UI remained frozen on `Processing...`.

---

## 6. Concrete Architectural Recommendations for OpenJarvis

### 1. Never Hardware-Mute the Microphone
* Keep microphone streaming active at all times.
* Let VAD detect user barge-in continuously.

### 2. Add Audio Fillers & Immediate Feedback (Target TTFT < 1.5s)
* When a turn involves complex multi-step reasoning or payment tools, immediately stream a brief acknowledgment audio frame (e.g. *"Dạ em đang tạo mã thanh toán cho bạn nhé..."* or subtle earcon/chime) within 800ms so the user knows the system is working and does not think it crashed.

### 3. Asynchronous Cancellation Hook in `_run_stream`
* Wrap the `engine.generate()` and `engine.stream()` coroutines in an `asyncio.Task`.
* When an `InterruptionFrame` arrives in `OpenJarvisVoiceLLMService`, trigger `task.cancel()`.
* Ensure `_run_stream` uses a `try...finally` block that immediately releases the conversation lock on cancellation, allowing the next user turn to acquire the lock in 0.001s.

### 4. Categorize Tool Interruptibility
* Tag tools:
  * Read tools (`display_menu`, `GET /products`): `interruptible=True` -> cancel immediately.
  * Financial/Order tools (`POST /orders/public`, `POST /payment/initiate/public`): `interruptible=False` -> let network request complete in background, store result in evidence, and do not repeat or double-charge.

