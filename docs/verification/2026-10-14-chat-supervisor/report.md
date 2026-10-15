# Chat-Supervisor conformance: verification report

Date: 2026-10-14. The change follows `docs/superpowers/specs/2026-10-14-chat-supervisor-conformance-design.md`
and the plan `docs/superpowers/plans/2026-10-14-chat-supervisor-conformance.md`.
Nothing was committed and the stack was not restarted.

## Code and tests

- `uv run pytest tests/server/test_voice_responder.py tests/server/test_voice_llm.py tests/server/test_voice_background.py tests/server/test_print_completion.py tests/agents/test_progress_speech.py -q` → 90 passed.
- `uv run ruff check` on the touched files → clean.
- The CI-style suite (`uv run pytest tests/ -n auto -q -m "not live and not cloud and not hub"`) → 9,326 passed, 6 failed, 1 error. All of these are outside voice:
  - CUDA device selection in `learning`;
  - the `pearl` converter;
  - the CLI numpy import;
  - `kiosk/test_browser_routes`;
  - `polars` missing for evals.

  `import openjarvis.cli` loads no `server.voice` module.

## Offline eval

Command: `uv run --env-file .env python scripts/benchmark_voice_responder.py --runs 3`.
Model: `deepseek-flash`, thinking off. 34 synthetic VI/EN utterances × 3 runs = 102 calls per run.

| Run | Prompt state | Business without a tool call | Delegation without a filler | Wrong language | Flagged fillers/answers | Tool call p50 / p90 / max |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | as in Task 3 | 0 | 0 | 3 ("Hello there" answered in VI) | 7 (quantity, size, table repeated) | 0.94 / 1.17 / 1.27 s |
| 2 | + language rule for greetings, + detail rule | 0 | 0 | 0 | 2 (the word "size" only) | 0.90 / 1.20 / 1.49 s |
| 3 | + answers spoken directly, + no "confirmed"/"being made" | 0 | 0 | 2 | 7 (2 quantity/size repeats, 3 narrated answers, 2 size mentions) | 0.94 / 1.12 / 1.30 s |

- **Routing (the correctness gate) held in every run:** 0 of 225 business calls missed the tool, and every delegation had a filler.
- **Human review found wording problems the automated flags do not catch.** They appear in every run, and their counts vary between runs at temperature 0.3:
  - **English fillers that imply the drink is being made or the choice confirmed:** "I'll get that iced latte started for you.", "Preparing your take-away order now.", "Confirming the payment method."
  - **The same in Vietnamese:** "Mình đang chuẩn bị món cà phê sữa mang đi nhé."
  - **Answers that narrate the routing (run 2):** "That's a greeting, so I'll answer directly. Hello! …".
  - **An answer that restated a question as a fact (run 3):** "You can pay by QR code. Would you like to pay that way?".
  - **A filler that repeated the quantity and size (2 of 3 calls in run 3):** "Mình đang thêm hai ly bạc xỉu size lớn nhé."
  - **A Vietnamese confirmation answered with an English filler:** "Checking your payment method now."

**Gate result: FAIL** on wording (F3, L1). Routing passes.

These fixture tests and the offline eval do not prove live behaviour. Live
`voice_preamble decision_seconds` and outcomes need a restarted stack.

## Final review (fresh reviewer, read-only)

- **Verdict:** with fixes. It found no path that speaks twice or silently drops a request, apart from M1.
- **Fixed (test first):**
  - **M1:** a Responder task that ended cancelled escaped as `CancelledError` and stopped the voice service's frame task. It now routes to the Agent.
  - **M4:** the eval's language check counted "unknown" as correct. Vietnamese diacritics now count as Vietnamese.
  - After the fixes, the voice suites give 91 passed.
- **Caveats for the operator:**
  - A Gemini Responder would block the event loop, because `_stream_full_google` iterates a synchronous stream.
  - A Responses-API Responder reports tool calls only at stream end.
  - Live Responder calls likely open a new connection each turn (httpx keep-alive is 5 s), so live `decision_seconds` may exceed the eval's. Check it against the 2.0 s deadline after a restart.

## Temperature 0 (user decision) and the final prompt

| Run | Prompt state | Business without a tool call | Delegation without a filler | Wrong language | Flagged | Tool call p50 / p90 / max |
| --- | --- | --- | --- | --- | --- | --- |
| 4 | run-3 prompt, temperature 0 | 0 | 0 | 0 | 7 | 0.84 / 1.09 / 1.40 s |
| 5 | fillers limited to looking verbs; language line reworded | 0 | 0 | 0 | 1 | 0.99 / 1.25 / 2.06 s |

- **Run 4.** At temperature 0 the wording problems became deterministic, 3 out of 3 calls each:
  - "Preparing your take-away order.", "I'll get that iced latte started for you.", "Confirming payment method for the order.", "Mình đang chuẩn bị món cà phê sữa mang đi nhé." and "Mình đang thêm hai ly bạc xỉu size lớn nhé."
  - The cause was the prompt itself, which offered "preparing" as an example. In 1 of 3 calls the language instruction was also quoted back.
- **Run 5, human review.**
  - **Fillers:** all fillers use checking, finding, opening or looking up. None repeats a quantity, size or table number. None implies preparing, confirming or a result. The one flag ("phần chọn size cho trà đào") names the topic of the assistant's size question without the value, which is acceptable.
  - **Answers:** they are correct, with one exception (3 of 3 calls). "Sorry, what did you say?" got "You asked if you'd like to pay by QR code. Would you like me to say it again?" That misattributes the question and offers to repeat instead of repeating. It is harmless, since no action follows, but it is wrong wording. The spec gate does not cover answer wording.
- **Latency.** One of 75 delegations took 2.06 s, past `RESPONDER_DECISION_SECONDS = 2.0`. Live, such a turn times out and the Agent runs without a filler; no request is dropped.

**Gate result (spec §6): PASS.** Routing, filler presence and language pass automatically, and the flagged fillers were reviewed. One answer-wording issue is reported above.

## Repeat and explain requests delegate to the Agent (user decision)

Runs 5–7 showed the Responder answering "what did you say?" wrongly in English
("You asked if I'd like to pay by QR code?", 3 of 3 calls) while a verbatim-repeat
prompt written in run 6 did not fix it (run 7: 8 incorrect repeats). Decision:
the Agent has the whole conversation, so repeat and explain requests are
delegated. The Responder prompts no longer let it answer them, and the eval
expects them to delegate (the exact-repeat gate was removed).

| Run | Prompt state | Business without a tool call | Delegation without a filler | Wrong language | Flagged | Allow-list delegated anyway | Tool call p50 / p90 / max |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 7 | run-6 repeat rules | 0 | 0 | 0 | 4 | 6 | 1.00 / 1.15 / 1.92 s |
| 8 | repeat and explain delegated | 0 | 0 | 3 ("Large please" → VI filler) | 5 | 0 | 1.18 / 1.70 / 2.21 s |
| 9 | + language rule for short replies, + no numbers | 0 | 0 | 0 | 3 | 3 | 1.09 / 1.49 / 1.60 s |

- **Run 9, repeat and explain requests:** 12 of 12 delegated, each with a filler that only says it is
  looking at or repeating what was just said (e.g. "Mình kiểm tra lại câu hỏi vừa rồi nhé.",
  "Let me look up what was just said."). Nothing misattributes the question. "Let me repeat
  that for you." (2 of 12) promises a repeat but states no result.
- **Flags:** all 3 are the word "size" naming the topic of the assistant's size question, without the value.
- **Known wording issue (3 of 3):** "Alo alo" is delegated with the odd filler "Mình đang kiểm tra bạn nhé." The
  routing is safe (the Agent answers), but the filler is poor. This is a regression from run 5, where the
  Responder answered it. It is reported, not gated.
- **Latency:** the run-8 max of 2.21 s exceeded the 2.0 s deadline once; run 9 stayed under it.

**Gate result (spec §6), run 9: PASS.** Offline eval only; live behaviour needs a restarted stack.

## Run 6: repeat-answer fix (superseded)

The run-6 prompt required the Responder to repeat the assistant's last words
verbatim. It was not evaluated (DNS failure in the sandbox) and run 7 showed it
did not hold, so it was removed in favour of delegation.

## Hear-check follow-up (2026-10-14)

User requested fixing the "Alo alo" delegation regression. Changed only the
hear-check rule in the preset prompt and `DEFAULT_PROMPT`: standalone hear
checks should directly confirm hearing in the user's language, even after an
assistant question. Mixed hear-check/business requests still delegate. No
runtime text matching was added.

- Before the change, a fresh three-call probe reproduced the bug once out of
  three calls; the assertion that all hear checks answer failed. See
  `hear-check-before.json`. Temperature zero did not make every call identical.
- Run 10 (first wording, 108 calls): automated gate PASS, "Alo alo" answered
  3/3, repeat requests delegated 12/12, three flags mentioning "size" only.
  Tool-call p50 / p90 / max: 0.91 / 1.09 / 1.94 s. See
  `hear-check-eval-run-10.txt` and `hear-check-prompt-run-10.md`.
- Additional boundary probes found a mixed English/Vietnamese reply to "Alo"
  with the first wording. The existing language heuristic counted it as
  correct. Tightened the same sentence to require a brief confirmation in the
  user's language. Second probe: both prompts routed all six cases correctly
  (12/12 total), with correct language on manual review. See
  `hear-check-after.json`, `hear-check-after-final.json`, and
  `hear-check-prompt-probe-2.md` (the probe file retains its original
  `after-final` filename).
- Expanded the benchmark from 36 to 40 cases: "Alo", "Nghe không?", and two
  hear-check-plus-order requests in VI/EN. The spec's allow-list metric still
  reports unnecessary delegation without failing the gate; inspect these
  cases explicitly when checking this regression.
- Run 11 (120 calls): all routes correct, but one "Alo alo" answer was in
  English and one filler repeated a table number; gate FAIL. Mixed English
  hear-check/order turns also added a hearing acknowledgement before their
  filler. Tightened only the hear-check rule again: explicitly identify
  "alo/alo alo/nghe không" as Vietnamese, and for mixed requests speak only
  the business filler. See `hear-check-eval-run-11.txt` and
  `hear-check-prompt-run-11.md`.
- **Run 12 (final wording, 120 calls): automated gate PASS.** Zero business
  requests dropped, missing fillers, wrong-language replies or unnecessary
  allow-list delegations. All five pure hear-check cases answered directly
  (15/15); "Alo alo" answered in Vietnamese 3/3: "Mình nghe rõ bạn nhé, bạn
  muốn dùng gì hôm nay ạ?" Mixed hear-check/order cases delegated 6/6; repeats
  delegated 12/12. One flag mentions the topic "size" without its value and
  is acceptable. Tool-call p50 / p90 / max: **0.92 / 1.09 / 1.37 s**. See
  `hear-check-eval-run-12.txt`. Explicit assertions on the final hear-check
  and mixed-request rows also passed (in addition to the aggregate gate).
- Final `DEFAULT_PROMPT` probe: all six routes and languages correct, including
  four standalone hear checks and two mixed requests. See
  `hear-check-default-final.json`.
- **Remaining wording limitation:** the mixed English request "Can you hear
  me? One latte please." still gets "I can hear you. Let me look up the latte
  for you." before delegation (3/3 in run 12; also seen with the default
  prompt). It does not claim a business result or drop the order, but violates
  the one-sentence filler instruction and is not caught by the automated
  gate. Standalone hear checks also often resume the previous question.
  This follow-up fixes standalone hear-check routing; it does not establish
  perfect compliance with every wording instruction.
- Fresh voice suites plus eval tests: **97 passed**, six dependency warnings.
  Ruff check and format check on the changed Python files passed.

No commit or stack restart. Verification uses synthetic text and provider
inference, not live microphone/TTS. The full CI suite was not rerun for this
prompt change; its earlier failures remain recorded above.
