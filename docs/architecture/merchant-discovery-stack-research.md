# Merchant discovery and direct-execution stack research

**Date:** 2026-08-20  
**Scope:** Trend Coffee first, then other merchant websites  
**Status:** Research recommendation; no production implementation

## Decision

OpenJarvis should use an **HTTP-first escalation ladder**. Playwright is not a
prerequisite for merchant discovery and should not be the default execution
path.

The recommended shape is:

```text
safe discovery
  -> compile MerchantProfile with evidence
  -> validate and quarantine
  -> instantiate a MerchantPort adapter
  -> read through semantic tools
  -> show exact order and obtain confirmation
  -> perform one mutation
  -> independently verify merchant state
```

For Trend Coffee, `httpx` plus HTML/JavaScript parsing is sufficient for the
first adapter. A browser is useful only as a passive network-observation
fallback for sites whose public HTTP surface cannot be recovered statically.
The fallback can use Chrome DevTools Protocol (CDP) or W3C WebDriver BiDi and
does not require `click` or `type`.

For general multi-site crawling, Crawlee is the best optional worker-level
stack. It already models HTTP crawling and adaptive browser fallback. It should
not be added to the latency-sensitive Voice/Kiosk runtime until a real workload
requires its queue, retry, proxy, and autoscaling machinery.

## What the current code provides

The existing [`MerchantPort`](../../src/openjarvis/merchants/port.py) is a good
semantic seam for branches, products, variants, a draft cart, order placement,
and order verification. Its mutation methods return only acknowledgements,
preserving the project's mutate/observe doctrine.

The current production wiring is not real-merchant capable:

- [`SystemBuilder`](../../src/openjarvis/system/builder.py#L479) constructs a
  merchant only for `backend == "fake"` and injects the same instance into all
  ordering tools.
- [`MerchantsConfig`](../../src/openjarvis/core/config.py#L1591) describes only
  `fake` and `none`.
- The [ordering Kiosk preset](../../configs/openjarvis/examples/ordering-kiosk.toml)
  explicitly enables no network or browser tools.
- [`HttpRequestTool`](../../src/openjarvis/tools/http_request.py) is a useful
  SSRF-protected primitive, but every call creates an independent request. It
  has no persistent cookie jar, merchant session, CSRF lifecycle, origin
  contract, typed response mapping, or distinction between discovery and
  mutation. It therefore must not itself become the real merchant adapter.

Production wiring should stop using `FakeMerchant`. Keeping it as a test
fixture is still useful; deleting the fixture would remove deterministic
contract and doctrine tests without improving the production architecture.

A real deployment must also avoid one global mutable cart shared by unrelated
Kiosk/HTTP sessions. The `MerchantPort` implementation may share a read-only
catalog cache, but cart and authentication state must be scoped to one
conversation/customer session.

## Discovery ladder

The discovery engine should stop at the first level that yields an attributable
and sufficiently described contract.

| Level | Technique | Browser required | Accepted output |
|---|---|---:|---|
| 0 | Read `robots.txt`, linked Terms, sitemap, origin headers | No | Crawl policy and seed URLs |
| 1 | Read `/.well-known/api-catalog` and `Link`/HTML relations such as `api-catalog`, `service-desc`, and `service-doc` | No | Publisher-declared API/catalog links |
| 2 | Parse JSON-LD and schema.org `Restaurant`, `Menu`, `MenuItem`, `Product`, and `Offer` | No | Merchant identity, branches/menu/price candidates |
| 3 | Parse OpenAPI or GraphQL introspection when the publisher exposes it | No | Machine-described operations and schemas |
| 4 | Fetch HTML and same-origin JavaScript assets; extract API bases, route strings, and request-construction candidates without executing the bundle | No | Evidence-backed endpoint candidates |
| 5 | Validate candidates using safe `GET`, `HEAD`, or `OPTIONS`; map responses into the semantic domain | No | Read-only `MerchantProfile` capabilities |
| 6 | Render and passively observe Fetch/XHR with CDP or WebDriver BiDi | Yes, observation only | Requests/responses touched by the observed page flow |
| 7 | Guided DOM interaction | Yes | Last-resort interactive execution, not discovery's default |

The standard first steps are not speculative conventions:

- [RFC 9727](https://www.rfc-editor.org/rfc/rfc9727.html) defines
  `/.well-known/api-catalog` and the `api-catalog` link relation for automated
  discovery of published APIs.
- [RFC 8631](https://www.rfc-editor.org/rfc/rfc8631.html) defines the
  `service-desc` and `service-doc` link relations.
- [OpenAPI](https://spec.openapis.org/oas/latest.html) is a
  language-independent HTTP API description. OpenAPI 3.2 recommends
  `openapi.json` or `openapi.yaml` for an entry document, but that is not a
  universal well-known discovery path; do not brute-force arbitrary filenames.
- [GraphQL introspection](https://spec.graphql.org/September2025/#sec-Introspection)
  exposes a service's schema through `__schema` and `__type` when the publisher
  permits it.
- [JSON-LD 1.1](https://www.w3.org/TR/json-ld11/) and schema.org types such as
  [`Restaurant`](https://schema.org/Restaurant),
  [`Menu`](https://schema.org/Menu),
  [`MenuItem`](https://schema.org/MenuItem), and
  [`Offer`](https://schema.org/Offer) provide a cheap structured-data path.

Static JavaScript analysis should produce candidates, not authority. A string
that looks like `/orders` does not prove its method, required body, auth, or
semantics. Every promoted capability needs publisher documentation or observed
correspondence evidence plus typed validation.

## Stack comparison

### Recommended core: HTTPX plus a small HTML parser

Use a persistent per-origin `httpx.Client` initially because `MerchantPort` is
currently synchronous. `httpx.Client` provides connection pooling and cookie
persistence across requests; its official documentation explicitly positions
it as the session equivalent of `requests.Session`
([HTTPX Clients](https://www.python-httpx.org/advanced/clients/)). If the port is
later made async end-to-end, move to `httpx.AsyncClient` rather than hiding an
event loop inside the adapter.

For HTML and script discovery, add either:

- [`selectolax`](https://selectolax.readthedocs.io/en/latest/parser.html): small,
  fast, CSS-selector-oriented, and appropriate for focused page/asset discovery;
  or
- [`lxml.html`](https://lxml.de/lxmlhtml.html): mature HTML parsing and link/form
  utilities, but a larger general-purpose XML dependency.

Recommendation: start with `selectolax` in an optional `merchant-discovery`
extra. Parse JSON-LD with the standard JSON decoder before adding a separate
metadata framework. This is the smallest stack that solves Trend Coffee.

### Optional multi-site worker: Crawlee for Python

Crawlee provides request queues, session management, retries, concurrency and
both HTTP and browser crawlers. Its
[`AdaptivePlaywrightCrawler`](https://crawlee.dev/python/docs/0.6/guides/adaptive-playwright-crawler)
combines an HTTP parser with browser rendering and learns which path a site
needs.

Use it when OpenJarvis must crawl many pages/origins repeatedly. Run it as a
bounded discovery worker rather than embedding its whole lifecycle into each
Voice turn. The semantic Agent should receive a compact discovery result, not
raw crawl pages.

### Alternative at crawler scale: Scrapy

Scrapy is mature for broad link traversal and has an official
[`AutoThrottle`](https://docs.scrapy.org/en/latest/topics/autothrottle.html)
extension. It is a reasonable separate-process crawler, but its framework and
reactor lifecycle are unnecessary for one merchant and awkward to embed beside
the existing asyncio/Pipecat server. It is not recommended for the first
implementation.

### Dynamic fallback: CDP or WebDriver BiDi

When HTML and bundles do not reveal the actual API, open an isolated browser
context, navigate, and record network traffic:

- CDP's [`Network` domain](https://chromedevtools.github.io/devtools-protocol/tot/Network/)
  exposes request/response headers, bodies, timing, cookies, and Fetch/XHR
  traffic. It is Chromium-specific.
- W3C [`WebDriver BiDi` Network](https://www.w3.org/TR/webdriver-bidi/#module-network)
  is the browser-neutral protocol choice.

This needs a narrow `merchant_network_observe` capability, not automatic access
to all form-filling/browser-action tools. The current Playwright MCP preset does
not expose network tooling, so merely combining that preset with the Kiosk
preset would still not implement passive API discovery.

## Trend Coffee evidence

Read-only checks on 2026-08-20 found:

- [`https://trendcoffee.net/`](https://trendcoffee.net/) is a small SPA shell
  that points to a versioned JavaScript module.
- The current first-party
  [application bundle](https://trendcoffee.net/assets/index-CjHlC2Rr.js)
  contains the API base `https://trendcoffee.net/api/latest` and route strings
  for `/products`, `/branch`, `/orders/public`, and
  `/payment/initiate/public`.
- The first-party [`/branch`](https://trendcoffee.net/api/latest/branch) and
  [`/products`](https://trendcoffee.net/api/latest/products) endpoints return
  public JSON with the same branch/product/variant model already represented by
  `MerchantPort`.
- [`robots.txt`](https://trendcoffee.net/robots.txt) currently allows the
  wildcard user agent but explicitly disallows several named bots and carries
  Cloudflare content signals. OpenJarvis must send an honest, stable user-agent
  and apply the matching rules for that identity.
- `/sitemap.xml` currently returns the SPA HTML shell rather than a sitemap.

This evidence supports a dedicated `TrendCoffeeMerchant` first:

```text
TrendCoffeeMerchant
  - HTTPX session: origin, cookies, timeouts, rate policy
  - catalog mapper: branch/product/variant JSON -> MerchantPort dataclasses
  - per-conversation draft cart
  - order executor: typed, fixed endpoint and body schema
  - verifier: public order read mapped back to Order
```

Do not treat a minified asset hash as a permanent contract. Store the source
URL, discovery timestamp, asset fingerprint, observed response schemas, and an
expiry. A changed fingerprint or schema demotes the capability to quarantine
until read-only validation passes again.

Trend's payment endpoints do not fit the present `MerchantPort`, which ends at
`read_order`. Payment should remain a separate `PaymentPort`/payment tool pair,
as the existing design already anticipates with `payment_start` and
`payment_verify`. A QR display must accept a server-side payment identifier and
resolve the merchant-returned payload; it must never accept an LLM-authored QR
payload.

## Authentication and session handling

Each merchant profile needs an explicit auth mode: `none`, cookie session,
API key reference, OAuth, or interactive-only. Cookies follow
[RFC 6265](https://www.rfc-editor.org/rfc/rfc6265.html); OAuth deployments
should follow the current security best practice in
[RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html).

Rules for OpenJarvis:

- Store credential references, never tokens or cookies, in a merchant profile.
- Keep cookie jars and CSRF tokens backend-only and per user/session.
- Use an isolated merchant browser profile for interactive login; never import
  a user's general browsing profile.
- Never promote an endpoint merely because a logged-in browser emitted it.
  Attribute it to an origin/action and remove literal secrets before storage.
- Enforce HTTPS and an origin allowlist in addition to the existing SSRF checks;
  re-check every redirect.

OWASP's
[SSRF prevention guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
supports allowlisting trusted destinations where the application can identify
them. The current `HttpRequestTool` already re-checks redirect targets, which
should be reused rather than weakened.

## Robots, terms, and load policy

[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html) requires a crawler to
follow successfully fetched, parseable robots rules. It also states that
robots is not an access-control mechanism. Therefore OpenJarvis must check both
robots and the publisher's Terms/API policy; one does not replace the other.

Per origin:

- Start with concurrency `1` and a descriptive user-agent.
- Cache catalog/menu GETs and use validators such as ETag/Last-Modified where
  available.
- Respect `429`, `503`, and `Retry-After`; back off rather than rotate identity.
- Bound page count, depth, total bytes, asset size, redirects, and wall time.
- Never crawl authenticated/private paths discovered accidentally.

HTTP defines `Retry-After` and method retry semantics in
[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html). These controls belong
in the discovery/session layer, not in the LLM prompt.

## Safe direct execution

The Agent must not receive an unrestricted raw HTTP mutation tool. Discovery
and execution are separate authorities:

```text
MerchantDiscoverer: GET/HEAD/OPTIONS only -> MerchantProfile candidate
MerchantProfileValidator: offline/static checks -> validated or quarantined
MerchantPort adapter: semantic reads and typed mutations
Agent: chooses semantic tools; never constructs merchant HTTP directly
```

Before `order_place`, present and confirm the exact merchant, branch, fulfillment
type, items, variants, quantities, notes, total, customer/delivery fields, and
payment consequence. OWASP recommends that transaction authorization show
significant data and that the user be able to identify what is being authorized
([Transaction Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html)).

Then:

1. Bind the confirmation to a hash of that exact order preview and expire it
   after any cart/profile change.
2. Serialize the mutation (`parallel_tools = false`).
3. Send one typed request to an allowlisted endpoint.
4. Never automatically retry `POST` unless the merchant contract provides a
   real idempotency mechanism.
5. Call a separate observing method and compare merchant-recorded state with
   the confirmed preview.
6. Redact credentials and personal data from traces.

[RFC 9110's safe and idempotent method semantics](https://www.rfc-editor.org/rfc/rfc9110.html#name-common-method-properties)
are decisive here: `GET`/`HEAD` are safe by defined semantics, while a client
must not automatically retry a non-idempotent method without knowing that its
semantics are idempotent.

## Required correction to the existing design spec

The current
[goal-execution spec](../superpowers/specs/2026-08-19-one-jarvis-goal-execution-design.md)
proposes discovering request schemas by sending deliberately incomplete `POST`
bodies and assumes each validation failure creates nothing. That is not a
general safety guarantee. `POST` is unsafe under HTTP semantics, and a server
may log, reserve, enqueue, rate-charge, or partly apply a request before
returning a validation error.

For a general merchant capability, replace that step with:

1. publisher API catalog/service links;
2. OpenAPI/GraphQL/JSON-LD;
3. static HTML and bundle analysis;
4. safe read-only validation;
5. passive network observation;
6. merchant-approved sandbox/staging probes, if available.

Invalid-body mutation probing is acceptable only against a merchant-owned test
environment or with explicit merchant authorization and documented
transactional guarantees. It must not be Phase 2's default discovery method.

The rest of the existing design remains compatible: `MerchantCapability`
quarantine, no inline secrets, semantic tools, mutate/observe separation,
mandatory verification, and irreversible payment kept interactive.

## Recommended delivery sequence and gates

1. **Trend adapter, read-only:** discover/fingerprint the official surface;
   implement branch/menu/product mapping through `MerchantPort`.
   Gate: live reads match the public endpoints and use no browser.
2. **Per-session draft cart:** preserve the semantic cart tools locally while
   binding only current merchant variant IDs.
   Gate: two sessions cannot see or mutate each other's carts.
3. **Confirmed direct order:** typed order request, exact preview hash, one
   mutation, independent verification, no automatic POST retry.
   Gate: a live authorized test order matches branch/items/quantity/note/total.
4. **General discovery profile:** standards-first ladder, evidence/fingerprint,
   quarantine, expiry and demotion.
   Gate: a candidate can never execute; a stale profile fails closed.
5. **Passive dynamic fallback:** CDP or WebDriver BiDi network observation.
   Gate: recover an API from a JS-rendered fixture without `click`/`type` and
   without exposing cookies in the profile.
6. **Payment separately:** approval, payment initiation/verification and
   identifier-only QR display.
   Gate: no LLM-supplied QR payload and no payment before exact confirmation.

This sequence gives the Kiosk real discovery and direct execution without
making Playwright a latency or availability dependency, while retaining a
bounded fallback for websites that genuinely require a browser.
