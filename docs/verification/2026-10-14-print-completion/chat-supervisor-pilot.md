# Chat-Supervisor pilot: Responder voices a verified `PRINT_COMPLETED`

Date: 2026-10-14. This is a proposal only. No code, config, test or service was changed and nothing was run live. It builds on `notification-solution-research.md` (option C) and does not repeat it.

## Current behaviour (checked against the working tree)

| Path | Who writes the words | Tools offered | Source |
| --- | --- | --- | --- |
| Customer turn | Responder: preamble or a simple `ANSWER`. Agent: all business text, streamed directly to TTS | Agent: all | `server/voice/llm.py:563`, `:693` |
| Idle outcome with a tool-written `customer_message` | The Agent wrote it during the original turn. The reporter speaks it verbatim | none | `server/voice/background.py:93` |
| Idle outcome without one (failure, unknown, `PRINT_COMPLETED`) | The Agent event turn, on the Responder's model with reasoning forced off | **all, full authority (`nullcontext()`)** | `llm.py:770-776`, `agents/orchestrator.py:361`, `:376` |

`PRINT_COMPLETED` enters the ledger as a code-verified fact: run id `background-event`, status `succeeded`, `event_context` set to the worker's fields including `verification=cups_job_completed_successfully`, and no `customer_message` (`server/app.py:158`, `agents/runtime.py:126`, `kiosk/printing.py:246`). Only the Agent prompt carries the pickup wording (`configs/openjarvis/prompts/ordering-kiosk.md:100`).

The OpenAI references describe the responder as rephrasing the thinker's text for speech. Neither page states a rule against inventing results. That rule comes from this design.

## Minimal result contract

Add no new type. The verified result is the existing `LedgerEntry`, and the Responder receives only `entry.as_context()` (`agents/action_ledger.py:26`).

A Responder may voice an entry only when all of the following hold:

1. `run_id == "background-event"`: code produced the facts, not a model.
2. `status == "succeeded"` and `customer_message` is empty.
3. The preset sets `[voice.responder] notice_prompt_file`. This is the pilot switch and the rollback path. Without it, today's event turn runs.

Every other entry keeps today's path. Failures and unknown states still go to the Agent event turn, because they may need a recovery decision.

## Boundary

- **Agent/workflow (Thinker):** owns tools, checkout and printing truth. Unchanged.
- **Responder:** new `announce(outcome: dict[str, str], conversation: str) -> str | None`. It uses a separate prompt from `acknowledgement`, which treats its input as the customer's words and must never state a result (`responder.py:136`). It makes one engine call with **no `tools` argument** and returns the whole line or nothing. The merchant's pickup wording lives in the preset's notice prompt file, not in core code.
- **LLM service:** new `run_notice_turn(outcome) -> True | False | None` reuses the `_event_task` slot and its guards. It does not start while a customer turn is being answered. `InterruptionFrame`, `EndFrame` or `CancelFrame` cancel it. The text it pushes carries `openjarvis_is_current`. `True` means spoken, `False` means interrupted (the entry stays unreported), and `None` means the Responder returned no line.
- **Reporter:** decides the route for each entry. On `None` it falls back to `run_event_turn` once. On `False` it does nothing. This gives one speaker per outcome: the fallback runs only when no Responder text was produced.
- **History:** the notice must enter the Pipecat context, because the next customer reply ("menu") answers the notice's question. `TTSSpeakFrame.append_to_context` defaults to `True` in Pipecat 1.8.1. Whether `LLMTextFrame` pushed outside `LLMFullResponseStart/End` is aggregated needs a test.

## Benefit compared with today's event turn

| Claim | Status |
| --- | --- |
| Tools cannot be called again: the Responder is given no tools | Structural. Today only the prompt stops the event turn from navigating or repeating checkout |
| Smaller request: no tool schemas, no full kiosk prompt | True by construction. The latency effect is **unmeasured** |
| Same voice and language rules as the preambles | Plausible. Needs a live evaluation |
| Cost: the pickup wording is in two prompts (the Agent still folds `background_actions` into a running turn) | Real drift risk |
| Cost: the Responder call is outside `TraceCollector` | A shape-only WARNING log is needed, like `voice_preamble` |

## Regression tests to write first (only if implementation is requested)

| Area | Test |
| --- | --- |
| Ownership | The notice does not start while a customer turn runs. A new customer turn cancels it without waiting for it. It uses only this session's conversation |
| No double speech | When the Responder returns text, `run_event_turn` is never called. When it returns `None`, exactly one event turn runs. A real lifecycle speaks each outcome once. A notice never gets a preamble |
| No tool calls | The fake engine sees no `tools` kwarg. `binding.run_stream` is not called. No new ledger `start` entry appears |
| Interruption | An interruption during generation pushes nothing, leaves the entry unreported and does not fall back. The next idle reports it once. `EndFrame` and `reporter.stop()` cancel the notice |
| Language | `announce` receives `recent_conversation` from a Vietnamese or an English session. Touch-only checkout passes an empty conversation. The core default prompt carries no business wording. Fakes cannot prove the spoken language; a live evaluation can |
| Unchanged | Failure or unknown print states still use the event turn. `test_print_completion_reaches_agent_and_order_status` and the current `test_voice_{background,llm,responder}` tests pass |

## Decisions for the user (not made here)

1. **Effort for `announce`:** reuse the Responder's existing reasoning-off options, or use the operator preset. A fair comparison with option B needs the same model and effort on both sides.
2. **When an interruption hits during audio:** mark the entry reported before speaking, as the verbatim path does (the notice may be lost), or after (the whole notice may repeat, as the event turn does today).
3. **Duplicate pickup wording:** accept it in two prompts for the pilot, or keep the event turn.

## Extending the proposal to customer turns (evaluated, not recommended now)

Data source: `~/.openjarvis/traces.db`, read-only, last 4 days, 1,087 completed customer runs. The models are mixed (deepseek-flash, gpt-5.6-luna, gpt-6-luna) and harness runs are not separated. Interrupted runs are excluded.

| How the run ends | Share | What the customer hears now | Cost of adding a Responder step |
| --- | --- | --- | --- |
| Tool `customer_message` | 48% (523) | The Agent writes the line before the tool runs. Code releases it only after the whole batch succeeds (`orchestrator.py:628`) | One extra model call where there is none today. Estimate: about +0.8 s (deepseek-flash full preamble line p50 0.76–0.84 s, short output) |
| Final model round after tools | 20% (218) | `message` streams once `_intent` and `state` are accepted (`_SpeechStream`) | Wait for the whole round (stream p50 0.36–0.51 s, p90 up to 1.87 s) plus the Responder. Estimate: about +1.2 s p50 |
| No tool | 32% (346) | Agent answer | The same as above, unless the existing `ANSWER` path covers it |

The latency figures are estimates built from other measurements, not measured end to end.

- **OpenAI sources:** the Responder–Thinker rephrase step is described for a realtime speech-to-speech responder. The chained voice workflow guide (STT → agent → TTS) recommends no rephrase step, only optional checks before TTS. The kiosk is chained, with a parallel preamble.
- **Correctness:** rephrasing adds a new place where prices, quantities or payment state ("QR shown" vs "paid") can change. Today the intent contract (`intent.py:243`) and the `customer_message` success gate hold that truth in code. A runtime check of the rephrased text would need string matching, which the user has ruled out for dedup. Applying it here needs the user's decision.
- **Benefit:** one voice for language and register, and no need for `AGENT_NOTE`. There is no measurement of a mismatch today: preamble words are not logged (shape only), so the problem's size is unknown.
- **Recommendation:** keep customer turns as they are. If language consistency is the goal, first run an offline VI/EN evaluation comparing preamble and answer language, then weigh the measured mismatch rate against about +0.8–1.2 s on about 68% of completed turns.

Background event turns in the same window: 8 runs, 0 tool calls. The sample is small. The notice pilot's benefit is structural (no tools offered), not a fix for an observed failure.

## Measurement before any rollout

Measure `PRINT_COMPLETED` → first audio at the speaker for option B and the pilot, with the same model, effort, history and warm state. Use at least 30 runs per arm and report the median and p95. Record time held back by customer speech separately (see `notification-latency.md`).
