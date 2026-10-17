# Barge-in stops speech, not tools: verification

Date: 2026-10-17. Branch `feat/live-voice-mode`, commits `faf126e5..d37b8a45`
on top of `65d429ec`. Spec and plan:
`docs/superpowers/specs/2026-10-17-barge-in-tools-run-to-completion-design.md`
and `docs/superpowers/plans/2026-10-17-barge-in-tools-run-to-completion.md`.
Both are local only, because `docs/superpowers/` is gitignored. The research
behind the design is `openai-barge-in-research.md` in this directory.

## What changed

- **Committed dispatch.** The orchestrator starts a round's completed tool
  calls before it hands speech and `AgentToolStarted` to the voice layer. The
  calls run inside `committed_dispatch()`. There, the cancellation checks do
  not drop them, a queued write keeps its place, and the turn's checkout nonce
  stays valid and claimable once.
  - Code: `agents/_stubs.py`, `agents/orchestrator.py`, `tools/_stubs.py`,
    `core/conversation.py`.
- **Checkout.** A checkout runs all its steps through a barge-in, QR included.
  The end of the voice session revokes the committed turns, so a running
  checkout stops at its next step (`server/voice/routes.py`).
- **Display calls.** A Playwright MCP call made inside a dispatched tool is
  not cancelled by a barge-in (`mcp/client.py`).
- **Ledger.**
  - Entries of an interrupted run are flagged `interrupted`.
  - A skill refused before it started is no longer recorded as a failed write
    (`agents/action_ledger.py`).
- **Reporting.** When nobody is talking, a successful late result of an
  interrupted request is phrased by the Responder's fast model through
  `run_phrased_report`, using the preset's `report_prompt_file`. It is not
  read out verbatim. A timeout (3 s), an empty line or a missing Responder
  falls back to the Agent's event turn.
  - Code: `server/voice/responder.py`, `server/voice/llm.py`,
    `server/voice/background.py`, `server/voice/pipeline.py`.
- **Merchant facts.** A created order cannot be cancelled at the kiosk, an
  unpaid order expires by itself, and a running checkout cannot be stopped.
  - Files: `configs/openjarvis/prompts/ordering-kiosk.md` and the new
    `voice-reporter.md`.

## Tests

Bounded suites, each in its own process:

| Suite | Result |
|---|---|
| `tests/server/test_conversation_scope.py` | 22 passed |
| `tests/agents/test_write_queue.py` | 31 passed |
| `tests/mcp/test_client.py` | 26 passed |
| `tests/agents/test_action_ledger.py` | 19 passed |
| `tests/agents/test_orchestrator_background.py` | 13 passed |
| `tests/agents/test_orchestrator.py` | 71 passed |
| `tests/agents/test_agent_runtime.py` | 25 passed |
| `tests/agents/test_background_scenarios.py` | 4 passed |
| `tests/skills/` (CI markers `not live and not cloud and not hub`) | 511 passed, 5 deselected |
| `tests/tools/test_display.py` | 94 passed |
| `tests/server/test_voice_responder.py` | 53 passed |
| `tests/server/test_voice_background.py` | 31 passed |
| `tests/server/test_voice_llm.py` | 26 passed |
| `tests/server/test_voice_lifecycle.py` | 29 passed |
| `tests/server/test_voice_carry_over.py` | 9 passed |
| `tests/server/test_voice_routes.py` | 9 passed |
| `tests/server/test_voice_turn_authority.py` | 48 passed |
| `tests/kiosk/test_presentation.py` | 43 passed |
| `tests/server/test_voice_report_latency.py` (live) | skipped: not run (costs API calls; needs approval) |

Without the CI markers, `tests/skills/` also runs 4 `@pytest.mark.live` tests
in `test_integration_live.py`. They fail because no inference engine is
running.

`tests/core/test_preset_configs.py` has 2 failures:
`test_kiosk_mcp_preset_limits_agent_tool_surface` and
`test_kiosk_mcp_active_skills_are_prepared_composites`. They are
**pre-existing**: they fail the same way at `65d429ec` in a clean worktree.
This change does not touch the preset's tool surface.

### Tests whose expectation changed

Two tests asserted the old behaviour that spec §5.2/§5.4 replaces. Their
expectations were changed:

- `tests/agents/test_orchestrator_background.py`:
  `test_a_write_cancelled_before_dispatch_leaves_no_running_entry` is now
  `test_every_completed_call_of_a_cut_round_runs`.
- `tests/agents/test_background_scenarios.py`:
  `test_a_write_queued_by_a_turn_cut_again_is_dropped_at_once` is now
  `test_a_write_queued_by_a_turn_cut_again_still_runs_after_the_gate`.

## Not verified here

- **Live phrasing latency** against the spec §5.6 target (first audio within
  1.5 s at p50, so the line within 1.3 s).
  - Run with the kiosk keys in the environment:
    `uv run pytest tests/server/test_voice_report_latency.py -m live -q -s`.
- **The running stack.** It has not loaded this code. It needs a restart that
  the user requests.
- **Live check after that restart** (spec §8.10): order something, then say
  "Hủy đơn" while checkout runs. Expect:
  - `~/.openjarvis/traces.db` shows `display_payment_qr` ran for that run;
  - `backend.log` shows `background_action_finished ... status=succeeded`
    and `background_action_reported via=phrased` (or `via=model` when the
    customer's next turn folded it in);
  - the kiosk says the order exists, the QR is on screen and an unpaid order
    expires. It never says the order was cancelled, stopped or not created.
