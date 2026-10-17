# Barge-in keeps the Agent working: parallel Agent runs (OpenAI chat-supervisor)

Date: 2026-10-17. Status: user decisions applied; awaiting user review. Builds on
`2026-10-17-barge-in-tools-run-to-completion-design.md` (implemented, merged
to `main` at `2b35f846`) and changes decisions D2 and D5 of
`2026-10-10-background-actions-design.md`. Research:
`docs/verification/2026-10-17-barge-in-tools/openai-barge-in-research.md`.

## 1. Intent

The previous change made a barge-in stop speech, not tools: every tool call
the model completed now runs to its end. A barge-in still cancels the Agent's
*thinking*, though. A request whose tool call was not yet written is dropped
into a "carried" prompt, and the next turn's model may ignore it.

In OpenAI's chat-supervisor design the supervisor is never cancelled by a
barge-in. Only the chat agent's speech stops. The supervisor's own reasoning
and tool loop keep running, and its result comes back to the chat agent, which
phrases it for the conversation as it is then (research note §3). The user
chose this standard on 2026-10-17 ("đúng chuẩn OpenAI"), with latency as the
priority ("update to latency").

### Why now: live evidence (2026-10-17, voice session 2, 18:14–18:16)

| Time | Customer | What happened |
|---|---|---|
| 18:14:50 | "Cho mình một nước ép các loại mang đi." | The Responder spoke a filler; the Agent was still thinking (no tool call yet). |
| 18:14:58 | "Alo, bạn có nghe không?" | The barge-in cancelled the Agent. The juice request was carried (`answer_blocked`). The next Agent turn only said "Mình nghe rõ bạn. Bạn cần mình hỗ trợ gì ạ?" and dropped it. |
| 18:15:10 | "Bạn bỏ nước ép vô cho mình chưa?" | "Chưa, nước ép chưa được thêm vào giỏ." The customer had to ask again. |
| 18:15:35 | "Thêm tất cả các món sinh tố vào giỏ." | Agent round 1 read the menu; round 2 was still thinking. |
| 18:15:42 | "Alo, bạn có nghe không?" | Same drop: the next turn only said "Có, mình nghe rõ bạn." |
| 18:16:00 | "Bạn thêm chưa?" | A fresh Agent turn reasoned for 12 s (1153 tokens), then added the smoothies. |

Source: `.stack-logs/backend.log` lines 17721–18087.

## 2. Decisions (the user's)

| # | Decision |
|---|---|
| P1 | A barge-in never cancels an Agent run, whether it is thinking or acting. Only the speech of that run stops. This replaces D2 ("only thinking → stop and merge"). |
| P2 | A hear check, a greeting or small talk ("Alo", "bạn có nghe không?") is answered by the Responder at once. The interrupted Agent run keeps working in the background. |
| P3 | A new customer request starts its own Agent run at once, in parallel with the background run. It does not wait for the background run. This replaces D5 ("one model turn at a time"). |
| P4 | The final answer and the tool outcomes of an Agent run the customer interrupted are never read out verbatim. The fast model phrases them for the conversation as it is now, through `run_phrased_report` (already built). |
| P5 | Nothing is said to be done before it succeeded. A report accounts for what the customer said after the barge-in. |
| P6 | "Đổi sang mang đi" during a background "add the smoothies" changes the dining type only; the smoothies are still added. "Không lấy sinh tố nữa" lets the background work finish, then the cart is corrected from the real result. |
| P7 | Latency is the priority (targets in §6). |
| P8 | **Checkout waits for a stable cart.** A checkout waits until every other run of the session that may change the cart or the order details has finished, up to 15 s. The rules for that wait are in §7.7. |
| P9 | **A correction of in-flight work waits for its real result.** "Không lấy sinh tố nữa" or "ba ly thôi" while the smoothies are being added: the new run waits for that work to finish, then corrects the real cart. An independent request ("thêm một cà phê sữa", "đổi sang mang đi") runs at once; cart writes must keep both changes and never overwrite each other. The new run decides whether its request relates to in-flight work; if "ba ly" is ambiguous, it asks before changing anything. It confirms receiving the request at once and says "done" only after success. |

## 3. Goals

1. No customer request is lost because the customer spoke while the Agent was
   thinking. In the 18:14–18:16 session, both the juice and the smoothies would
   have been added without the customer asking again.
2. A new request is answered without waiting for an earlier, slower request.
3. Each interrupted run is reported exactly once, phrased for the current
   conversation, and never over the customer's voice.

## 4. Non-goals

- A task revision that discards results of superseded requests (still
  deferred by the user).
- A cancel flow for running work. There is no merchant cancel API; P6 covers
  corrections after the fact.
- Making a single Agent run faster (the 12 s reasoning at 18:16 is a model
  and prompt matter, not this design).
- Changes to Speaker fusion, STT, TTS or the Responder's filler rules. The
  Responder's input only gains the requests in flight (§7.6).

## 5. Anti-goals (failures even if the code "works")

- **The same request done twice** because two Agent runs both acted on it.
  An example is "Bạn thêm chưa?" starting a second "add the smoothies" while
  the first is still running.
- **Two runs speaking at the same time,** or any speech over the customer.
- **A payment past an unseen failure.** D4 still holds: no payment while an
  earlier write failed or is unknown and the customer was not told.
- **"Đã thêm" / "đã đổi" before success.**

## 6. Latency targets (P7)

| Moment | Target |
|---|---|
| Hear check ("Alo") answered | first audio ≤ 1.0 s after the customer stops, p50 (the Responder alone; deepseek-flash p50 ≈ 0.84 s measured) |
| New request while a run is in the background | its Agent run starts within 0.1 s of the Responder's decision, never after the background run |
| Late result of an interrupted run | first audio ≤ 1.5 s after the session goes idle, p50 (`run_phrased_report`, measured line p50 0.95 s on 2026-10-17) |

## 7. Required behaviour

### 7.1 Barge-in

An `InterruptionFrame` still stops TTS and fences the current turn's frames.
It no longer cancels the Agent run of that turn. The run continues as a
**background run**:

- its text deltas are consumed and never spoken;
- its tool calls run as they do today, through committed dispatch;
- its final answer is kept for the report (§7.5).

Today the cancellation comes from Pipecat cancelling the processing task that
awaits the Agent stream (`server/voice/llm.py` `process_frame` → `_answer` →
`_consume_agent` → `binding.run_stream`). The run must be owned by a task
that the interruption does not cancel.

### 7.2 The interrupting words

- The interrupting words become a normal customer turn, with the Responder
  deciding first (P2).
- A hear check, a greeting or small talk is answered by the Responder alone.
- Nothing is carried. The earlier request is still being worked on, so the
  `answer_blocked` rule for carried turns no longer applies to it.
- A business request delegates to the Agent and starts a new run at once
  (P3). This run is the **foreground run**: it speaks.

### 7.3 The foreground run knows what is in flight

The new run's input names every request whose background run is still
working, in the customer's words, with a rule written once in runtime code
(site-agnostic):

- those requests are already being handled; do not perform them again;
- a question about them ("Bạn thêm chưa?") is answered as still in progress,
  unless `background_actions` shows the outcome.

Tool outcomes that already finished reach the run through
`background_actions` as today. This replaces `carried_prompt` for
interrupted turns: it is the anti-goal guard against doing a request twice.

### 7.4 Concurrency

- **At most 2 Agent runs per voice session:** one foreground and one
  background. When a third is needed, the newest request waits until one of
  them finishes. It is admitted with the same rules as today.
- **Cart and order writes stay serialized** by the existing per-runtime
  `AgentToolGate`. A write waits up to `WRITE_WAIT_SECONDS = 5.0`, then gets
  the existing `pending_operation` answer. D4 (`_earlier_action_unsettled`)
  stays.
- **Reads never wait,** as today.
- **The checkout nonce stays per run** (its own `agent_turn_scope`), with the
  committed-dispatch rules of the previous spec.
- **Writes of two runs of the same session wait for each other until the
  holder finishes.** They are bounded by the tool's own timeout, not by
  `WRITE_WAIT_SECONDS`. This applies to `display_*` calls too, which count
  as writes. A write of a parallel run is therefore never dropped as
  `pending_operation`, and Goal 1 holds. The 5 s bound stays for a write
  that waits behind a run of another session.
- **The retained-lease cap (4) and the runtime admission stay in force.**
  The runtime lock (`NativeAgentRuntime._lock`) must admit 2 concurrent
  streams per voice session instead of 1.

### 7.5 Reporting a background run

- A background run is reported once, when it finishes. The report covers its
  final answer and the outcomes of its tool calls, so that it is not told
  twice.
- If a foreground run is active and its model sees the outcomes in
  `background_actions`, that run reports them, as today (D3).
- Otherwise the reporter, when nobody is talking and no run is speaking,
  calls `run_phrased_report` with an event built from:
  - the background run's final answer;
  - its tool outcomes;
  - the customer's words since the barge-in.
- The fallback is unchanged: on `"failed"`, the Agent event turn reports it.
  On `"cut"`, the report stays pending.
- The background run's final answer is never spoken verbatim (P4).

### 7.6 What other runs and the Responder can see

- **A finished background run leaves a run-level ledger entry:** its request
  in the customer's words, its final answer and its status. It is visible to
  other runs in `background_actions`, like tool outcomes.
  - A run that ended with a question or a fact and no write ("hết nước ép",
    "loại nào ạ?") is therefore still seen, and its request is not redone.
- **"Reported" is tracked per run.** When a foreground run folds a background
  run's outcomes into its reply, the whole background run counts as
  reported, including its final answer. The customer never hears it twice.
- **The Responder's input names the requests in flight,** in the customer's
  words, with runtime wording that is site-agnostic. A hear check during a
  background run is answered with "I hear you" and what is being worked on.
  It does not answer "what would you like?", which at 18:14:58 made the
  customer repeat the request.

### 7.7 Checkout while other work is in flight (P8)

- **Waiting.** Before its first write, a checkout waits until no other run of
  the session that may change the cart or the order details is in flight.
  The wait is at most 15 s.
- **Timeout.** If the 15 s pass, no order is created. The customer is told the
  cart is still being updated and is asked whether to wait.
- **A new purchase request during the wait.** If the customer adds or changes
  something, the pending checkout is dropped. The change is handled, then the
  customer is asked whether to pay. A hear check ("Alo") does not drop the
  pending checkout.
- **Locked from cart freeze to dispatch.** No cart change may slip in between
  the moment the checkout freezes the cart and its dispatch.
- **After dispatch.** Once the checkout is dispatched, it runs to its end as
  the previous spec requires. Nothing promises to stop or cancel the order.

### 7.8 A request that is waiting for a slot

A delegated request that is waiting for a free run slot (§7.4) counts as a
run for P1: a barge-in does not drop it. Waiting requests are served in
arrival order.

### 7.9 Session end

The end of the voice session cancels every run of that session, background
ones included. Committed checkouts stop at their next step through the revoke
built in the previous spec (§5.8 there).

## 8. Left to the builder

- The task ownership that keeps a run alive through an interruption, and how
  its frames are discarded.
- How the runtime admits two streams per voice session, and what a third
  request waits on.
- How the foreground run's input names in-flight requests (§7.3). It must use
  runtime wording that is site-agnostic, with tests of the "do not repeat"
  rule.
- How the reporter recognises a finished background run and builds its event.
- Test layout, mirroring `src/openjarvis/`.

## 9. Calls made without the user (check while reading)

- **At most 2 concurrent Agent runs per session.** One background and one
  foreground cover every case in the evidence. More would multiply the
  write-gate waits and the retained leases.
- **In-flight requests are named in the foreground run's input (§7.3).** This
  is the guard against the anti-goal "same request twice", which parallel
  runs make possible.
- **One phrased report per background run, covering its answer and its
  tools.** Reporting each tool separately would speak several lines for one
  request.
- **Run-level ledger entries, "reported" tracked per run, and the Responder
  told what is in flight (§7.6).** These were needed so a run that ended
  without a write is not redone, nothing is told twice, and "Alo" is not
  answered with "what would you like?".
- **A request waiting for a slot survives a barge-in (§7.8).**
- **Same-session writes wait until the holder finishes (§7.4).** A 5 s drop
  would lose a customer's request, which contradicts Goal 1.
- **The hear-check target (1.0 s) uses the Responder's measured latency.**
  The other two targets reuse numbers already measured on 2026-10-17.

## 10. Verification

Bounded suites, each in its own process.

1. **A barge-in while the Agent is thinking:** the run finishes, its tool
   runs, nothing of it is spoken, and one phrased report follows when idle.
2. **"Alo" during a background run:** the Responder answers. No carried
   prompt is built, and the background run continues.
3. **A new request during a background run:** its run starts before the
   background run finishes (a fake slow engine proves the overlap). Only the
   new run speaks.
4. **"Bạn thêm chưa?" during a background "add" run:** the foreground run does
   not call the add tool again, and answers that it is in progress.
5. **A third request while two runs are active** waits for a slot; no third
   concurrent run.
6. **Writes from two runs** are serialized by the gate; D4 still holds a
   payment after an unseen failure.
7. **Checkout during a background run (P8):**
   - "thanh toán" while "add the smoothies" is thinking: the order contains
     the smoothies;
   - after 15 s with the background run still going: no order, and the
     customer is asked;
   - "thêm cà phê sữa" during the wait: the checkout is dropped and the
     customer is asked to pay again;
   - "Alo" during the wait: the checkout is kept.
8. **Correction of in-flight work (P9):** "không lấy sinh tố nữa" during
   "add the smoothies" ends with no smoothies in the cart. "Thêm một cà phê
   sữa" during it ends with both. Ambiguous "ba ly" asks first.
9. **A finished background run with no write** ("loại nào ạ?") is visible
   to the next run, and its request is not redone.
10. **The session ends during a background run:** both runs end, and the
   committed checkout stops at its next step.
11. **Existing suites keep their pass counts:** voice LLM, background,
   lifecycle, carry-over (its expectations change where they encoded the
   carry of interrupted turns; each flip named), orchestrator, runtime, write
   queue.
12. **Live check after a restart the user requests:** replay the 18:14–18:16
   session.
   - Juice + "Alo": the juice is added without asking again.
   - Smoothies + "Alo" + "Bạn thêm chưa?": no second add, and the answer is
     "đang thêm" or the result.
   - Smoothies + "thêm một cà phê sữa": both are added, the second without
     waiting.
   - Smoothies + "đổi sang mang đi": the smoothies are added and the order is
     take-out.
   - Latencies from `backend.log` (`voice_preamble`, `voice_phrased_report`,
     run start times) against §6.

## 11. Risks

- Two runs share one conversation history. The foreground run does not see
  the background run's unfinished reasoning, only its request (§7.3) and its
  finished tool outcomes.
- A background run may end by asking a question ("Bạn muốn dùng tại quán hay
  mang đi?") that the customer already answered. Phrasing with the current
  conversation (P4) is the mitigation, and the live check covers it.
- Model cost rises when a customer talks over a long run, because two runs
  are billed. The cap of 2 bounds it.
