# Chat-Supervisor for OpenJarvis, OpenAI design first (realtime priority)

Date: 2026-10-14. Evaluation only. No code, config or service was changed.

## 1. OpenAI's design

| Element | OpenAI Chat-Supervisor demo | Voice prompting guide (Responder–Thinker) | GPT-Live delegation (newest) |
| --- | --- | --- | --- |
| Front voice | Realtime speech-to-speech chat agent | Realtime responder | `gpt-live-1`, full duplex, `v1/live/sessions` |
| Front's tools | **only** `getNextResponseFromSupervisor(relevantContextFromLastUserMessage)` | the same tool | client delegation: the app runs its own agent |
| Front handles alone | greetings, chitchat, repeat/clarify, collecting parameters | the same | — |
| Filler | always before delegating, never implies success or failure | the same | — |
| Supervisor | text model (`gpt-4.1`) with tools, full message history, `parallel_tool_calls=false` | stronger text model | the app's backend |
| How the result is spoken | **read verbatim** | **rephrased**: two sentences or fewer, numbers formatted for speech | `session.commentary.append`, which GPT-Live **paraphrases** |
| Correctness rules | supervisor must not guess and must not call tools with missing values | fillers must not imply a result | the app owns permissions, confirmations, records and task state; send results only after real success; barge-in does not stop backend work; discard stale results after a correction; operation ID + task revision |

Realtime language guidance: English is the default. Do not infer the language from accent. Switch only on an explicit request or a substantive utterance in another language.

## 2. OpenJarvis mapped onto it

The pipeline is chained: Gemini STT (`server/voice/pipeline.py:244`) → speaker gate (TSE/diarizer, `turn_detection.py`) → `OpenJarvisLLMService` → VieNeu TTS (`pipeline.py:335`).

| OpenAI element | OpenJarvis today |
| --- | --- |
| Front voice with filler and basic answers | `VoiceResponder`, a text model running **in parallel**. It is not in front, and the Agent speaks the results itself |
| Single delegation tool | none. The Agent receives the turn directly |
| Only send after success | `customer_message` is released only after the whole batch succeeds (`orchestrator.py:628`). QR shown ≠ paid (`ordering-kiosk.md`) |
| Do not retry an action that already ran | intent contract: "target already attempted; observe outcome, do not retry" (`agents/intent.py:152`) |
| Operation ID / stale results | `ActionLedger` (call_id/run_id, carry, settle, already_told) and `turn_state` invalidation on interruption (`server/voice/llm.py:496`) |
| Corrections during backend work | the interrupted request is carried and the latest one wins (`llm.py:517`) |

Conclusion: the **correctness half** that OpenAI requires already exists. The missing part is the **front voice that owns the speech and delegates**.

## 3. Realtime options for OpenJarvis

| Option | What the customer hears | Fit | Blockers or risks |
| --- | --- | --- | --- |
| A. `gpt-realtime-2.1` speech-to-speech | OpenAI voice | the OpenAI demo exactly | loses the VieNeu voice the user chose. Vietnamese is not on an official list. The speaker gate must run before audio reaches the model (Pipecat: local turns, `turn_detection=False`) |
| B. `gpt-realtime-2.1`, audio in, `output_modalities=["text"]` → VieNeu | VieNeu voice | keeps the voice. Pipecat 1.8.1 supports it (`OpenAIRealtimeLLMService`, text-only modality, local turns) | Vietnamese understanding compared with Gemini STT is unknown. Community reports say text-only output was not always reliable (older models) |
| C. `gpt-live-1` + client delegation (OpenJarvis Agent as the backend) | OpenAI voice, full duplex | OpenAI's newest route for "a text workflow behind voice" | Pipecat 1.8.1 has no GPT-Live service (a custom integration is needed). Languages are not listed. Results are paraphrased, not exact. $0.05 per minute plus the backend |
| D. Chained (keep STT/TTS), `VoiceResponder` in front | VieNeu | not realtime | verbatim reading = today plus one extra hop. Rephrasing ≈ +0.8–1.2 s on about 68% of turns (estimate, see `chat-supervisor-pilot.md`) |

Latency: realtime only speeds up the **front** (filler and basic answers). The business result still waits for the Agent: tool-first turns p50 ≈ 3.0 s to first speech, and Luna has a TTFT floor of 1.1–1.4 s (measured 2026-10-13). Today's text Responder gives the filler as text at p50 ≈ 0.8 s before TTS.

## 4. Correct design, whichever option is chosen

1. **The front has exactly one tool, `delegate`.** No cart, checkout, payment or print tools. This is enforced by the tool list, not the prompt.
2. **The supervisor is the existing OpenJarvis Agent** with full history, the intent contract, the ledger and `turn_nonce`. The front's `relevantContext` is only a hint and never replaces the transcript.
3. **The result contract** comes from the existing final JSON (`_intent`, `state` ∈ ANSWER/NEEDS_INPUT/FAILED, `message`) and the tool `customer_message`. Only a verified result reaches the front. QR/pending is never paid.
4. **Verbatim or paraphrase is a correctness choice.**
   - Verbatim: prices, quantities and payment state stay exactly as the Agent wrote them.
   - Paraphrase (GPT-Live, guide): the app controls the facts, not the wording, and a runtime check would need string matching, which the user has ruled out.
5. **One speaker.** Only the front speaks. The Agent never pushes text to TTS.
6. **Stale or interrupted work.** Each delegation is tied to a turn id and revision. After barge-in or a correction, the old result is not spoken. An action that already ran stays in the ledger and is not repeated.
7. **Language.** The front holds the VI/EN rules (follow the explicit request, otherwise the latest substantive utterance, not accent). When verbatim, the Agent's message must already be in that language.

## 5. Evaluation before any pilot

Run offline first, without touching the running stack. **Do not upload customer recordings** (STT rule). Use synthetic speech or newly consented recordings.

| Measure | Pass condition |
| --- | --- |
| Vietnamese understanding: `gpt-realtime-2.1`/mini vs Gemini STT | not worse on the same scripted set |
| Delegation decision (business → delegate; greeting/repeat → handle alone) | aligned with the current Responder SKIP/SAY/ANSWER set |
| Filler implies no result | 0 slips |
| Language lock VI/EN | correct language on mixed turns |
| First audio (A/C) or first text → VieNeu (B) | p50/p95, same Agent model and effort |

## Decisions for the user

1. Keep the VieNeu voice (→ B), or accept an OpenAI voice (→ A/C)?
2. Verbatim (correctness) or paraphrase (natural speech)?
