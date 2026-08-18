# TrendCoffee Kiosk — observed automation contract

Date: 2026-08-19
Status: Salvage notes, not a design

## Why this file exists

`/home/robber/Work/jarvis/trendcoffee-kiosk` was read during design on
2026-08-19 and disappeared from the filesystem during the same session. It was
untracked in git, so no copy exists in this repository. These are the facts
captured from it before it vanished, recorded so the design work done against
it is not lost.

**Everything below is transcribed from a reading, not from a live source.
Verify against the project before relying on any of it.**

## What the project was

A Vite + React 19 + Tailwind kiosk web app for "Trend Coffee & Roastery",
built explicitly for AI browser-agent control. Its README described a
"Zero-Touch Paradigm":

```
customer voice
  → AI agent (Gemini / Pipecat Voice)
  → Playwright MCP tools
  → kiosk browser (DOM actions and VietQR canvas)
```

Run locally with `npm run dev`, served at `http://localhost:3000`
(`vite --port 3000 --host 0.0.0.0`).

Dependencies of note: `qrcode`, `canvas-confetti`, `lucide-react`,
`tailwind-merge`, `clsx`. No WebRTC, no Pipecat, no reference to port 8000 —
**it contained no voice client of its own.**

## Structure

```
src/
  App.tsx
  main.tsx
  index.css
  components/
    WelcomeStage.tsx
    MenuStage.tsx
    CartStage.tsx
    CheckoutStage.tsx
    ReceiptStage.tsx
    Header.tsx
    AgentHUD.tsx
    BotanicalDecor.tsx
  data/
    kioskContext.tsx     # KioskProvider, global agent API
    menu.ts              # PRODUCTS
  types/
    kiosk.ts
```

Stage flow: `welcome → menu → cart → checkout → receipt`.

## The `data-testid` contract

Transcribed from README section 4. This is the merchant's hand-written,
documented automation contract — the reason no capability discovery is needed
for this merchant.

| Action | `data-testid` |
|---|---|
| Start ordering | `btn-start-order` |
| Category tabs | `cat-all`, `cat-coffee`, `cat-tea`, `cat-freeze`, `cat-food`, `cat-combo` |
| Menu search box | `input-search-menu` |
| Product card | `product-card-{id}` |
| Quick add | `btn-add-item-{id}` |
| Open customizer | `btn-customize-item-{id}` |
| Size modifier | `modifier-size-{id}` (M, L) |
| Sugar modifier | `modifier-sugar-{id}` (100%, 70%, 50%, 0%) |
| Ice modifier | `modifier-ice-{id}` (100%, 50%, none, hot) |
| Table number | `input-table-number` |
| Customer name | `input-customer-name` |
| Proceed to QR | `btn-proceed-checkout` |
| VietQR canvas | `vietqr-canvas` |
| Confirm payment | `btn-simulate-payment-success` |
| Receipt order id | `receipt-order-id` |
| New order | `btn-new-order` |

Additional ids observed directly in the components, not listed in the README:

`stage-cart-empty`, `stage-cart`, `btn-back-to-menu`, `btn-continue-shopping`,
`btn-clear-cart`, `cart-item-{id}`, `btn-qty-minus-{id}`, `qty-val-{id}`,
`btn-qty-plus-{id}`, `btn-remove-{id}`, `opt-dine-in`, `opt-take-away`,
`input-customer-phone`, `cart-subtotal`, `cart-discount`, `cart-total`,
`kiosk-header`, `brand-logo-btn`, `stage-breadcrumbs`, `breadcrumb-{id}`,
`voice-agent-pill`, `btn-reset-kiosk`, `agent-hud-container`,
`btn-run-agent-macro`, `input-simulated-speech`, `btn-submit-speech`,
`agent-event-logs`.

## The `window.__TREND_KIOSK__` API

Registered in a `useEffect` in `kioskContext.tsx` (around line 309), typed via
a `declare global` block (around line 64):

```ts
window.__TREND_KIOSK__ = {
  getMenu:              () => Product[],
  getCart:              () => CartItem[],
  setStage:             (stage: KioskStage) => void,
  selectCategory:       (catId: CategoryId) => void,
  searchMenu:           (q: string) => void,
  addQuickItem:         (productId: string, quantity?: number) => boolean,
  setCustomerInfo:      (info: Partial<CustomerInfo>) => void,
  submitOrder:          () => Order,
  simulatePaymentSuccess: () => void,
  reset:                () => void,
}
```

**This API is not reachable from the audited browser toolset.** The Playwright
MCP preset
(`configs/openjarvis/examples/browser-agent-playwright-mcp.toml`) allowlists 21
tools and deliberately excludes every code-execution tool, so there is no
`browser_evaluate`. Driving the kiosk therefore means DOM actions against the
`data-testid` contract above.

That exclusion should be kept. Every action routed through the real UI is an
action the customer can see happening on the screen in front of them. Calling
`submitOrder()` directly would change state the rendered page might not
reflect — the exact failure mode `configs/openjarvis/prompts/browser-agent.md`
warns about ("the user is looking at the real screen and will see that nothing
happened").

## Consequences for the goal-execution design

The design at `2026-08-19-one-jarvis-goal-execution-design.md` was written
assuming an opaque third-party merchant site. Against this kiosk, large parts
of it are unnecessary:

| Design section | Status against this merchant |
|---|---|
| 5, display surface (`display.html`, iframe, `display_menu`, `display_cart`, `display_qr`) | Already provided. The kiosk renders menu, cart, checkout, VietQR canvas and receipt itself. |
| 4, `MerchantCapability`, `merchant_learn`, correspondence checks, CANDIDATE quarantine | Not needed. The `data-testid` table is a hand-written, documented capability record the merchant maintains. Nothing to discover. |
| 3, semantic tools (`cart_add`, `order_place`, `cart_view`) | Not needed. The Agent acts on the DOM. |
| 3, mutation/observation doctrine | Already satisfied. `browser_click` and `browser_type` mutate; `browser_snapshot`, `browser_find` and the four `browser_verify_*` tools observe. Separate tools already. |
| QR safety rule | Satisfied more strongly than designed. The page generates its own VietQR; the Agent never supplies QR content at all. |

What remained to build was small: an `ordering` skill mapping conversation to
the testid flow, merging the browser MCP tools into the Voice/Kiosk serving
config, and an approval gate on `btn-simulate-payment-success`.

## Open question that was never resolved

The decision taken was that the Agent's own headed Chromium is the
customer-facing screen, showing this kiosk fullscreen at `:3000`.

That leaves voice unresolved. WebRTC needs a browser peer for microphone and
speaker, and the voice client lives in `KioskPage.tsx`, served from `:8000`.
This project contained no voice client. Either the Pipecat client moves into
this kiosk, or the voice session runs in a second, non-visible browser context
and the two must not interfere — `browser_navigate`, `browser_close` and
`btn-reset-kiosk` would each be able to kill a voice session sharing the page.
