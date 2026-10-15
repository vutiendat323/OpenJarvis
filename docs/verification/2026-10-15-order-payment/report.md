# Identical new orders and existing-order payment

Implemented in the working tree; no commit or stack restart. No live order or
payment was created during verification. Existing unrelated changes remain.

## Confirmed root causes

Trace `85e55a60027f4eb4aba955e745a9853f` (2026-10-14 23:36 local) corresponds
to the customer's explicit request for another identical order. Checkout
arguments were valid: revision 3 and take-out, with no conflicting cart lines.
Menu/table reads succeeded, but the order POST was refused locally before the
network: the conversation had already sent the same URL/body. HTTP deduplication
covered the whole conversation rather than one checkout. The failure wrapper
then mentioned the earlier pending order, causing the misleading spoken refusal.

Trace `288cd086596e48a9846d992a01c53c3b` corresponds to payment of an older
coffee order. The Agent emitted its refusal with zero tools. The enabled preset
had checkout and one currently shown payment reference, but no operation to
select and freshly read an earlier owned order. Two earlier creation traces
confirm successful order/payment responses and QR display. Read-only merchant
GETs for those known order IDs returned pending orders with payment/QR data;
no QR contents or customer/account data were retained in this report.

## Changes

- HTTP mutation deduplication now includes the runtime's reserved checkout
  revision. Two fresh confirmed drafts can dispatch identical payloads. Repeating
  a POST within one checkout, including an ambiguous timeout, remains blocked.
  Generic HTTP callers without this scope retain conversation-wide deduplication.
- The presentation session retains up to 20 QR-free created-order references,
  filtered by Voice conversation. They survive menu navigation and later orders;
  reset removes them. Agent context exposes `created_orders`.
- New prepared procedure `skill_trendcoffee-order-payment` selects an exact owned
  ID before any network call, GETs its current state, verifies response ID/amount
  and final HTTP origin, and displays its verified existing QR or paid bill.
  It never POSTs an order or starts a payment. `_intent=NONE`; `display=false`
  reads status without selecting a different order's screen.
- Reopening an earlier QR preserves the current draft. Expiry uses the original
  merchant creation time and the recipe's 900-second window, matching the kiosk.
- A verified canceled/expired/missing QR clears that order's selected payment;
  its visible QR becomes a QR-free receipt. Another order's screen is preserved.
  Status-only reads also invalidate an unusable QR if that same order is on screen.
- Per-order read tokens are reserved before GET. Response application and display
  happen atomically under the presentation lock. A newer read or receipt publication
  invalidates older responses, including a paid publication from browser polling.
  Polling publishes under the owner stored with the QR, even outside Voice scope.
- Preset registration, hidden internal display tool, active procedure and ordering
  prompt are wired. Explicit identical new purchases use a fresh checkout;
  existing-order payment uses the new read procedure.

## Verification

- Checkout regression initially failed because the second identical confirmed POST
  was blocked; green after scope injection. Covers both sequential execution and
  real WorkflowEngine context propagation through independent GETs then POST.
- Existing-order tests initially failed on missing ownership history and primitive.
  Configured Agent wiring initially failed on unknown procedure. Redirected
  merchant-origin test initially displayed an untrusted paid receipt. All were
  corrected against the observed failures.
- Independent reviewer identified two P1 regressions: retaining an invalid QR for
  the same displayed order and late pending reads restoring QR after paid state.
  Ten added cases failed before correction. Re-review identified the browser-poll
  variant without ambient owner; its added test failed before owner propagation.
  Final focused re-review confirmed the original race is rejected with
  `order_read_superseded`, the screen and cached order remain paid, and all
  30 existing-order tests pass. No unresolved findings in that review.
- Focused final check: **223 passed, 1 skipped**, covering existing-order payment,
  checkout mutation scope, presentation/routes, display and checkout procedure.
- Initial broader check: **1695 passed, 14 skipped, 14 deselected** across skills,
  tools, kiosk, system and relevant Agent suites. Broader repeat after the first
  review fixes: **1706 passed, 14 skipped, 14 deselected**, exit 0. This repeat
  was launched before the final browser-poll owner fix; the affected paths and
  added poll regression were rerun afterward in the final 223-test check above.
- Ruff formatting/checks passed for touched Python files; TOML parses and
  `git diff --check` passed.

## Limits

Transport fixtures exercise real cart, HTTP guards, procedure executor, display
tools and presentation state. The configured Agent integration uses scripted
FakeEngine responses; no paid model evaluation was run, so live spontaneous
routing and end-to-end speech remain unverified. No live checkout was submitted.

Order references are session memory, not rehydrated after backend restart. Only
orders created/retained by that Voice conversation can be selected, bounded to
20 references. The changes require loading the updated backend/config; the
currently running stack has not been restarted.
