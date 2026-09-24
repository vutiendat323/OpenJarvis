# Kiosk Playwright Customer Display Design

## Purpose

Present every customer-facing menu, cart, bill, and QR display in a dedicated
browser tab controlled by the existing Playwright MCP server.  The Kiosk page
keeps its current voice, visualizer, and manual screen-sharing UI, but no
longer embeds `display.html`.

This is a presentation change only.  The Universal Data Plane remains
HTTP/direct-first for discovery, reads, ordering, and payment.  Browser
interaction remains explicit and is only used when a customer asks to view or
interact with the merchant's live website.

## Decisions

- One Kiosk serves one customer at a time.
- At Kiosk-session startup, create one dedicated `customer-display` tab using
  the already configured Playwright MCP client.
- Keep the existing merchant tab as `live-site`; the display tab is never a
  target for merchant browsing actions.
- The existing `getDisplayMedia()` flow is retained.  The operator explicitly
  shares the headed Chromium window controlled by Playwright.
- Do not add a second browser runtime, BrowserAgent, remote browser stream, or
  iframe fallback.
- A display tab receives only events for its presentation session and starts
  empty for the next customer.

## Architecture

### KioskPresentationSession

`KioskPresentationSession` is a backend-owned lifecycle object for the active
Kiosk.  It owns:

- a generated `presentation_session_id`;
- the identity of the `customer-display` browser tab;
- the identity of the `live-site` tab when one is selected or created;
- the latest customer display state for replay after reconnect or recovery.

It reuses the `MCPClient` already owned by `JarvisSystem`; it does not launch
or manage a separate browser process.  Creating the session is idempotent:
repeated frontend requests for the active Kiosk receive the existing session
and do not create more display tabs.

### PresentationSessionManager

`PresentationSessionManager` is the single backend entry point for creating,
finding, resetting, and recovering `KioskPresentationSession` objects.  On
session creation it uses Playwright MCP's `browser_tabs` capability to create
or select the display tab, then uses `browser_navigate` to load the customer
display route with that session ID.

The manager is responsible for tab roles.  Before an explicit merchant browser
interaction, the browser workflow selects `live-site`; display updates target
only `customer-display`.  This prevents a display update from navigating the
merchant page and prevents merchant actions from accidentally changing the
customer display.

### Customer display route

The frontend adds a dedicated `/customer-display` route.  It connects to the
existing agent-event WebSocket using its `presentation_session_id`, renders the
latest display state, and never invokes agent or ordering tools.  It replaces
the old standalone `display.html` page.

The route supports these safe, structured views:

- menu;
- cart;
- bill;
- payment QR;
- waiting/empty state.

The route renders tool payloads as React data, not HTML supplied by the agent
or merchant.

### Display publisher and WebSocket bridge

Native `display_*` tools continue to publish through the EventBus.  The
publisher resolves the active `KioskPresentationSession`, attaches its
`presentation_session_id`, and stores the normalized latest state before
publishing `DISPLAY_UPDATE`.

The existing WebSocket bridge delivers an event only to a connection that
declared the same session ID.  On connection or reconnect, it also receives
the saved latest state.  There is no global broadcast of customer data.

### Kiosk page

`KioskPage` requests `ensure presentation session` once at Kiosk startup and
does not render `<iframe src="/display.html">`.  It otherwise preserves the
current voice lifecycle, visualizer, overlays, and floating `ScreenShareView`.

When the current customer voice session ends, the Kiosk calls reset for the
presentation session.  The display tab remains open and changes to its empty
waiting state, preventing the next customer from seeing the previous
customer's data.

## Data Flow

1. The operator opens `/kiosk`.
2. `KioskPage` ensures a presentation session once.
3. `PresentationSessionManager` creates/selects `customer-display` through
   Playwright MCP and navigates it to `/customer-display?session=<id>`.
4. The display page connects to the event WebSocket and shows its waiting
   state.
5. The customer asks for menu, cart, bill, or QR information.
6. The agent obtains business data through the direct-first Data Plane and
   calls the relevant `display_*` tool.
7. The display tool publishes a session-scoped `DISPLAY_UPDATE`; the display
   tab renders it immediately.
8. If the customer asks to view Trend Coffee's live site, the agent explicitly
   selects `live-site` and uses Playwright MCP observe/act/verify tools there.
9. At customer-session end, the Kiosk resets the display state and the tab
   returns to waiting.

## Failure Handling

- If MCP is unavailable or the display tab cannot be created, direct-first
  data operations and voice continue.  A display tool returns the explicit
  `presentation_unavailable` result; it does not fall back to an iframe or a
  Kiosk overlay.
- If `customer-display` is closed, the next display attempt recreates that
  tab, reopens the route, and replays the latest state.  The `live-site` tab is
  unchanged.
- If the display page disconnects, it reconnects and receives the saved latest
  state for its session.
- A Kiosk reset removes the saved menu, cart, bill, and QR state before the
  next customer session.
- For manual screen sharing to work, Playwright Chromium must be headed and
  visible in the same desktop session as the operator.  `getDisplayMedia()`
  remains an explicit operator choice; this design does not add a remote
  browser-video bridge.

## Non-goals

- Browser fallback for Data Plane discovery, menu, order, or payment data.
- A remote Playwright-frame transport.
- Changing the current manual screen-share controls or their floating layout.
- Rendering merchant or agent-authored HTML.
- Supporting simultaneous customers on one Kiosk.

## Verification

### Automated

- `PresentationSessionManager` creates the display tab exactly once and is
  idempotent for repeated startup requests.
- It assigns and preserves distinct `customer-display` and `live-site` roles.
- A closed display tab is recreated without navigating the merchant tab.
- WebSocket delivery filters by `presentation_session_id`, replays current
  state after reconnect, and clears it on reset.
- The customer-display route renders structured menu, cart, bill, and QR
  events and ignores another session's events.
- `KioskPage` no longer embeds `display.html`.
- Existing direct-first Data Plane and display-tool tests remain passing.

### Manual acceptance

1. Start the Kiosk with Playwright MCP enabled and open `/kiosk`.
2. Confirm Chromium has a Trend Coffee `live-site` tab and an OpenJarvis
   Customer Display tab.
3. Choose that Chromium window with the existing Share Screen control.
4. Ask to view Trend Coffee; confirm live navigation and interaction stay in
   the merchant tab.
5. Ask for menu, cart, bill, and QR; confirm each appears in the display tab.
6. Close the display tab, request another display update, and confirm only the
   display tab is restored with the latest state.
7. End the customer session and confirm the display tab returns to waiting.
8. Stop screen sharing through both the in-page control and the browser-native
   control; confirm existing behavior is unchanged.
