# Parallel Agent runs: scenario replay and verification (2026-10-18)

Plan: `docs/superpowers/plans/2026-10-18-barge-in-parallel-agent-runs.md`. Spec: `docs/superpowers/specs/2026-10-17-barge-in-parallel-agent-runs-design.md`.
Branch `feat/live-voice-mode`, commits `758e8df9`..`92c58906` plus the Task 12 commit.

Status: nothing is pushed and nothing is deployed. The stack was not started or restarted. What the running stack has loaded is not established by this report.

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
| `test_two_fenced_draining_runs_release_their_slots_for_a_third` | extra replay from review: with the real two-slot runtime, a third request waits while two runs hold the slots, then proceeds after both are fenced and drain. Drain, not cancel, is proved by each request's model round completing after its hold (a cancelled run never reaches it), all three completing, the first two ledger run entries being `succeeded` and unreported, and both slots free at the end |

Sensitivity check (before fix round 1): with the rig's runtime set to one slot (temporary edit, reverted), 6 of the 8 tests fail, so they depend on the two-slot runtime.

## Suite counts (each in its own process, `-m "not live and not cloud and not hub"`)

| Suite | Result |
|---|---|
| tests/core/test_runs.py | 13 passed |
| tests/agents/test_agent_runtime.py | 29 passed |
| tests/core/test_config.py | 65 passed |
| tests/agents/test_write_queue.py | 39 passed |
| tests/agents/test_background_scenarios.py | 4 passed |
| tests/skills/test_checkout_stable_cart.py | 11 passed (10 plus the Ruling 15 test, added in fix round 1) |
| tests/skills/test_checkout_barge_in.py | 6 passed |
| tests/skills/test_checkout_mutation_scope.py | 4 passed |
| tests/skills/test_tool_adapter_v2.py | 27 passed |
| tests/tools/test_await_requests.py | 4 passed |
| tests/agents/test_action_ledger.py | 31 passed |
| tests/agents/test_orchestrator_background.py | 19 passed |
| tests/agents/test_orchestrator.py | 71 passed |
| tests/server/test_voice_parallel_runs.py | 15 passed |
| tests/server/test_voice_llm.py | 26 passed |
| tests/server/test_voice_background.py | 42 passed |
| tests/server/test_voice_lifecycle.py | 29 passed |
| tests/server/test_voice_carry_over.py | 4 passed |
| tests/server/test_voice_responder.py | 58 passed |
| tests/server/test_voice_turn_authority.py | 48 passed |
| tests/server/test_voice_routes.py | 10 passed |
| tests/server/test_print_completion.py | 2 passed |
| tests/core/test_preset_configs.py | 19 passed, 2 failed (pre-existing) |
| tests/skills/ (whole directory) | 521 passed, 5 deselected |
| tests/server/test_voice_parallel_scenarios.py (new) | 8 passed (stable over 3 runs) |

The 2 failures are `test_kiosk_mcp_preset_limits_agent_tool_surface` (display_existing_order drift, Ruling 14) and `test_kiosk_mcp_active_skills_are_prepared_composites`; both fail on the tree before this plan.

Lint: `uv run ruff check` and `ruff format --check` are clean on the new test file, on the edited `test_voice_report_latency.py` and on all 30 Python files changed since `70149586`. `ruff check src/ tests/` reports 3 E501 errors in files this plan did not touch (`src/openjarvis/server/cloud_router.py:94`, `tests/server/test_speaker_audio.py:142`, `tests/server/test_speaker_vision.py:275`).

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

Deferred minors and carried-over items are listed in `.superpowers/sdd/2026-10-18-barge-in-parallel-agent-runs/progress.md`. The Task 4 priority item (two waiting checkouts with `update_order_type=True` and an `order_type`) is now covered by `test_two_waiting_checkouts_that_set_the_order_type_create_one_order` in `tests/skills/test_checkout_stable_cart.py` (Ruling 15): exactly one order POST with the full total, the other checkout refused with no order started, and the type saved.

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
