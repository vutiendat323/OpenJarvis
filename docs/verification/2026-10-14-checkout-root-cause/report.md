# Checkout failures reported on 2026-10-14

The two quoted replies have different causes. A pending earlier order is
additional context, not a general rule prohibiting a new order.

Diagnosis only: no production changes, new merchant orders/payments, paid model
calls, commits or stack restart. SQLite was opened with `mode=ro` and
`PRAGMA query_only=ON`. Evidence below is from the actual turns, not a new
live checkout.

## "Không thể tạo đơn mới; đơn hiện tại vẫn chờ thanh toán."

- Time: 2026-10-14 21:13:37–21:13:50, Asia/Ho_Chi_Minh.
- Trace: `996932d81fc44f6bab75117d2bec3ac4`; Agent `gpt-5.6-luna`.
- User requested placing the new at-table order after choosing table 5.
- Checkout arguments contained **both** `cart_lines` (Pain suisse) and
  **`cart_revision: 0`**.
- Actual failure: **`provide cart lines or cart revision, not both`**.
  `DisplayCartTool.begin_checkout` rejects this combination before reserving
  the cart or making merchant writes (`src/openjarvis/tools/display.py:1060`).
  This trace contains a table GET and no order POST.
- `SkillExecutor` appends `shown_order` describing the earlier Pain suisse QR
  (`src/openjarvis/skills/executor.py:96`). The earlier order was pending and
  take-out. The generated reply juxtaposes that fact with the checkout failure,
  making pending appear to be the cause; the recorded failure says otherwise.

Recovery also failed: the early refusal returned no
`execution_started: false`. `IntentState` consequently kept
`target_attempted=True` and blocked the Agent's next checkout call with
`target already attempted; observe outcome, do not retry`. Backend log lines
19456–19469 record this sequence. `IntentState.observe` can release that flag
only when a failed target is explicitly marked not started
(`src/openjarvis/agents/intent.py:202`).

The checkout schema already declares mutually exclusive `oneOf` branches for
`cart_revision` and `cart_lines` (`src/openjarvis/skills/tool_adapter.py:209`).
However, `_validate_parameters` does not implement `oneOf`, so these arguments
reach the later cart guard rather than the early argument-error path that marks
execution not started. The manifest's runtime binding injects only the turn
nonce; there is no manifest default injecting revision zero. Persisted trace
messages are empty, so the raw provider argument event is unavailable; the
recorded execution arguments are the authoritative observation here.

## "Chưa xác nhận được đơn, bạn nhờ nhân viên kiểm tra nhé."

- Time: 2026-10-14 21:10:43–21:10:54, Asia/Ho_Chi_Minh.
- Trace: `11357ffc254843448d56d9bb88f65512`; Agent `deepseek-flash`.
- Cam tươi was added successfully. Checkout used revision 7, take-out, with
  `update_order_type=true`.
- `POST /api/latest/orders/public` returned **HTTP 201**, with order
  **`a5e2f6a194`**, total **45,000 VND**, type **take-out**, status **pending**.
  The verified bill was then published successfully.
- `POST /api/latest/payment/initiate/public` subsequently returned
  **HTTP 500**, `{"statusCode":500,"message":"Internal server error"}`.
  No payment QR was published by this checkout.

The failure occurs at payment initiation, after order creation succeeded.
The available evidence cannot identify the private merchant backend's cause of
HTTP 500 or establish the payment's final state. This is not evidence that the
order creation failed.

On failure, `SkillTool.execute` returns only the last step's error. If any write
was attempted, it adds `outcome_unknown=True` and the generic sentence that the
order write may have been sent (`src/openjarvis/skills/tool_adapter.py:320`).
It does not pass the successful `created` response or order ID to the Agent in
that failure result. Thus known order-creation success and unknown payment
outcome are collapsed into one unknown checkout outcome. This loss of partial
success explains the vague answer. A more accurate answer would distinguish
"order created" from "payment not confirmed" and retain the order ID for
read-only reconciliation.

## The subsequent Pain suisse replies

| Trace | Actual failure | Merchant write in this trace |
| --- | --- | --- |
| `9a184ce18d2f439aa99633493c2ae0ff` | `cart revision is stale or empty`, checkout requested revision 14 | None |
| `f78fc3f00ce149e8a3135b1fde8f5326` | Fresh menu GET timed out after 5 s; its one read retry also timed out | None |

Both failure results append the old pending Pain suisse `shown_order`, which
the Agent mentions instead of clearly explaining the current failure. These
are separate failures, not proof that all new orders require paying the old one.

## Local verification and repair direction

The replay script uses the current real checkout manifest, `SkillTool`,
`SkillExecutor` and draft-cart code, with recorded HTTP responses supplied by
an in-memory tool. It performs no network requests. Fresh local nonces/revisions
replace historical expired ones; it does not replay model speech generation.

Command from `OpenJarvis/`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python /tmp/jarvis-checkout-root-cause-2026-10-14/replay.py
```

Results:

- Conflicting cart inputs reproduce the exact refusal with **zero HTTP calls**.
- Control: omit `cart_revision` and the same at-table replacement draft is
  reserved even when a pending old order is reported as shown.
- Recorded order-201/payment-500 responses reproduce the composite unknown
  failure and the omission of successful created-order data.
- Existing related tests: **5 passed, 100 deselected** (`shown_order`,
  `unknown_outcome`, and `before_any_order_write` cases in the skill suites).

Recommended bounded fixes, not implemented in this diagnosis:

1. Validate exclusive checkout inputs before execution and mark pure validation
   refusals `execution_started=false`, so the Agent may repair arguments without
   weakening duplicate-write or nonce guards.
2. Return verified partial checkout facts and the failing stage: created order
   ID/status plus payment failure/uncertainty. Keep unknown writes protected;
   reconcile through reads instead of replaying order creation.
3. Treat old `shown_order` as separate context, never the reason for the current
   failure unless the actual guard/API error says so.
4. Investigate payment HTTP 500 on the merchant side using its request logs;
   the kiosk trace only establishes the failing endpoint and response.

Filtered evidence and the local replay are in
`/tmp/jarvis-checkout-root-cause-2026-10-14/`. They contain no QR images,
bank-account data, credentials or full customer history.

## Focused fix: conflicting checkout inputs

User authorized fixing the first failure only. Implemented schema `oneOf`
validation in `src/openjarvis/skills/tool_adapter.py`. The existing checkout
schema requires exactly one of `cart_revision` or `cart_lines`; both inputs,
including `cart_revision=0`, and neither input now take the existing early
`invalid_skill_arguments` path.

That path reports `steps=0` and `execution_started=false`, without appending
the old `shown_order`. `IntentState.observe` already handles that flag: it
allows the Agent to repair the arguments in the same turn. The refusal does
not claim the cart or consume the turn nonce. No change to the IntentState,
merchant write guard, checkout recipe, pending-order policy or payment-error
handling was needed.

Regression coverage in `tests/skills/test_checkout_argument_recovery.py`:

- Both/neither cart input rejected before a write; the same turn can repair
  to either replacement lines or the saved revision and submit exactly once.
- Old pending order context is not included in these argument refusals.
- A successful checkout still blocks a duplicate target.
- A timed-out POST remains an unknown outcome and still blocks retry.

Red before implementation: **4 failed, 1 passed**, all four failures missing
`execution_started=false`. Green after implementation: **5 passed**.

Broader verification:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/skills tests/agents/test_intent*.py tests/tools/test_display*.py tests/system/test_payment_trusted_hosts_wiring.py -q -p no:cacheprovider --tb=short -m 'not live and not cloud and not hub'
```

**645 passed, 5 deselected**, two dependency deprecation warnings. Ruff check
and format check on the two changed Python files passed; `git diff --check`
passed. The full CI suite was not rerun; its previously recorded unrelated
failures are not resolved by this focused change.

A read-only probe using the exact recorded arguments from trace
`996932d81fc44f6bab75117d2bec3ac4` and the shipped checkout manifest confirmed:
early refusal with `steps=0`, then same-turn correction accepted after removing
`cart_revision`. The real draft guard accepts that correction with the original
local nonce. No HTTP tool was registered in the probe, so no merchant requests
could be sent. The earlier `replay.py` characterizes the pre-fix failure and
is not the post-fix verification command.

No paid model eval, production orders/payments, commit or stack restart. Tests
verify the recovery path, not the model's spontaneous wording on a live turn.
