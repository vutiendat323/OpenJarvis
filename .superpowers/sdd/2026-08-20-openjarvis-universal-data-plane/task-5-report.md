# Task 5 — Trend Coffee adapter report

## Scope

Added the registered `trendcoffee` structured-source adapter, minimal fixtures,
fixture tests, and an explicitly gated live-read test. The adapter compiles the
publisher origin independently from the API base URL, keeps `order.place` and
`payment.initiate` `QUARANTINED`, and performs no network I/O itself.

`build_request()` accepts only these provider-neutral request shapes:

- `order.place`: `{order_type: "take-out", branch_slug, items}` with only
  non-empty, positive `{quantity, variant_slug, note}` entries. It maps to the
  fixed Task 8 provider body.
- `payment.initiate`: `{order, paymentMethod: "bank-transfer"}`.

The page iterator continues only while the provider envelope says
`result.hasNext is true`; it never infers completion from returned item count.
The bundle helper re-discovers script paths from homepage HTML while its stored
evidence contains only a content hash and publisher origin, not an asset path.

## Fixture provenance

- `branch.json` and `products.json` are minimal redacted public GET captures
  dated 2026-08-20.
- `order.json` and `payment.json` are clearly marked reconstructed first-party
  bundle schema examples. They do not establish write evidence or promote
  either mutation from `QUARANTINED`.

## RED / GREEN

1. RED: `test_trendcoffee_adapter.py` failed collection because
   `TrendCoffeeAdapter` did not exist.
2. GREEN: normalization, contracts, provider-envelope pagination, registration,
   exact request allowlists, and bundle evidence tests passed.
3. RED: the `order.place` request test failed with the payment-only builder.
4. GREEN: the exact take-out mapping passed and rejects missing, extra, empty,
   and invalid order/payment fields.
5. RED: the bundle rediscovery test found the regex used a literal `\\b`.
6. GREEN: correcting the word boundaries discovered `/assets/app.js` while
   retaining only a SHA-256 content fingerprint.
7. RED: whitespace-only `order` and `note` values were accepted by the initial
   truthiness checks.
8. GREEN: exact request validation now requires non-blank strings.

## Verification

```
POSTHOG_DISABLED=true uv run pytest tests/data_plane -q
# 83 passed, 1 skipped

OPENJARVIS_LIVE_TREND_READ=1 POSTHOG_DISABLED=true \\
  uv run pytest tests/data_plane/test_trendcoffee_live.py -q -m live
# 1 passed

uv run ruff check src/openjarvis/data_plane/adapters/__init__.py \\
  src/openjarvis/data_plane/adapters/trendcoffee.py \\
  tests/data_plane/test_trendcoffee_adapter.py \\
  tests/data_plane/test_trendcoffee_live.py
uv run ruff format --check <same four files>
git diff --check
# all passed
```

The live check issued GETs only for the publisher homepage,
`/api/latest/branch`, and `/api/latest/products`. It compiled attributable
evidence, normalized two non-empty batches, and invoked no browser observer.
No live response body was committed.

## Fix round 1

- Corrected `StructuredSourceAdapter` to the binding contract:
  `compile(evidence)` and `normalize(resource_type, payload)`. The Trend Coffee
  implementation now accepts either one `DiscoveryEvidence` or a sequence;
  one item is wrapped before normal validation, so incomplete evidence remains
  a validation `ValueError`, not a shape `TypeError`.
- Added a regression test that inspects the Protocol binding and calls the
  adapter through the `StructuredSourceAdapter` type.
- The live harness now creates a `BrowserObservationPort` mock and explicitly
  calls `assert_not_called()`. `DiscoveryEngine` does not select or invoke
  provider adapters yet, so routing this live provider read through it would
  exercise generic discovery rather than this adapter and add non-ruling GETs.
  The direct adapter harness therefore documents and checks the no-browser
  boundary without adding a production abstraction.

## Fix round 2

- Replaced the disconnected observer mock with a live-gated `DiscoveryEngine`
  invocation that injects the mock, uses a temporary `SQLiteCapabilityStore`,
  sets `browser_fallback=False`, and explicitly asserts both
  `browser_actions == 0` and `observer.observe.assert_not_called()`.
- The store and its test-local HTTP seam are closed in `finally`. The engine
  run is deliberately separate from the provider adapter's three public GET
  reads because provider-adapter dispatch is not part of `DiscoveryEngine` yet.
- The engine budget is 10 seconds and no browser or mutation path is reachable.
- Reformatted all modified Python files, including `types.py`.

## Fix round 3

- Replaced the real discovery HTTP client in the observer proof with a
  deterministic test-local fake that implements the engine's deadline,
  HTTPS-policy, fetch, and close seam. It records only its controlled root and
  `robots.txt` responses; it performs no network I/O.
- The gated live path therefore makes exactly three network requests: publisher
  homepage, `/api/latest/branch`, and `/api/latest/products`, all via GET. The
  fake-call recording separately proves the real `DiscoveryEngine` reached its
  `browser_fallback=False` decision without invoking the injected observer.
