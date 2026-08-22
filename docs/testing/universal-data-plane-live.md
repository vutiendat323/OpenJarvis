# Universal Data Plane — live acceptance runbook

Manual acceptance against the real provider. Everything here is opt-in: nothing
in CI runs it, and no step below is authorized to mutate a merchant until an
operator has explicitly configured that exact operation.

Preset: `configs/openjarvis/examples/ordering-kiosk.toml`
Harness: `scripts/e2e_universal_data_plane.py`

The harness reads its numbers from the events the system already publishes on
the `EventBus` (`source.discovery.*`, `source.sync.*`, `source.execute.*`,
`source.verify.*`) and from the persisted capabilities and receipts. There is no
separate metrics service, and there is no browser stage — discovery is
HTTP-first only.

## 1. Read-only cold run

```bash
POSTHOG_DISABLED=true uv run python scripts/e2e_universal_data_plane.py \
  --source https://trendcoffee.net/
```

Expect:

- `cold discovery: trend-coffee rev 1`, with one line per discovery stage and
  its elapsed time;
- cold latency inside the 60-second discovery budget;
- `browser actions 0`;
- `schema demotions 0`;
- a `snapshot branch` and a `snapshot menu_item` line, each with a version, a
  record count, `stale=False` and a `synced_at` from this run.

Evidence to keep: the capability's `source_id`, `revision` and `fingerprint`
(`jarvis` reads it from the store, or read `source_capabilities` in
`~/.openjarvis/structured.db`), plus the two snapshot lines.

## 2. Restart, warm run

The harness already does this: it closes the cold system and rebuilds from the
persisted database before the warm pass. Confirm on the summary that

- `capability cache hit  True` — the warm pass answered from the database, not
  by rediscovering;
- warm latency is in the 5–30s band (usually far under it);
- `browser actions 0`.

If `capability cache hit` is `False`, the capability expired or its fingerprint
moved; investigate before going further. Never continue to a mutation on a
capability that just changed.

## 3. Operator inspects the fingerprint and adds temporary exact trust

Read the capability's `fingerprint` and its operation contracts. Every unsafe
operation is `quarantined` until an operator trusts that exact identity.

Copy the preset to a temporary file and add **only** the one entry you intend
to authorize, in `provider:operation:fingerprint` form:

```toml
[data_plane]
trusted_write_operations = "trendcoffee:order.place:sha256:<fingerprint>"
```

No wildcards; the parser rejects them, and any whitespace, duplicate or
malformed entry. Restart the system against the temporary config. Re-run step 1
and confirm the operation now reads `write_validated` and nothing else changed.

## 4. Customer request

Through the kiosk (voice or text), say:

> đặt cho tôi cà phê đen, 2 ly mang đi

The Agent must establish the branch, find the variant from the snapshot, and
ask only for what is genuinely missing.

## 5. Exact cart and order approval

The Agent adds to the cart in one turn and reads it back in the next. Before
any mutation reaches the provider, an approval appears in the queue
(`GET /v1/approvals/pending`). Read the **bounded argument preview** on that
action — order type, branch, each variant, quantity and note — and check it
against what the customer asked for.

Approve exactly that action:

```bash
curl -X POST http://127.0.0.1:8000/v1/approvals/<action-id>/approve
```

The approval is single-use and bound to the exact request hash. If any argument
changes, the grant is refused and a fresh approval is required.

## 6. One order POST, then a separate GET verification

Confirm from the receipt that exactly one `POST /orders/public` was issued, and
that `order_verify` / `source_verify` ran in a **separate** turn against a safe
`GET`. An ambiguous result must be reported as ambiguous — never retried.

The summary's `ambiguous mutations` counter must be `0`.

## 7. Explicit bank-transfer approval, one payment, verified QR

Payment is a separate authorization from placing the order. It requires its own
temporary trust entry (`trendcoffee:payment.initiate:sha256:<fingerprint>`) and
its own approved action id.

Only after the order snapshot exists may `payment.initiate` be proposed. Approve
it explicitly, let it run once, then verify in the next turn.

Confirm:

- the unverified receipt carries **no** QR data;
- the QR only appears in the `payment` snapshot after `source_verify` reports
  `observed: true`;
- the QR shown to the customer is byte-identical to the merchant's response.
  The Agent never authors a QR payload.

To drive the mutation from the harness instead of the Agent:

```bash
POSTHOG_DISABLED=true uv run python scripts/e2e_universal_data_plane.py \
  --allow-order --approval-id <action-id>
```

`--allow-order` refuses — before touching the provider — unless the action id is
an approved `source_execute` **and** the config carries an exact trusted write
fingerprint for that operation. It prints the exact request preview, runs the
mutation once, and never retries it.

## 8. Screen share

Open `/kiosk`, click **Share Screen**, and pick the browser window Jarvis is
driving.

- Confirm the customer-facing view shows that live window.
- Confirm the in-page **Stop** control returns the kiosk to its idle display.
- Share again, then use the browser's own "Stop sharing" control, and confirm
  the kiosk returns to idle that way too.

## 9. Remove the temporary write trust

Delete the temporary config (or empty `trusted_write_operations`) and restart.
Re-run step 1 and confirm every unsafe operation is back to `quarantined`. A
standing write trust must never survive an authorized test.
