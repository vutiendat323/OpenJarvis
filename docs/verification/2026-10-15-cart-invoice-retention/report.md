# CART retains the latest invoice

User expectation: browsing the menu and reopening CART must retain the latest
invoice/payment QR. A requested new cart or items for the next purchase replace
that invoice with a new draft.

## Root cause

- `CustomerDisplayPage` cleared `cartSnapshot` on `payment_qr`, and the snapshot
  accepted only draft carts. Selecting CART therefore fell back to an empty cart.
- Checkout correctly settles the submitted draft, but `display_cart(action="view")`
  subsequently created an empty draft instead of restoring the submitted invoice.

## Change

- Retain cart/bill/QR as the CART tab's current presentation across menu navigation.
- Server retains the same presentation for voice reopening and reconnect replay.
  Reopening an invoice does not run checkout or recreate the order/payment.
- New draft edits replace that presentation, including silent touch additions.
  Returning to the menu alone leaves it intact.
- Paid/invalidated receipts and print completion update the matching cached invoice
  without navigating away from the menu or overwriting a newer draft.
- Keep the original merchant creation time (ISO or JavaScript GMT format). If it is
  absent/unusable, assign one first-seen fallback and retain it when reopening.
- Session reset clears retained data; menu replay carries explicit null when empty,
  covering a client that missed the reset while disconnected. Voice restoration
  checks the conversation owner and existing publication generation.
- Prompt and agent-visible tool description distinguish viewing an existing invoice
  from explicitly starting a new draft. No merchant IDs or spoken replies hardcoded.

## Verification

Regression tests were added before each fix and observed failing for the intended
reason. Initial reproduction: five frontend failures and three backend failures.
Additional regressions cover paid-poll/new-draft interleaving, old print completion,
invalidated cached QR, missed reset, silent touch/reconnect, expiry, and first-checkout
JavaScript timestamps. Independent read-only review found and verified these edge
cases; final review reported no remaining concrete findings in scope.

Final results:

- Backend: **539 passed** across kiosk presentation/routes/printing/session tests,
  display tools, existing-order payment, checkout recovery/scoping, auto-table and
  bundled skills. Existing dependency deprecation warnings only.
- Frontend: **154 passed** across six related suites, including ten new interactive
  navigation regressions.
- `npx tsc -b`: pass. Ruff check/format for changed Python files: pass.
  `git diff --check`: pass.

No stack restart, deployment, commit, paid model calls, or real merchant writes.
The running stack has not been verified with this patch loaded.

## Self-evaluation

| Criterion | Score | Evidence / practical limit |
|---|---:|---|
| Accuracy | 4/5 | Real reducers, display tools and manager tested; live stack not restarted. |
| Completeness | 4/5 | Touch/voice, status races, reconnect, ownership and deadline covered; live replay remains. |
| Clarity | 5/5 | Separates retained invoice presentation from editable draft and checkout. |
| Actionability | 5/5 | Implemented patch and reproducible regression coverage. |
| Conciseness | 4/5 | Production changes confined to presentation, display tool, frontend and prompt. |
