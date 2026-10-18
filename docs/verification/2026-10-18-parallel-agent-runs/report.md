# Parallel Agent runs: scenario replay and verification (2026-10-18)

Plan: `docs/superpowers/plans/2026-10-18-barge-in-parallel-agent-runs.md`. Spec: `docs/superpowers/specs/2026-10-17-barge-in-parallel-agent-runs-design.md`.
Branch `feat/live-voice-mode`, commits `758e8df9`..`92c58906`, the Task 12 commits (`de77a031`, `b961b6e3`) and the final-review fix wave `e619a541`..`9c349743` (see "Final review fix wave" below).

Status: nothing is pushed and nothing is deployed. The stack was not started or restarted. What the running stack has loaded is not established by this report.

## Cart-lines correction fix and re-review (2026-10-19)

Baseline: `24f77240e1b8e05075caee2cdfc594d90bc6982d`, applied on
`feat/live-voice-mode`. This wave fixes the remaining Important finding from
the C2/N1 review below. The previous sections' counts remain historical.

Accepted cart edits now invalidate replacement lines belonging to other
active runs of that conversation. Touch edits invalidate all active runs;
own edits do not invalidate their author or erase an earlier conflict.
`begin_checkout` checks that conflict under the same cart lock before
consuming the nonce, replacing the draft, or starting any recipe step. The
conflict lasts until the run ends, including across retries. This covers
edits while the model is thinking, during the stability wait, between STABLE
and freeze, and between freeze and cart reservation. A later request may
still intentionally replace an older draft.

Successful no-op edits also count: clearing an empty cart can cancel an
older run's intended replacement, and reaffirming quantity 1 must not permit
that run to replace it with 2. Ordinary views and rejected edits do not
invalidate the run. Settlement and cache eviction also invalidate affected
active runs when they remove a draft without issuing a new revision.
Revision-based checkout retains its existing exact-revision/follow rules;
replacement lines conservatively refuse any accepted peer edit since the
run began, including edits by an earlier background run.

### TDD evidence

The probes from `/tmp/openjarvis-c2-n1-review/test_remaining_checkout_paths.py`
are now acceptance tests in `tests/skills/test_checkout_stable_cart.py`.
All checkout HTTP is intercepted by the existing fixture.

| Regression | Observed RED | Final GREEN |
|---|---|---|
| Touch quantity correction during wait, populated or initially empty cart | 2 stale successful checkouts | Refused; corrected snapshot unchanged; zero HTTP |
| Voice removal between STABLE and freeze; touch edit immediately before atomic claim | 2 stale successful checkouts | Refused before recipe execution; corrected snapshot unchanged |
| Touch correction before the checkout call, while its run is already active | 1 stale successful checkout | Refused on first call and retry in the same run |
| Settlement or eviction before the checkout call | 2 stale successful checkouts | Empty draft preserved; zero HTTP, including retry |
| Successful clear of an empty cart; successful no-op quantity correction | 2 stale successful checkouts | Both invalidate older replacement intent |
| Tracker lifecycle, conversation isolation, peer/touch authors, own edits | 3 failures before the new tracker API existed | Conflicts persist across own edits and author end; new runs start clean |

Positive controls also pass: replacing an unchanged empty/populated draft,
including the checkout run's own cart edits, and a distinct later purchase.
Existing overlapping-checkout tests continue to assert one merchant order
and one payment POST.

### Bounded verification

Each suite in this table ran in its own process, bounded by 120 s, using
`uv run --offline --no-sync pytest <suite> -q --tb=short -m "not live and not cloud and not hub"`
and `UV_CACHE_DIR=/tmp/openjarvis-uv-cache`.

| Suite | Result |
|---|---|
| `tests/core/test_runs.py` | 35 passed |
| `tests/skills/test_checkout_stable_cart.py` | 33 passed |
| `tests/skills/test_checkout_barge_in.py` | 6 passed |
| `tests/tools/test_await_requests.py` | 7 passed |
| `tests/server/test_voice_parallel_scenarios.py` | 11 passed, 2 dependency warnings; workaround below |
| `tests/agents/test_write_queue.py` | 39 passed |
| `tests/tools/test_display.py` | 98 passed, 4 context cleanup warnings |
| `tests/skills/test_checkout_runtime_revision.py` | 4 passed |
| `tests/skills/test_checkout_mutation_scope.py` | 4 passed |

Required suites: **131 passed**; additional affected suites: **106 passed**.
Ruff check and format check passed on all five changed Python files.
`git diff --check` passed. No live/cloud/hub tests, real merchant requests,
push, deployment, or stack restart were performed.

Voice used the same temporary `loop_wakeup_plugin` and command documented in
the C2/N1 section below, scheduling a 10 ms event-loop timer for this
sandbox. Its 11 tests passed in 8.00 s; no product method or assertion was
replaced. The display suite reported four `PytestUnraisableExceptionWarning`
warnings from `agent_turn_scope` context-token cleanup in the two existing
cross-run add-dedup tests. These are passing results with warnings, not a
claim of warning-free verification.

### Critical re-review result

Self-review and independent read-only review found and fixed the additional
pre-invocation, empty-clear, settlement, and eviction cases above. Final
review found no remaining substantive defect in this diff or remaining
second/stale-order path in the exercised tracked-run schedules. It checked
atomicity before nonce/merchant dispatch, retries, own versus peer edits,
empty drafts, run cleanup, existing epoch/claim protection, and lock order.
The new calls acquire the cart lock before the tracker condition; tracker
methods do not acquire the cart lock or invoke external callbacks.

The guarantee applies to active registered runs. Direct checkout calls
without a registered voice run retain their existing behavior. A run with
a recorded conflict cannot use replacement lines even after refreshing its
inference; the safe response asks for a new payment confirmation. These
limits do not establish the behavior of the currently running kiosk stack.

Commit delivery: `.git` is still mounted read-only. Staging the six scoped
files failed creating `.git/index.lock`; the original index and HEAD remain
unchanged. The authorized fallback is a conventional commit in a temporary
clone, exported as `fix-cart-lines.bundle` and `fix-cart-lines.patch` under
`/tmp/openjarvis-cart-lines-fix.BHtZM3/`, based on `24f77240`. Neither artifact
includes the kiosk TOML, `frontend/tsconfig.tsbuildinfo`, or unrelated files.

## Residual C2 / N1 fix and re-review (2026-10-19)

Baseline: `59e1b102`, branch `feat/live-voice-mode`. This section records the
that wave's bounded verification; the older counts below remain historical.

- **C2 residual fixed:** each run captures the checkout epoch in `begin()`,
  before model work or slot waiting. A peer's freeze release supersedes that
  run even when its checkout call starts after the release. `end()` removes
  the run's epoch. Own releases advance its baseline by exactly one, rather
  than resetting it to the current epoch, so they neither supersede their
  owner nor hide an earlier peer release. Re-entering `wait_stable` cannot
  reset this protection.
- **N1 fixed:** `waiting` now means currently inside `wait_stable` and is
  cleared in `finally`. Checkout snapshots remain in `waits` until `end()` so
  peer checkouts still ignore one another. `wait_others` skips only an active
  stability waiter and never a freeze owner; after a checkout leaves that
  wait, corrections wait for its run to end, including after thaw.

TDD evidence, with HTTP requests intercepted by the existing checkout fixture:

| Regression | RED before its fix | GREEN after |
|---|---|---|
| Core: peer release before the checkout wait, via thaw or owner end; repeated waits; own releases | 3 failed: peer release missed; own release incorrectly superseded itself | Passed |
| Checkout: B2 begins during B1's held order POST, then calls checkout after B1 returns; both `cart_lines` and revision; B1 ended or still running | 4 failed: `cart_lines` checkout succeeded again; revision reached the consumed claim instead of being superseded before dispatch | All four refuse B2 before dispatch; exactly one order POST and one payment POST |
| Core: correction after STABLE, after thaw, or after a timed-out stable wait | 3 failed: `wait_others` returned True before checkout ended | Passed |
| Await tool and real checkout fixture: correction while earlier checkout holds freeze / order POST | 1 failure in each suite: `earlier_requests_finished` while order still posting | Reports still running until checkout ends |

Each requested suite ran in a separate process using
`uv run --offline --no-sync pytest <suite> -q --tb=short -m "not live and not cloud and not hub"`
with `UV_CACHE_DIR=/tmp/openjarvis-uv-cache`:

| Suite | Current result |
|---|---|
| `tests/core/test_runs.py` | 32 passed |
| `tests/skills/test_checkout_stable_cart.py` | 21 passed |
| `tests/skills/test_checkout_barge_in.py` | 6 passed |
| `tests/tools/test_await_requests.py` | 7 passed |
| `tests/server/test_voice_parallel_scenarios.py` | 11 passed, 2 dependency warnings; runner workaround below |
| `tests/agents/test_write_queue.py` | 39 passed |

Total: **116 passed**. Ruff check and format check passed for all five changed
Python files; `git diff --check` passed. No live/cloud/hub tests, stack restart,
deployment, push, or real merchant calls were performed.

Voice runner qualification: the ordinary suite stalled in pipeline setup and
executor teardown. A first-scenario run using the exact HEAD tracker loaded
from `/tmp` also timed out at 45 s. Independently,
`asyncio.run(asyncio.to_thread(lambda: 42))` returned 42 but hung during
executor shutdown (10 s timeout). Scheduling a recurring 10 ms event-loop
timer let that minimal example close normally. The full voice suite then
passed in 7.87 s with a temporary pytest plugin that only schedules that
timer, without replacing product methods or assertions. The plugin and probe
are in `/tmp/openjarvis-c2-n1-review/`, outside the committed changes. Re-run:

```sh
PYTHONPATH=/tmp/openjarvis-c2-n1-review:.:src UV_CACHE_DIR=/tmp/openjarvis-uv-cache \
  timeout --signal=INT --kill-after=5s 120s uv run --offline --no-sync pytest \
  tests/server/test_voice_parallel_scenarios.py -p loop_wakeup_plugin \
  -q --tb=short -m "not live and not cloud and not hub"
```

The interrupted/timeout runs are not passing evidence. The two warnings in
the completed run concern Pipecat's deprecated `AudioContextTTSService` and
google-auth's future grpcio minimum version.

### Critical re-review result

Self-review of the diff plus a read-only independent reviewer found no
introduced defect in C2/N1. The review checked peer epoch changes before and
during waits, own-release exclusion without masking a peer, owner `end()`,
retrying a wait, revision claims, exclusive freeze, and the correction / peer
checkout waiting distinction. The tested overlapping-checkout schedules now
produce a single order; this is not a proof of all merchant-order paths.

**Remaining Important finding — `cart_lines` can overwrite a later correction
and create a stale merchant order (pre-existing, not fixed in this wave).**
`src/openjarvis/tools/_stubs.py:223` follows revisions only when the arguments
contain `cart_revision`; `src/openjarvis/tools/display.py:1257` replaces the
draft from `cart_lines` without a check for intervening draft changes. Two
offline characterization probes with real tracker/gate/cart/skill code and
mocked HTTP confirmed these schedules:

1. B supplies the visible coffee quantity 1 and waits behind a read-only A.
   A touch updates that line to quantity 2 (`total=70000`), then A ends.
   Touch does not call `note_write`, so B reaches STABLE, replaces the
   correction with quantity 1 and posts `total=35000` successfully.
2. B reaches STABLE with quantity 1. Before `freeze()` acquires the freeze,
   a new voice run clears the cart through the real `ToolExecutor` and ends.
   B then freezes, replaces the empty cart from its older `cart_lines` and
   posts `total=35000` successfully. The equivalent revision path is already
   refused by the existing stale-revision test.

Probe: `/tmp/openjarvis-c2-n1-review/test_remaining_checkout_paths.py`
(`test_cart_lines_overwrites_a_later_touch_correction`,
`test_cart_lines_reintroduces_a_voice_removal_before_freeze`). Both
characterizations assert the observed bad outcome and passed; they are not
regression acceptance tests. Both also reproduced with the exact HEAD tracker
loaded by the baseline plugin (2 passed, the same stale POSTs). Neither
requires a network call. The fix should
bind replacement lines to the draft/change history they observed and refuse
later corrections atomically before reserving checkout; an older draft being
intentionally replaced must remain distinguishable. The earlier background
add being replaced is not reported separately because the configured prompt
explicitly permits replacement of an older draft.

The requested C2/N1 fixes and regression tests are the scope of this commit;
the remaining finding is reported for the next fix wave. The kiosk TOML,
`frontend/tsconfig.tsbuildinfo`, and unrelated untracked files remain unstaged.

Commit delivery limitation: the sandbox mounts this checkout's `.git`
read-only. `git add` failed creating `.git/index.lock`; no index or branch
ref was changed. A conventional commit with the requested final co-author
line is prepared in a temporary clone instead, with bundle
`/tmp/openjarvis-c2-n1-review/c2-n1-fix.bundle` and mail patch
`/tmp/openjarvis-c2-n1-review/c2-n1-fix.patch`. The original checkout keeps
the six task files modified and uncommitted. The bundle contains only those
six files, with parent `59e1b102`.

## What was replayed

`tests/server/test_voice_parallel_scenarios.py` drives the real voice service over the real `NativeAgentRuntime(max_concurrent_runs=2)`, the real `OrchestratorAgent`, the real action ledger, run tracker, write gate and `DisplayCartTool` (and, for payment, the real checkout `SkillTool` with its stable-cart wait). Only the edges are scripted: a streaming engine, a fake Responder and a sink in place of TTS. Delays are held with events rather than timed, and no assertion bounds a duration. A few negative checks ("nothing happened yet") sleep 0.2-0.4 s before looking, so they can only pass too early, never fail on a slow machine.

The scenarios were written from the 18:14-18:16 session in `.stack-logs/backend.log` (lines 17721-18087 were still present and were read; they were not rotated). In that log "Alo" cut the juice run (18:15:00), so the juice was never added and the customer asked again; the smoothies run was cut by two "Alo" and a later "Bạn thêm chưa?" started a new run.

| Test | Result it pins |
|---|---|
| `test_juice_then_alo_adds_the_juice_without_asking_again` | "Alo" is answered by the Responder and never reaches the Agent; the cut run still adds the juice once; its outcome stays unreported for the reporter |
| `test_smoothies_then_alo_then_did_you_add_does_not_add_twice` | one add call in the whole session; the second run's prompt equals `inflight_prompt([smoothies], "Bạn thêm chưa?")`; the script re-adds if the block is missing, so the test fails without it |
| `test_smoothies_then_add_coffee_runs_in_parallel_and_keeps_both` | the second run's model starts while the first run's write is held; both runs in flight; writes serialize in order; cart has both |
| `test_smoothies_then_take_out_keeps_the_items_and_changes_the_type` | `set_order_type` queues behind the add and lands on top of the items |
| `test_smoothies_then_no_smoothies_waits_and_ends_without_them` | the second run calls `await_earlier_requests` (blocked while the add is held), then reads the real cart and removes; cart ends empty; writes are add then remove |
| `test_pay_while_smoothies_are_added_orders_them` | the checkout posts nothing while the add is held, then orders `total=70000` (coffee + smoothie) once; one payment POST; the finished checkout ends the run |
| `test_alo_is_answered_before_the_first_run_finishes` | the Responder's text frame precedes the first run's write (spec §6 shape); wall-clock targets are checked live |
| `test_an_add_asked_while_checkout_waits_drops_the_stale_checkout` | final review C1: smoothies run A (held), "pay" B waits for a stable cart, a new add C waits for a slot (both slots held); A ends first, C then adds. No order POST at all (B ends `cart_changed_during_checkout`), the cart holds coffee, smoothie and C's item. Before the fix B ordered `total=70000` without C's item |
| `test_no_smoothies_asked_while_checkout_waits_drops_the_stale_checkout` | final review C1/I4: same, with "Thôi mình không lấy sinh tố nữa": `await_earlier_requests` returns in well under 2 s (no mutual 15 s wait with the waiting checkout), the smoothie is removed, no order POST, cart is coffee only |
| `test_a_run_that_waited_for_a_slot_sees_the_named_request_was_told` | final review I5, one-slot runtime: "Bạn thêm chưa?" waits for the slot while the smoothies run ends and is told (a ledger listener stands in for the reporter); its `background_actions` list the smoothies run as `succeeded`, `already_told`, and it does not answer "still adding" |
| `test_two_fenced_draining_runs_release_their_slots_for_a_third` | extra replay from review: with the real two-slot runtime, a third request waits while two runs hold the slots, then proceeds after both are fenced and drain. Drain, not cancel, is proved by each request's model round completing after its hold (a cancelled run never reaches it), all three completing, the first two ledger run entries being `succeeded` and unreported, and both slots free at the end |

Sensitivity check (before fix round 1): with the rig's runtime set to one slot (temporary edit, reverted), 6 of the 8 tests fail, so they depend on the two-slot runtime. The two C1 replays were run against the pre-fix `runs.py`/`_stubs.py` (stashed): both fail with an order POST `total=70000`.

## Suite counts (each in its own process, `-m "not live and not cloud and not hub"`)

After the final-review fix wave, at `9c349743` (the Task 12 counts they replace are in this file's history):

| Suite | Result |
|---|---|
| tests/core/test_runs.py | 26 passed (stable over 3 runs) |
| tests/agents/test_agent_runtime.py | 29 passed |
| tests/agents/test_write_queue.py | 39 passed |
| tests/agents/test_background_scenarios.py | 4 passed |
| tests/skills/test_checkout_stable_cart.py | 16 passed (stable over 3 runs) |
| tests/skills/test_checkout_barge_in.py | 6 passed |
| tests/skills/test_checkout_mutation_scope.py | 4 passed |
| tests/skills/test_tool_adapter_v2.py | 27 passed |
| tests/tools/test_await_requests.py | 6 passed (stable over 3 runs) |
| tests/tools/test_display.py | 98 passed |
| tests/tools/test_display_payment_evidence.py | 18 passed |
| tests/tools/test_display_refusal_not_started.py | 2 passed |
| tests/agents/test_action_ledger.py | 36 passed |
| tests/agents/test_orchestrator_background.py | 19 passed |
| tests/agents/test_orchestrator.py | 71 passed |
| tests/server/test_voice_parallel_runs.py | 16 passed |
| tests/server/test_voice_llm.py | 26 passed |
| tests/server/test_voice_background.py | 44 passed |
| tests/server/test_voice_lifecycle.py | 29 passed |
| tests/server/test_voice_carry_over.py | 4 passed |
| tests/server/test_voice_responder.py | 58 passed |
| tests/server/test_voice_turn_authority.py | 48 passed |
| tests/server/test_voice_routes.py | 10 passed |
| tests/server/test_print_completion.py | 2 passed |
| tests/core/test_preset_configs.py | 19 passed, 2 failed (pre-existing) |
| tests/skills/ (whole directory) | 527 passed, 5 deselected |
| tests/server/test_voice_parallel_scenarios.py | 11 passed (stable over 3 runs) |

`tests/core/test_config.py` was not rerun in the fix wave (nothing it covers changed).

The 2 failures are `test_kiosk_mcp_preset_limits_agent_tool_surface` (display_existing_order drift, Ruling 14) and `test_kiosk_mcp_active_skills_are_prepared_composites`; both fail on the tree before this plan.

Lint: `uv run ruff check` and `ruff format --check` are clean on all 33 Python files changed since `70149586`. `ruff check src/ tests/` reports 3 E501 errors in files this plan did not touch (`src/openjarvis/server/cloud_router.py:94`, `tests/server/test_speaker_audio.py:142`, `tests/server/test_speaker_vision.py:275`).

## Live latency (spec §6, step 3)

Not run (needs user approval; it spends API calls). A second `-m live` case was added to `tests/server/test_voice_report_latency.py`, `test_live_whole_run_report_is_fast_and_never_claims_a_cancel`: it phrases a whole-run report (`run_report_text` of a run entry plus its write) for the ten existing conversations and asserts p50 <= 1.3 s and no cancellation claim. It stays marked `live` and was only collected, not run. Run when approved: `uv run pytest tests/server/test_voice_report_latency.py -m live -q -s`, keys loaded from `.env` without printing them.

## Tests flipped or removed since 70149586

From the Task 8 commit `36c6054c` (a barge-in no longer cancels the run, nothing is carried):
- `test_voice_carry_over.py`: removed `test_nothing_unfinished_leaves_the_prompt_alone`, `test_carried_prompt_labels_earlier_and_latest`, `test_carried_prompt_skips_text_already_merged`, `test_remember_keeps_the_three_newest_in_order`, `test_remember_drops_entries_the_latest_repeats_or_extends` (they tested `carried_prompt` and `remember_unfinished`, which no longer exist). Flipped: `test_interrupted_request_is_carried_and_a_finished_one_is_not` to `test_an_interrupted_request_keeps_running_and_is_not_carried`; `test_a_repeated_interrupted_request_is_carried_once` to `..._runs_again_and_nothing_is_carried`; `test_a_carried_request_still_sees_its_write_after_the_reporter_told_it` to `test_a_told_write_is_not_listed_again_while_its_run_keeps_working`. Task 9 (`69bab06e`) adjusted the last one to the `<requests_in_progress>` prompt shape.
- `test_voice_responder.py`: removed `test_a_turn_carrying_an_interrupted_request_is_never_answered` and `test_a_blocked_answer_still_lets_a_preamble_through` (the `answer_blocked` outcome is gone). Flipped: `test_a_turn_interrupted_before_the_delegation_lands_starts_nothing` to `..._runs_the_agent_silently`; `test_an_interruption_while_the_responder_decides_starts_nothing` to `..._speaks_nothing` (a delegated Agent now runs, unheard). The `_unfinished` asserts were dropped from two tests.
- `test_voice_llm.py`: `test_cancellation_during_frame_delivery_closes_binding` to `test_cancelling_a_run_during_frame_delivery_closes_binding` (the run task is cancelled, as at session end; `process_frame` no longer awaits it).
- `test_print_completion.py`: `_answering` is now a read-only property, so the test patches the property instead of assigning it.
- `test_preset_configs.py` (Task 11, Ruling 14): `await_earlier_requests` added to the enabled and visible sets of `test_kiosk_mcp_preset_limits_agent_tool_surface`.
- `test_orchestrator_background.py`: only imports were reshuffled.

## Controller rulings (from progress.md)

1. `RunTracker.freeze` returns a bool and is exclusive, so two stable checkouts serialize and the second is refused at the revision claim instead of both timing out.
2. (Superseded) Reply language: the user later asked for Vietnamese.
3. After a queued write gets the gate, re-check the freeze; if another run owns it, leave the gate and wait again.
4. A nested `in_tool` step skips the freeze wait and recheck (its outer write already passed).
5, 7. Checkout revision follow-through: the pre-wait `cart_revision` is rewritten only when every bump since was made by a run in the checkout's wait snapshot; touch edits, runs started later and unknown authors leave it stale and the checkout is refused.
6. A checkout's own write and nested steps are not noted as cart changes for another waiting checkout.
8, 10. Ledger folding: `mark_run_completed` and `mark_run_reported` skip entries still running; a run is reported "answered" only when it completed, was not cancelled and could speak; a cancelled run finishes as "stopped".
9. A speaking run that completes marks its own run entry as answered; a fenced run leaves all its entries for the reporter (amended by 10).
11. On the compatibility path (engine without semantic streaming, no committed dispatch) a background checkout fails closed with "checkout turn expired" once run 2 starts; the production models use committed dispatch.
12. A rejected-speaker turn begins its run with request "" and no `CUSTOMER_REQUEST`; in-progress lists skip empty requests.
13. The plan's quoted "I hear you" in `voice-responder.md` became policy wording ("say that you can hear them"): the user's no-fixed-customer-sentence rule wins.
14. See the flipped `test_preset_configs.py` set above.
15. Add the two-waiter test with the production argument shape (`update_order_type=True`, `order_type`), where B1's `begin_checkout` bumps the revision itself.
16. (Final review C1, I4; amends Task 1.) `wait_stable` waits for every in-flight run that is not itself waiting, runs begun during the wait included, so the checkout is STABLE only when no such run is in flight; a write by a run not in the original snapshot stays CHANGED. `wait_others` (`await_earlier_requests`) waits only for runs begun before the caller and not waiting. The prompt's "cannot be stopped" covers only a checkout that has started ordering. Rulings 5/7 stay consistent: the follow-through accepts only bumps by the runs the checkout waited for (`RunTracker.waited_for`, the snapshot at the wait's start). Cost: a checkout waits up to 15 s longer behind a read-only run.
17. (Final review C2.) A per-conversation checkout epoch in `RunTracker`; a checkout whose epoch moved between its wait's start and its freeze is refused with `dispatched: False` (`checkout_superseded`), for both the revision and the `cart_lines` paths. Built as: the epoch moves when a checkout's freeze ends (unfreeze, or the owner's `end()`), not when it is taken, and the check runs once the freeze is held. This refuses every case "bump on take" refuses and also a checkout that began waiting while another already held the freeze (bump on take would miss that one: the epoch was already moved when it was captured). Cost: a legitimate second checkout that waited behind a refused first one is refused too and must be asked again.

Deferred minors and carried-over items are listed in `.superpowers/sdd/2026-10-18-barge-in-parallel-agent-runs/progress.md`. The Task 4 priority item (two waiting checkouts with `update_order_type=True` and an `order_type`) is now covered by `test_two_waiting_checkouts_that_set_the_order_type_create_one_order` in `tests/skills/test_checkout_stable_cart.py` (Ruling 15): exactly one order POST with the full total, the other checkout refused with no order started, and the type saved.

## Final review fix wave (2026-10-18)

The whole-branch review found C1, C2, I1-I5 and minors; one fixer handled them, each with a test that failed first (RED against the code before the fix, by stashing the source change) and passes after.

| Finding | Fix (commit) | Tests |
|---|---|---|
| C1: a change asked during the checkout wait was ignored once the snapshot runs ended; the prompt said a waiting checkout "cannot be stopped" | Ruling 16 in `RunTracker.wait_stable`; prompt reworded (`e619a541`) | `test_runs.py` (`..._run_begun_during_the_wait`, `..._writes_after_the_snapshot_ended_is_changed`, ...), `test_checkout_stable_cart.py::test_a_change_still_thinking_when_the_snapshot_ends_drops_the_checkout`, the two voice replays above, `test_voice_responder.py` wording |
| C2: two waiting `cart_lines` checkouts created two orders | Ruling 17 epoch, `checkout_superseded`; prompt handling line (`64ad2191`) | `test_two_waiting_cart_lines_checkouts_create_one_order` (one order POST; before: two), `test_a_checkout_that_waits_while_another_is_ordering_is_refused`, `test_a_later_checkout_is_not_refused_by_an_earlier_finished_one`, epoch tests in `test_runs.py` |
| I1: a folded background run was reported again when it ended | the ledger records a run folded while its run entry still runs and marks that entry told when it finishes; `run_report_text` leaves out told tools (`72e9c883`) | `test_action_ledger.py` (folded run told whole; a later write still reported alone), `test_voice_background.py` through the reporter |
| I2: `seen_by_run` was overwritten by a parallel run | `LedgerEntry.seen_by` is a set (`e1bb0399`) | `test_an_entry_seen_by_two_parallel_runs_is_told_by_either`, `test_d4_does_not_hold_a_run_that_saw_the_failure_before_another_run_did` |
| I3: another run's write reset a turn's add dedup | claims keyed by (conversation, turn nonce), bounded at 256 (`af93d132`) | `test_display.py` (interleaved add, interleaved remove, own remove, bound) |
| I4: `await_earlier_requests` waited for later runs and waiting checkouts | Ruling 16 `wait_others` (`e619a541`) | `test_runs.py`, `test_await_requests.py` (two corrections in a row; correction beside a waiting checkout), the no-smoothies replay |
| I5: a run that waited for a slot answered "still in progress" after the customer was told | the tracker remembers the runs each run's prompt names; the ledger run entry records its voice run; `background_for` lists a named, told run as `already_told` (`15a61f6a`) | `test_runs.py`, `test_action_ledger.py`, the one-slot voice replay above |
| Minor 1: `close()` popped state that later calls recreated | bounded tombstone (1024 ids); late calls return CLOSED/False (`e619a541`) | `test_calls_after_close_find_it_closed_and_leave_no_state`, `test_closed_ids_are_remembered_up_to_a_bound` |
| Minor 2: no notify after `waiting.add` | notify on entering the wait (`e619a541`) | `test_a_waiting_checkout_wakes_a_peer_as_soon_as_it_waits` (also passed before: a peer's exit notified) |
| Minor 7: dead `carry`/`settle`, `carrying` branch, `_GatedBinding.prompt_of` | removed (`d4845323`) | their tests removed |
| Minor 8: unbounded `cleanup()` wait | 5 s bound and a warning (`9c349743`) | `test_cleanup_does_not_wait_forever_for_a_run_that_will_not_unwind` (before: hung until killed) |

Tests changed in the fix wave (not new ones):
- `test_checkout_stable_cart.py`: the three two-checkout revision tests (`test_two_waiting_checkouts_create_one_order`, `..._after_an_add_...`, `..._that_set_the_order_type_...`) now expect the second checkout to be refused as `checkout_superseded` (Ruling 17 runs before the revision claim that refused it as "already consumed"/"stale"); their one-order, full-total and posts checks are unchanged.
- `test_runs.py`: `test_a_new_run_that_only_reads_...` ends the reader (a reader still running now holds the wait); `test_wait_others_waits_only_for_runs_in_flight_at_the_call` became `..._begun_before_the_caller` (a later run is no longer waited for); in `test_close_wakes_every_waiter` the `wait_others` caller is the latest run.
- `test_voice_background.py`: two tests take the expected `run_report_text` before reporting (told tools are now left out).
- `test_action_ledger.py`, `test_orchestrator_background.py`: `seen_by_run` asserts became `seen_by` sets; the three carry/settle tests were removed.
- Runtime contract wording (`orchestrator.py`): `already_told` entries "are finished ... do not report them again unless the customer asks about them".

## Known limits of this replay

- The model is scripted: it shows how the runtime behaves given the replies a model should give (for example, not adding again when the in-progress block is present), not that the live model does.
- The reporter (`BackgroundReporter`) is not wired into the replays; they only show that a fenced run leaves its entries unreported.
- After a checkout the orchestrator ends the run, so the replay asserts on the ledger and the order POST rather than on spoken text.

## Live check after a restart the user requests (spec §10.12)

Not done: the stack was not restarted. For the user, after a restart they request, repeat the 18:14-18:16 session and read `.stack-logs/backend.log`:

1. Juice + "Alo": the juice is added without asking again.
2. Smoothies + "Alo" + "Bạn thêm chưa?": no second add; the answer is "in progress" or the result.
3. Smoothies + "thêm một cà phê sữa": both are added, the second run starts without waiting; its write follows the first's.
4. Smoothies + "đổi sang mang đi": the smoothies are added and the order is take-out.

Compare with spec §6, from these lines:
- `voice_preamble ... outcome=... decision_seconds=`: "Alo" answered with first audio <= 1.0 s at p50 (the Responder alone).
- `runtime_acquired` `queue_wait_seconds` in traces (`traces.db`): a new request's run starts within 0.1 s of the Responder's decision, never queued behind the background run.
- `background_run_started` / `background_run_finished` (action ledger): run start times and that a fenced run still finishes.
- `voice_phrased_report outcome=... seconds=`: a late report has first audio <= 1.5 s after idle at p50.

Also check that no `background_action_started` appears twice for one request (no double add).

## Repository state

Committed in `OpenJarvis` on `feat/live-voice-mode`. `docs/verification/2026-10-18-parallel-agent-runs/report.md` is not gitignored, so no `git add -f` was needed. Not pushed. The unrelated dirty files (`configs/openjarvis/examples/ordering-kiosk-mcp.toml` `[kiosk]` hunk, `frontend/tsconfig.tsbuildinfo`, untracked docs) were left unstaged.
