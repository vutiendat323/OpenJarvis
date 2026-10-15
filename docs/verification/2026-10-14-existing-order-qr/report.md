# Existing-order QR recovery: root cause

Date: 2026-10-14. Diagnosis only; no production edits, merchant requests,
paid inference, commits or stack restart.

## Actual failed turn

Trace `288cd086596e48a9846d992a01c53c3b`, 23:33:30 Asia/Ho_Chi_Minh,
model `gpt-6-luna`:

- Request: "Vậy cho mình thanh toán đơn cà phê sữa trước."
- Reply: "Mình chưa thể mở lại QR đơn cà phê sữa; vui lòng nhờ nhân viên hỗ trợ."
- Trace contains one `generate` and one `respond` step, **zero tool calls**.
- Metadata: `normalized_intent=CHECKOUT_NOW`, `execution_path=[]`,
  `intent_satisfied=false`, `terminal_reason=FAILED`.

This is an Agent-generated refusal without an attempted API/display operation.
It is not a payment HTTP error, a checkout argument error, or evidence that the
merchant rejected this old order.

## Both earlier QR displays succeeded

| Order | Order ID | Payment ID | Amount | Historical result |
| --- | --- | --- | --- | --- |
| Cà phê sữa | `f9fbc6701b` | `a1b3b7ac58` | 40,000 VND | Order POST 201, payment POST 200/pending, QR published |
| Choco latte | `2fa8e2e008` | `f19abf1be1` | 50,000 VND | Order POST 201, payment POST 200/pending, QR published |

Source traces: `92a78dac5fd74e718174eb84f273b101` and
`9c81e9653ebd41639d997f890430b7ea`. These are the recorded statuses when QR
was initialized, not fresh merchant status queries.

The intermediate order-list answer, trace
`9a38144ede3a4404928b0f219c95d1d9`, also made zero tool calls. Its claim that
both orders are pending is based on conversation history, not a live read.

## Capability gap

The active preset exposes menu/cart/clear plus four prepared merchant skills:

```text
display_menu
display_cart
display_clear
skill_trendcoffee-menu
skill_trendcoffee-tables
skill_trendcoffee-add-to-cart
skill_trendcoffee-checkout
```

`configs/openjarvis/examples/ordering-kiosk-mcp.toml:83` hides `http_request`,
`display_bill`, and `display_payment_qr` from the model. They remain usable
inside prepared procedures. That visibility boundary is intentional; the
missing piece is an exposed prepared procedure for an existing order.

- Checkout takes a draft revision or replacement lines; its schema has no
  existing `order_id` input. It creates a new order and initializes payment.
- Cart actions modify/show a draft; they do not select an earlier order.
- No active skill reads a selected existing order and republishes its QR.
- A legacy, inactive instruction skill describes GET-order recovery, but the
  active prepared tool surface does not provide that operation.

The customer's request is to display/payment-select an already created order.
The available checkout operation creates a new one. Calling it would risk
creating a duplicate rather than paying the original coffee order. The
recorded Agent instead ends with FAILED. Raw model reasoning/tool schemas are
not retained in this trace, so the model's internal reasoning is not claimed;
the missing operation is verified in current preset wiring and schemas.

## Single selected payment state

`PresentationSession` stores one `payment` and one `last_payload`
(`src/openjarvis/kiosk/presentation.py:65`). In `publish`:

- A `bill` event clears `session.payment`.
- A `payment_qr` event assigns the new payload to `session.payment`.
- `shown_payment()` returns only that selected payment for its owning
  conversation, omitting QR bytes (`presentation.py:208`).

Thus Choco latte's bill/QR replaces the coffee QR as the selected screen state.
The current display API is not an order-indexed archive from which the Agent
can select the coffee receipt. This does not delete the merchant order or
historical traces: the coffee order and successful QR response still exist in
local trace history.

## Local verification

A deterministic probe used real `PresentationSessionManager.publish` and
`shown_payment`, with a fake Playwright boundary and synthetic QR strings:
coffee QR -> chocolate bill -> chocolate QR. It asserted that the chocolate
bill clears the selected coffee payment, and that the next QR becomes the
only selected payment and replay payload. No browser/network calls were made.

The same probe built the visible tool list with real
`SystemBuilder._model_visible_tools`, active skill manifests and tool schemas.
It confirmed the seven visible tools above, hidden QR primitive, and absence
of an existing-order ID parameter in checkout. This validates state and wiring;
it does not replay paid model generation.

## Repair direction

Provide a bounded existing-order operation: select a verified order belonging
to this customer/session, read its current merchant order/payment state, then
display that order's verified existing QR when still payable. If the order is
paid, expired, canceled or missing a usable QR, report that actual state.

Retain customer-owned order references independently of the currently selected
screen receipt so multiple pending orders can be selected safely. Do not use
checkout to recreate the coffee order, initialize another payment blindly,
or redisplay a stale QR based only on chat text. This diagnosis does not prove
that the merchant's current GET response will provide a still-valid QR; that
must be verified during implementation.
