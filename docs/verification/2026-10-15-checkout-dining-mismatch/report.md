# "Chưa tạo được đơn; vui lòng thử lại." — trace diagnosis

The initial diagnosis below used SQLite with `mode=ro` and
`PRAGMA query_only=ON`. The subsequently authorized production code repair is
recorded at the end. No merchant/model API calls, restart, commit or push.

## Exact matching turn

- Trace: `a6558ba683b6412d8def0ae6ef83405f`.
- Started: **2026-10-15 18:23:55.821, Asia/Ho_Chi_Minh**.
- Model recorded in trace: `gpt-5.6-luna`.
- Customer's already quoted request: one coconut drink, take-out. Logs confirm STT
  recognized both the earlier "mang đi" and the full request correctly.
- Final trace result exactly matches the quoted failure sentence. Backend log
  `.stack-logs/backend.log.1:17481` records it being sent to TTS at 18:24:07.050.

## Failure chain

1. The add-to-cart skill succeeded: menu GET **200**, then local draft revision
   **1** with one line. Its saved `order_type` and `table` were both empty strings.
2. Checkout supplied revision **1**, `order_type="take-out"`, empty table/note,
   but **omitted `update_order_type`**. Executor defaults that flag to false.
3. `tools/display.py:1136` rejected the differing draft/order types with
   **`checkout order type does not match the draft`**. This guard runs before
   claiming the turn nonce or dispatching any checkout HTTP steps.
4. `skills/executor.py:191` wraps this pre-execution refusal without
   `execution_started=false`. The SkillTool result consequently lacks that flag.
5. `IntentState.observe` only releases `target_attempted` for an unsuccessful
   result explicitly marked `execution_started=false` (`agents/intent.py:202`).
   The Agent attempted checkout again, but dispatch refused it with
   **`target already attempted; observe outcome, do not retry`**. The actual
   refusal is recorded at `.stack-logs/backend.log.1:17475`.
6. The Agent returned the generic failure sentence. This trace has **no order
   POST and no payment POST**; this failure is local, before merchant execution.

The current checkout skill description and ordering prompt already instruct
`update_order_type=true` after adding unknown item names. This turn omitted it
despite those instructions. The recovery bug makes that argument mistake terminal.

## Offline reproduction and control

From `OpenJarvis/`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python /tmp/jarvis-checkout-dining-replay-2026-10-15.py
```

The replay uses real shipped checkout/cart/IntentState code and the existing
`OrderingFixture` with `FakeMerchant`; socket connections are blocked. Its item
is a synthetic fixture, not the customer's product or payment data.

| Case | Result | Real network |
| --- | --- | --- |
| Empty draft type, take-out checkout, flag omitted | Exact mismatch; corrected retry blocked; 0 fixture HTTP calls | None |
| Same draft, flag true initially | Checkout succeeds; 2 fixture GETs and 2 fixture POSTs | None |

Both cases completed with their assertions passing. This establishes the guard
and recovery mechanisms, not live model compliance with the prompt.

Later successful traces `ad74c51a3d1a4af1932f7241c53bf31f` (18:43:17) and
`e103590979b14d2eae590660aa849012` (18:44:17) both supplied
`update_order_type=true`. Another successful route uses replacement cart lines.
That explains why some orders succeed while this saved-draft call fails.

## Repair direction

Classify verified pre-execution draft refusals as `execution_started=false` so
the Agent can repair the dining parameters in the same turn. Preserve the
duplicate-write protection for requests that may have reached the merchant.
Keep the saved-draft dining contract explicit; do not silently assume a dining
choice or disable the mismatch guard. These were recommendations at diagnosis;
the authorized code repair follows below.

## Self-evaluation

Accuracy 4/5: exact trace/log correlation plus offline replay; no live replay.
Completeness 4/5: initial refusal and blocked recovery explained; other historical
checkout errors have separate causes. Clarity 5/5: separates the argument error
from recovery failure. Actionability 4/5: reproducible script and source anchors;
production repair remains. Conciseness 4/5: retains evidence without full payloads.
Overall **4.2/5**. Next improvement: a regression for same-turn dining-argument
repair before implementing the bounded fix. These limits match diagnosis scope.

## Authorized production code repair

`src/openjarvis/skills/executor.py` now adds `execution_started=false` to the
ValueError refusal around `begin_checkout`, before `_run_steps` is entered.
SkillTool already propagates this flag; IntentState can therefore permit corrected
arguments. No automatic dining choice, merchant-specific branch, spoken text,
write retry, prompt or model change was added. Existing nonce/revision claims and
the unknown-write-outcome path remain in force.

Two added cases in `tests/skills/test_checkout_argument_recovery.py` exercise the
real shipped checkout manifest with the local fake merchant. A blank dining type
and a stale revision both refuse with zero HTTP calls and an unchanged draft;
the same turn can then repair the arguments and complete exactly one order and
one payment. A duplicate completion is refused. The existing ambiguous POST
timeout test still blocks retries.

- Red before production edit: **2 failed, 5 passed**, both new failures missing
  the non-started flag.
- Green focused recovery suite: **7 passed**.
- Bounded skill suite: **498 passed, 5 deselected**; existing SWIG deprecation
  warnings only.
- Separate intent/display/trusted-hosts suites: **196 passed**.
- Ruff check, Ruff format check and `git diff --check`: passed.
- Independent read-only review: no findings; nonce, revision and unknown-outcome
  protections remain effective. Reviewer did not run live merchant calls.

The offline diagnostic script above characterizes the original bug and asserts
that recovery is blocked; it is not a post-fix verification script. The regression
tests are the post-fix verification. No paid model compliance test or live
checkout was run, and the live backend has not been restarted by this repair.

Post-repair self-evaluation: accuracy 4/5 (deterministic tests, no live replay),
completeness 4/5 (repair and duplicate/unknown-outcome protections covered; live
acceptance remains), clarity 5/5 (failure stages separated), actionability 4/5
(code and regressions ready; backend reload remains), conciseness 5/5 (one
production metadata line). Overall **4.4/5**; next verification is a requested
live acceptance with the updated backend loaded, without creating real orders
merely to test error classification.
