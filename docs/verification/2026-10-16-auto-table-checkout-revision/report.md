# New multi-item at-table order fails after auto-table selection

The initial diagnosis used read-only SQLite, existing logs and an isolated replay.
The subsequently authorized production code fix is recorded at the end. No
model/merchant API calls, restart, commit or push were performed for this work.

## Exact trace

- Trace `7976dba7c2c045e8a7bb7abeaeb3179d`, started **2026-10-16 13:46:51.564**
  Asia/Ho_Chi_Minh; recorded model `gpt-5.6-luna`.
- Final result exactly matches: "Đơn mới chưa tạo; QR đơn trước vẫn đang chờ
  thanh toán." TTS logged that sentence at **13:47:04.732**, workspace
  `.stack-logs/backend.log:18692`.
- STT correctly recognized the quoted request for six items, at-table.

| Step | Observed result |
| --- | --- |
| `display_cart(action="add")` | Success; six lines; revision **9**; dining type initially empty |
| Auto-table merchant GET | HTTP **200** |
| Internal `display_cart(action="set_table")` | Success; type `at-table`; revision **10** |
| `skill_trendcoffee-auto-table` | Success; returned revision **10** |
| `skill_trendcoffee-checkout` | Supplied revision **11**, type `at-table`, `table_status="available"`; refused with **`cart revision is stale or empty`** |
| Final model round | No tool calls; ended with terminal reason **FAILED**, intent unsatisfied |

The new checkout never sent an order or payment POST. The preceding single-item
take-out order has a separate successful trace
`fd0b2591e589475e8a1e225454507ca0` at 13:46:22; it is not the failed new order.

## Cause and failed recovery

1. Checkout supplied a revision different from the latest verified auto-table
   result: **11 instead of 10**. `tools/display.py:1130` refuses a revision that
   does not identify the current nonempty draft. A revision is an opaque runtime
   value, not a number the model should predict or increment.
2. Runtime context is refreshed before each inference by
   `agents/orchestrator.py:_inject_runtime_context`; the cart and checkout
   providers expose `draft_cart.revision`. The checkout manifest binds only
   `turn_nonce` in `runtime_arguments`, leaving revision copying to the model.
3. The earlier production recovery fix is present in this failure:
   **`execution_started=false`** is recorded in both the tool step and run event.
   `IntentState.observe` can release the attempt and allow repaired arguments.
   This trace has no second checkout attempt or dispatch-blocked retry.
4. The next model round chose a failed final answer. `IntentState._accepts_state`
   permits **FAILED** even after a repairable pre-execution refusal; it does not
   require a correction attempt first. Allowing recovery does not ensure the
   model will use it.
5. `_with_shown_order` appends the earlier pending order to the actual error.
   The final reply mentions that QR but omits the revision failure. The earlier
   pending order is context, not the guard that refused the new checkout.

The trace stores no inference messages, so the exact prompt seen by the model
cannot be recovered. Revision 10 is the last verified returned value; the
recorded checkout uses 11. The inspected log interval shows no intervening cart
edit request, but the trace does not capture a snapshot at the instant of refusal.
These limits prevent claiming access to the model's internal reason for choosing
11 or excluding every possible unrecorded state change.

## Offline replay

Run from `OpenJarvis/`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python /tmp/jarvis-auto-table-revision-replay-2026-10-16.py
```

Uses the real auto-table/checkout manifests, cart, SkillTool and IntentState,
with the existing synthetic OrderingFixture/FakeMerchant. Socket connections are
blocked. Synthetic revisions are **2 -> supplied 3**, equivalent to the recorded
**10 -> supplied 11**; a synthetic earlier pending-order reference is retained.

- Wrong revision: exact refusal; **zero fixture POSTs**; execution not started;
  retry remains permitted; a FAILED final is also accepted by the policy.
- Control with the verified revision: checkout succeeds with one fixture order
  POST and one fixture payment POST, despite the previous pending reference.
- Both replay assertions passed. No paid inference or actual checkout was run.

## Bounded repair direction

Remove the need for the model to predict revisions: carry the verified snapshot
revision through the prepared checkout contract, preserving stale-snapshot and
concurrent-edit checks. For a refusal explicitly proven not started, make the
correction path explicit before a generic FAILED ending. Do not relax duplicate
write/unknown-outcome guards or automatically approve a changed cart. Report the
current failure separately from any previous pending order. These were the repair
directions at diagnosis; the authorized revision fix follows below.

## Self-evaluation

Accuracy 4/5: exact trace/log match and deterministic replay; inference messages
and guard-time snapshot are absent. Completeness 4/5: argument, recovery and
misleading previous-QR context covered; live inference choice cannot be replayed.
Clarity 5/5: separates this failure from the previously fixed retry lockout.
Actionability 4/5: source seams and offline check provided; repair remains.
Conciseness 4/5: only selected facts retained, without payment payloads.
Overall **4.2/5**. Next improvement: a regression for revision propagation after
auto-table and a bounded pre-execution correction, followed by a narrow fix.

## Authorized production code fix

The checkout manifest now declares
`runtime_arguments_if_present.cart_revision = "draft_cart.revision"`.
`SkillTool.spec` forwards this metadata. `IntentState.prepare` resolves selected
conditional arguments from the existing inference-time runtime context, using the
same path validation and value copying as mandatory runtime bindings.

The conditional binding preserves the current schema and its exclusive choice:

- Selecting `cart_revision` uses the verified snapshot revision even if the model
  supplies another number. This snapshot is captured before inference; it is not
  refreshed when the checkout worker executes.
- Selecting replacement `cart_lines` does not inject a revision or require an
  existing draft. Supplying both fields is still invalid.
- Missing verified state blocks dispatch. A cart edited after inference still
  fails the unchanged stale-revision guard before any HTTP.
- Merchant-specific policy stays in the manifest. No business decisions, spoken
  text, automatic retries or final-state policy changes were added to core.
  Direct SkillTool callers retain their supplied revision and existing guards.

## Verification of the fix

- Initial regression run before implementation: **5 failed, 50 passed**. Failures
  showed the wrong auto-table revision reaching checkout, selected arguments not
  being bound, and missing verified state not blocking dispatch.
- Auto-table integration exercises both synchronous and semantic streaming
  inference with correct and incorrect model revisions. All four cases complete
  in two inference rounds, with exactly one fixture order and payment each.
- Snapshot-race regression edits the draft after capturing runtime context and
  before preparing checkout. It still refuses with zero HTTP calls and preserves
  the touch edit. Additional cases cover replacement lines, conflicting fields
  and blocking retries after an ambiguous POST timeout.
- Skills: **505 passed, 5 deselected**; separate intent/display/trusted-hosts:
  **199 passed**. Existing SWIG deprecation warnings only.
- Ruff check and format check on the six touched Python files, plus
  `git diff --check`: passed.
- Independent read-only review of production changes and regressions: no
  findings; reviewer did not run live API calls.

A read-only preparation probe used the recorded checkout arguments from this
trace and revision 10 from its successful auto-table metadata. The effective
checkout argument became **10 instead of 11**, all other business arguments were
unchanged, and **zero HTTP calls** were made. This probe does not reconstruct
the unavailable historical inference messages or execute a new order.

The earlier diagnostic replay intentionally asserts the old failure, so it is
not a post-fix verification script. The regression suites above verify the fix.
No live backend reload or paid model evaluation was run. Source and manifest
changes need to be loaded before testing the actual microphone flow.

Post-fix self-evaluation: accuracy 4/5 (recorded-argument probe and deterministic
tests, no live inference), completeness 4/5 (snapshot and write protections covered;
live acceptance remains), clarity 5/5 (selected snapshot versus dispatch-time cart
distinguished), actionability 4/5 (fix and regressions ready; reload remains),
conciseness 4/5 (reuses existing runtime binding with one conditional variant).
Overall **4.2/5**. Next improvement: requested live acceptance with the updated
backend loaded, without creating real orders merely to test argument binding.
