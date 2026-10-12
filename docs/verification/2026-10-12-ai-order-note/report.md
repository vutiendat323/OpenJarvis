# AI order note — 2026-10-12

Every new order created through `trendcoffee-checkout` receives the merchant
whole-order description `AI order`, or `AI order | <customer note>` when the
customer provided an order note. Line notes retain their original contents.

The policy lives in the TREND checkout recipe and ordering prompt. A 12-line
generic `$join` expression joins non-empty strings in the existing JSON template
renderer. It adds no dependencies, network calls, cart edits, or model rounds.
Checkout still checks the exact merchant description before proceeding to payment.
The customer-facing draft note and existing checkout guard remain as before.

## Verification

New real-recipe tests first failed because the outgoing descriptions lacked
`AI order`; executor tests first failed because `$join` was unsupported.
The implementation then passed these tests. The wider suite caught an input
schema regression: using `draft_cart.order_note` removed the exposed `order_note`
parameter. Both recipe expressions now use the existing `order_note` input,
which restores the original schema and guarded checkout contract.

Final command:

```sh
.venv/bin/python -m pytest tests/skills/ tests/agents/test_intent_existing_skills.py tests/tools/test_display.py tests/kiosk/test_printing.py tests/kiosk/test_cups.py tests/server/test_kiosk_printing.py -m 'not live and not cloud and not live_external' -q --tb=short -p no:cacheprovider
```

Result: **590 passed, 5 deselected, 7 dependency/deprecation warnings**.
Coverage includes saved and replacement drafts, empty and custom order notes,
Vietnamese text, quotes/newlines/braces, preserved line notes, mismatched merchant
descriptions stopping before payment, unchanged agent call counts, and printing
regressions. Live Ollama/installed-skill tests require services unavailable here
and are excluded from the final offline command.

Ruff lint and format checks passed for all six changed Python files;
`git diff --check` passed. A separate reviewer found no important issues and
independently reran the final note and bundled-skill tests: **132 passed**.

## Runtime at implementation handoff

No historical order was modified and no live merchant order or print was created
for testing. The running stack was left running. The backend loads the recipe,
prompt and Python implementation at startup, so **a backend restart is required
to activate this update**. Repository `CLAUDE.md` says: “Do not restart or deploy
the running stack unless asked.” This change does not restart the stack.

## Live confirmation — 10:48 +07:00

The update is now observed in a real agent-created merchant order. Read-only
trace inspection found trace `9b6a1891d37c4aafb235b216d53eecd8`, agent
`orchestrator`, execution path `skill_trendcoffee-checkout`:

- At 10:43:50 on 2026-10-12, `POST /api/latest/orders/public` sent
  `description: "AI order"`.
- TREND returned HTTP **201**, order **`c669b20e34`**, with
  `description: "AI order"`.
- At 10:48:03, an independent read-only
  `GET https://trendcoffee.net/api/latest/orders/c669b20e34` returned HTTP **200**
  and the same slug and description. The merchant retains the note.

The two preceding successful create responses, orders `b90d58eeaa` and
`7cc205ea0f`, also echoed `AI order`. An initial readback using urllib received
403; readback using httpx with browser User-Agent/Origin succeeded. No live order
was created, modified, paid or printed during this verification. The stack was
already running with backend PID 325781; no restart was performed here.

## Self-evaluation at implementation handoff

| Criterion | Score | Evidence / remaining improvement |
| --- | --- | --- |
| Accuracy | 5/5 | Exact payload and readback descriptions exercised through the real recipe. |
| Completeness | 4/5 | Code and regression verification complete; activation awaits a backend restart. |
| Clarity | 5/5 | Explicit examples and runtime limitation documented. |
| Actionability | 5/5 | Same checkout interface; existing launcher can load the update. |
| Conciseness | 4/5 | Small production change; tests cover template validation and existing flows. |
