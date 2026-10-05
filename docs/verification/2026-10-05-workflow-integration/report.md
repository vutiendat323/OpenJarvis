# WorkflowEngine in the live kiosk — 2026-10-05

## Scope

Activate the existing `WorkflowEngine` in the kiosk runtime without redesigning
unrelated parts. `OrchestratorAgent` stays the only decision-maker; the engine
only schedules the deterministic steps of prepared skills. No UI, Vision, STT,
TTS, prompt or skill-recipe change.

## Before

- `serve.py` forced `.workflow(True)`, so the engine was built and never used.
- Every skill ran its steps strictly in manifest order (`SkillExecutor._run_steps`).
- Checkout: `GET menu → GET tables → POST order → display_bill → POST payment → display_payment_qr`.

## After

```
OrchestratorAgent ── one model decision ──► skill_trendcoffee-checkout (one tool call)
                                              │
                         SkillExecutor ── build_step_graph(manifest) ──► WorkflowEngine
                                              stage 1: GET menu ∥ GET tables
                                              stage 2: POST order
                                              stage 3: display_bill
                                              stage 4: POST payment
                                              stage 5: display_payment_qr  → completed_display + customer_message → TTS
```

- `[workflow].enabled` decides (default `false`, the kiosk preset sets `true`).
  `false` restores the previous strictly sequential steps.
- Direct tools never touch the engine. Skills with nothing to overlap (menu,
  add-to-cart, tables) keep the sequential loop: `build_step_graph` returns `None`.
- Only TOOL nodes are created, and the engine runs with `system=None`: no agent,
  loop, planner or verifier node can call a model.
- The terminal path is unchanged: `SkillTool` still returns `completed_display`
  and `customer_message`, so `_completed_display_batch` ends the turn without a
  second model round.

## Rules for sharing a stage (`skills/step_graph.py`)

- A plain read is an `http_request` whose template fixes `method` to GET/HEAD.
  It waits only for the steps whose output it references (template or
  assertions) and for the last barrier before it.
- Everything else (writes, `display_*`, sub-skills, a templated method, an
  unparsable template) is a barrier: it waits for every earlier step and every
  later step waits for it. Writes keep manifest order.
- A reused `output_key` keeps the sequential path.

## Safety kept

Each step still runs through the same `_run_step` (extracted verbatim from the
loop): turn-nonce check, recipe check, one GET/HEAD retry on transport errors,
assertions, `_checkout_write_attempted`, and `begin_checkout`/`end_checkout`
around the whole run. Results are returned in manifest order and cut after the
first failure. Parallel steps run on a locked copy of the context (assertions
copy it with `{**ctx}`) inside a copy of the caller's `contextvars` (turn nonce,
conversation, cancellation lease).

Behavior change: when one checkout read fails, its sibling read has already run,
so a stale menu now makes two GETs instead of one. Both are read-only; no write
runs.

## Files

- `workflow/engine.py`: optional `tool_runner` for TOOL nodes; parallel nodes run
  in `contextvars.copy_context()`.
- `skills/step_graph.py` (new): manifest → graph of TOOL nodes.
- `skills/executor.py`: loop body extracted to `_run_step`; `_run_step_graph` when
  an engine is set and the graph overlaps something.
- `skills/manager.py`: `set_workflow_engine`, passed to every `SkillExecutor`.
- `system/builder.py`: engine built before skills and handed to `SkillManager`.
- `cli/serve.py`: `.workflow(config.workflow.enabled)`.
- `configs/openjarvis/examples/ordering-kiosk-mcp.toml`: `[workflow] enabled = true`.

## Verification

- New tests: `tests/workflow/test_workflow.py` (tool_runner, context in parallel
  stage), `tests/skills/test_step_graph.py`, `tests/skills/test_skill_workflow.py`
  (overlap, sequential without engine, failed read stops before writes and
  releases the cart, engine skipped when nothing overlaps, real checkout recipe
  end-to-end with URL-routed merchant fake, stale menu writes nothing),
  `tests/system/test_workflow_wiring.py` (on/off), preset and serve tests.
- Full suite (`env -u FORCE_COLOR uv run pytest tests/ -n auto -m "not live and
  not cloud and not hub"`): 8787 passed; failures are the pre-existing baseline
  (2 pearl, device selection, kiosk prompt test, CLI numpy import, evals import
  error). `test_speaker_audio`/`test_browser_routes` failed once each under load
  and pass on rerun.
- Existing bundled checkout tests use an order-based HTTP fake
  (`_SequenceRecording`), so they cannot run with the engine forced on: parallel
  reads would receive each other's responses. They still run the sequential path.
- Not verified on the live kiosk (stack not restarted).

## Expected latency

Only checkout changes: tables (~0.26 s measured) now overlaps menu (~0.57 s),
about −0.26 s on a ~5 s checkout turn. Menu, cart and table reads, direct tools
and the number of model rounds are unchanged. Engine overhead is one thread pool
per parallel stage (sub-millisecond).
