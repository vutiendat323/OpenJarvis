"""Customer-display sessions backed by the already-live Playwright MCP client."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock, RLock
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import quote, urlsplit
from uuid import uuid4

from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.core.conversation import conversation_scope, current_conversation_id
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult

logger = logging.getLogger(__name__)


class PresentationUnavailableError(RuntimeError):
    """Raised when the customer display cannot be backed by Playwright."""


def _fetch_json(url: str) -> Any:
    import httpx

    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    return response.json()


@dataclass(frozen=True, slots=True)
class TouchCheckout:
    """The merchant checkout recipe plus the reads the touch cart needs."""

    run: Callable[..., ToolResult]
    tables_url: str
    order_url: str  # "{order_id}" is replaced with the displayed order
    fetch_json: Callable[[str], Any] = _fetch_json
    invoice_url: str = ""
    print_queue: str = ""
    print_width_dots: int = 576


_PRESENTATION_GENERATION: ContextVar[str | None] = ContextVar(
    "openjarvis_presentation_generation", default=None
)


@contextmanager
def presentation_generation(generation: str | None) -> Iterator[None]:
    """Correlate display publications made by one native Voice session."""
    token = _PRESENTATION_GENERATION.set(generation)
    try:
        yield
    finally:
        _PRESENTATION_GENERATION.reset(token)


_ORDER_FIELDS = (
    "order_id",
    "payment_slug",
    "status",
    "payment_status",
    "total",
    "order_type",
    "branch",
    "table_name",
    "lines",
    "created_at",
)


@dataclass(slots=True)
class PresentationSession:
    """State isolated to one customer-display browser page."""

    session_id: str
    display_url: str
    last_payload: dict[str, Any] = field(default_factory=lambda: {"view": "none"})
    display_connected: bool = True
    initial_display_started: bool = False
    # Portions of the last menu shown, keyed by variant id: a touch order is
    # priced from what the customer saw, never from what the page sends.
    menu_variants: dict[str, dict[str, Any]] = field(default_factory=dict)
    # The last live table read, so a tap can only select a table it was shown.
    tables: dict[str, dict[str, str]] = field(default_factory=dict)
    # The payment receipt on screen until the merchant reports it paid.
    payment: dict[str, Any] | None = None
    payment_conversation_id: str = ""
    # CART retains its last draft/invoice while the customer browses the menu.
    cart_snapshot: dict[str, Any] | None = None
    cart_conversation_id: str = ""
    # QR-free references survive navigation and later orders, owned by Voice.
    orders: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Retain verified print status for this session's receipts across navigation.
    printing_states: dict[str, str | None] = field(default_factory=dict)
    # Menu rows the customer found by typing on the display, in screen order.
    screen_search: list[dict[str, Any]] = field(default_factory=list)


def find_playwright_client(clients: Iterable[Any]) -> Any | None:
    """Return the configured MCP client for the Playwright server, if present."""
    return next(
        (
            client
            for client in clients
            if getattr(client, "_server_name", None) == "playwright"
        ),
        None,
    )


class PresentationSessionManager:
    """Own the primary customer-display tab and its automation companion."""

    def __init__(
        self,
        bus: EventBus,
        client: Any | None,
        *,
        shared_page: bool = False,
        current_page_url: Callable[[], str] | None = None,
    ) -> None:
        self._bus = bus
        self._client = find_playwright_client([client]) if client is not None else None
        self._shared_page = shared_page
        self._current_page_url = current_page_url
        self._lock = RLock()
        # Serialises ensure() so its slow first navigation can run without _lock.
        self._ensure_lock = Lock()
        self._session: PresentationSession | None = None
        self._active_generation: str | None = None
        self._initial_display: Callable[[], ToolResult] | None = None
        self._touch_checkout: TouchCheckout | None = None
        self._ui_language: str | None = None

    def set_language(self, language: str) -> None:
        """Update active language for customer presentation.

        Called from async routes on the event loop, so it must never wait for
        ``_lock``: ``ensure`` holds it across a browser call whose page needs
        that same loop to load. One attribute assignment is atomic.
        """
        self._ui_language = language

    def configure_initial_display(self, loader: Callable[[], ToolResult]) -> None:
        """Configure the recipe-backed display load for a newly opened tab."""
        with self._lock:
            self._initial_display = loader

    def configure_touch_checkout(self, checkout: TouchCheckout) -> None:
        """Configure the merchant checkout the customer can start by touch."""
        with self._lock:
            self._touch_checkout = checkout

    @property
    def touch_checkout(self) -> TouchCheckout | None:
        return self._touch_checkout

    def live_tables(self, session_id: str) -> list[dict[str, str]] | None:
        """Read the merchant's tables now and remember them for this session."""
        checkout = self._touch_checkout
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
        if checkout is None:
            raise PresentationUnavailableError("touch_checkout_unavailable")
        try:
            rows = checkout.fetch_json(checkout.tables_url)["result"]
            if not isinstance(rows, list):
                raise TypeError("table list expected")
        except Exception as exc:
            raise PresentationUnavailableError("tables_unavailable") from exc
        tables = sorted(
            (
                {"slug": row["slug"], "name": row["name"], "status": row["status"]}
                for row in rows
                if isinstance(row, dict)
                and all(
                    isinstance(row.get(key), str) and row[key].strip()
                    for key in ("slug", "name", "status")
                )
            ),
            key=_table_order,
        )
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            self._session.tables = {row["slug"]: row for row in tables}
        return [dict(row) for row in tables]

    def share_screen_search(self, session_id: str, item_ids: list[str]) -> int | None:
        """Record what a typed search shows, resolved against the shown menu.

        Only ids from the menu this display last published become rows, so the
        page can point at items but never name or price them itself.
        """
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            variants = self._session.menu_variants
            rows = [
                {
                    "id": variant["variant_id"],
                    "name": variant["name"],
                    "price": variant["unit_price"],
                    "available": variant["available"],
                }
                for item_id in dict.fromkeys(item_ids)
                if (variant := variants.get(item_id)) is not None
            ]
            self._session.screen_search = rows
            return len(rows)

    def screen_search(self) -> list[dict[str, Any]]:
        """The rows the customer currently sees from their own typed search."""
        with self._lock:
            if self._session is None:
                return []
            return [dict(row) for row in self._session.screen_search]

    def shown_payment(self) -> dict[str, Any] | None:
        """The order this conversation's customer is asked to pay, as shown.

        Only for the conversation that put the payment QR on screen; the QR
        value itself is left out.
        """
        owner = current_conversation_id()
        with self._lock:
            if (
                not owner
                or self._session is None
                or self._session.payment is None
                or self._session.payment_conversation_id != owner
            ):
                return None
            payment = self._session.payment
            return {
                key: deepcopy(payment[key])
                for key in ("order_id", "status", "total", "order_type", "lines")
                if key in payment
            }

    def restore_cart_receipt(self) -> ToolResult | None:
        """Show CART's retained invoice for its owner, without checkout or edits."""
        with self._lock:
            session = self._session
            if (
                session is None
                or not current_conversation_id()
                or session.cart_conversation_id != current_conversation_id()
                or session.cart_snapshot is None
                or session.cart_snapshot.get("view") not in {"bill", "payment_qr"}
            ):
                return None
            receipt = deepcopy(session.cart_snapshot)
            result = self.publish(receipt)
            if result.success:
                result.content = json.dumps(
                    {
                        "shown": receipt["view"],
                        "order": {
                            key: receipt[key] for key in _ORDER_FIELDS if key in receipt
                        },
                    },
                    ensure_ascii=False,
                )
            return result

    def remember_cart(self, cart: dict[str, Any]) -> None:
        """Retain a touch draft without publishing or selecting a screen."""
        with self._lock:
            generation = _PRESENTATION_GENERATION.get()
            if (
                self._session is not None
                and current_conversation_id()
                and (generation is None or generation == self._active_generation)
            ):
                self._session.cart_snapshot = {"view": "cart", **deepcopy(cart)}
                self._session.cart_conversation_id = current_conversation_id()

    def known_orders(self) -> list[dict[str, Any]]:
        """Created receipts belonging to the current Voice conversation."""
        owner = current_conversation_id()
        with self._lock:
            if not owner or self._session is None:
                return []
            return [
                {key: deepcopy(row[key]) for key in _ORDER_FIELDS if key in row}
                for row in self._session.orders.values()
                if row.get("_owner") == owner
            ]

    def update_order(self, order_id: str, data: dict[str, Any]) -> bool:
        """Refresh an owned reference without changing the selected screen."""
        owner = current_conversation_id()
        with self._lock:
            if not owner or self._session is None:
                return False
            generation = _PRESENTATION_GENERATION.get()
            if generation is not None and generation != self._active_generation:
                return False
            row = self._session.orders.get(order_id)
            if row is None or row.get("_owner") != owner:
                return False
            row.update(
                {key: deepcopy(data[key]) for key in _ORDER_FIELDS if key in data}
            )
            return True

    def begin_order_read(self, order_id: str) -> dict[str, Any] | None:
        """Reserve the latest read of an owned order before its network request."""
        with self._lock:
            selected = next(
                (row for row in self.known_orders() if row["order_id"] == order_id),
                None,
            )
            if selected is None:
                return None
            token = uuid4().hex
            self._session.orders[order_id]["_read_token"] = token
            return {**selected, "read_token": token}

    @contextmanager
    def order_read_scope(self, order_id: str, token: str) -> Iterator[bool]:
        """Apply the latest read atomically with its corresponding display update."""
        with self._lock:
            row = self._session.orders.get(order_id) if self._session else None
            generation = _PRESENTATION_GENERATION.get()
            yield bool(
                token
                and row
                and row.get("_owner") == current_conversation_id()
                and row.get("_read_token") == token
                and (generation is None or generation == self._active_generation)
            )

    def invalidate_order_qr(self, order_id: str, receipt: dict[str, Any]) -> None:
        """Remove an invalidated QR without selecting a different order's screen."""
        with self._lock:
            session = self._session
            if not session or not session.payment:
                return
            if session.payment.get("order_id") != order_id:
                return
            if session.last_payload.get("view") == "payment_qr" and (
                session.last_payload.get("order_id") == order_id
            ):
                self.publish({"view": "bill", **receipt})
            else:
                self.publish({"view": "bill", **receipt, "navigate": False})

    def table(self, session_id: str, slug: str) -> dict[str, str] | None:
        """Return one table from this session's last live read."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            row = self._session.tables.get(slug)
            return dict(row) if row is not None else None

    def check_payment(self, session_id: str) -> str | None:
        """Ask the merchant about the displayed order; show its bill once paid."""
        checkout = self._touch_checkout
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            payment = self._session.payment
            payment_conversation_id = self._session.payment_conversation_id
        if payment is None:
            return "none"
        if checkout is None:
            raise PresentationUnavailableError("touch_checkout_unavailable")
        url = checkout.order_url.replace(
            "{order_id}", quote(str(payment["order_id"]), safe="")
        )
        try:
            order = checkout.fetch_json(url)["result"]
            if order.get("slug") != payment["order_id"]:
                raise ValueError("merchant_order_mismatch")
            status = order["status"]
        except Exception as exc:
            raise PresentationUnavailableError("order_unavailable") from exc
        if status != "paid":
            return status if isinstance(status, str) else "unknown"
        with self._lock:
            # A concurrent poll may already have shown this bill.
            if self._session is None or self._session.payment is not payment:
                return "none"
            # Browser polling has no ambient Voice owner; retain the owner
            # captured when this QR was shown, including read invalidation.
            with conversation_scope(payment_conversation_id):
                shown = self.publish(
                    {
                        "view": "bill",
                        "navigate": False,
                        "order_id": payment["order_id"],
                        "status": "paid",
                        **(
                            {"printing_state": payment["printing_state"]}
                            if "printing_state" in payment
                            else {}
                        ),
                        "order_type": payment.get("order_type", ""),
                        "branch": payment.get("branch", ""),
                        "lines": payment.get("lines", []),
                        "total": payment.get("total", 0),
                    }
                )
            if (
                shown.success
                and payment_conversation_id
                and not any(
                    entry.tool == "payment_completed"
                    and entry.call_id == payment["order_id"]
                    for entry in ACTION_LEDGER.entries(payment_conversation_id)
                )
            ):
                ACTION_LEDGER.start(
                    payment_conversation_id,
                    call_id=payment["order_id"],
                    tool="payment_completed",
                    run_id="merchant-payment",
                )
                ACTION_LEDGER.finish(
                    payment_conversation_id,
                    call_id=payment["order_id"],
                    run_id="merchant-payment",
                    result=ToolResult(
                        tool_name="payment_completed",
                        content="paid",
                        metadata={
                            "event_context": json.dumps(
                                {
                                    "event": "payment_completed",
                                    "order_id": payment["order_id"],
                                    "payment_status": "paid",
                                    "view": "bill",
                                }
                            )
                        },
                    ),
                )
        return "paid"

    def preload_initial_display(self) -> bool:
        """Run the configured display recipe once without racing a Voice turn."""
        with self._lock:
            session = self._session
            loader = self._initial_display
            if (
                session is None
                or loader is None
                or session.initial_display_started
                or self._active_generation is not None
            ):
                return False
            session.initial_display_started = True
            generation = f"initial-display-{session.session_id}"
            self._active_generation = generation

        try:
            with presentation_generation(generation):
                result = loader()
            if not result.success:
                logger.warning("Initial display recipe failed.")
            return result.success
        except Exception:
            logger.exception("Initial display recipe raised.")
            return False

    def load_initial_display(self) -> bool:
        """Run the configured display recipe for an active kiosk session."""
        with self._lock:
            if self._session is None or self._initial_display is None:
                return False
            loader = self._initial_display

        try:
            result = loader()
            if not result.success:
                logger.warning("Initial display recipe failed.")
            return result.success
        except Exception:
            logger.exception("Initial display recipe raised.")
            return False

    def ensure(self, display_origin: str) -> PresentationSession:
        """Create one display tab, returning the existing session on later calls.

        Never selects the tab: Playwright's tab selection raises the browser
        window and steals OS focus from the operator's kiosk browser.
        """
        origin = _normalize_display_origin(display_origin)
        with self._ensure_lock:
            with self._lock:
                if self._session is not None:
                    self.recover_display_tab()
                    return self._session
                session_id = uuid4().hex
                display_url = f"{origin}/customer-display?session={session_id}"
                if self._ui_language:
                    display_url = f"{display_url}&lang={self._ui_language}"
                session = PresentationSession(
                    session_id=session_id,
                    display_url=display_url,
                )
                # Visible before the page loads: the display connects back here
                # while this call still waits for it, and every route that reads
                # this state runs on the event loop that page needs. _lock must
                # not be held across the navigation.
                self._session = session
            try:
                self._call_tool("browser_navigate", {"url": session.display_url})
            except BaseException:
                with self._lock:
                    if self._session is session:
                        self._session = None
                raise
            return session

    def publish(self, payload: dict[str, Any]) -> ToolResult:
        """Publish a display event scoped to the active customer session."""
        from openjarvis.agents._stubs import check_agent_cancelled
        from openjarvis.tools._stubs import tool_is_executing

        # A dispatched tool finishes its display after a barge-in (spec
        # 2026-10-10 background actions D2); only direct publishes are guarded.
        guarded = not tool_is_executing()
        with self._lock:
            if guarded:
                check_agent_cancelled()
            if self._session is None or self._client is None:
                return ToolResult(
                    tool_name="presentation",
                    content="presentation_unavailable",
                    success=False,
                )
            publication_generation = _PRESENTATION_GENERATION.get()
            if (
                publication_generation is not None
                and publication_generation != self._active_generation
            ):
                return ToolResult(
                    tool_name="presentation",
                    content="presentation_stale_generation",
                    success=False,
                )
            if not self._session.display_connected:
                if self._shared_page and payload.get("navigate") is not False:
                    # An explicit display action returns the shared page to the
                    # receipt/menu; passive ensure/recovery never changes it.
                    if self._current_page_url is None or (
                        self._current_page_url() != self._session.display_url
                    ):
                        self._call_tool(
                            "browser_navigate", {"url": self._session.display_url}
                        )
                else:
                    self.recover_display_tab()
            if guarded:
                check_agent_cancelled()
            normalized = deepcopy(payload)
            normalized["presentation_session_id"] = self._session.session_id
            if normalized.get("view") == "payment_qr":
                # A QR without merchant time still gets one fixed deadline;
                # browsing/reopening must never restart its payment window.
                try:
                    created = datetime.fromisoformat(normalized.get("created_at", ""))
                except (TypeError, ValueError):
                    try:
                        created = datetime.strptime(
                            str(normalized.get("created_at", "")).split(" (")[0],
                            "%a %b %d %Y %H:%M:%S GMT%z",
                        )
                    except ValueError:
                        created = None
                if created is None or created.tzinfo is None:
                    previous = self._session.orders.get(normalized.get("order_id"), {})
                    normalized["created_at"] = (
                        previous.get("created_at")
                        if previous.get("_owner") == current_conversation_id()
                        else None
                    ) or datetime.now(timezone.utc).isoformat()
            if normalized.get("view") in {"bill", "payment_qr"}:
                order_id = normalized.get("order_id")
                if isinstance(order_id, str) and order_id:
                    owner = current_conversation_id()
                    if owner:
                        row = self._session.orders.setdefault(
                            order_id, {"_owner": owner}
                        )
                        if row.get("_owner") == owner:
                            # A newly displayed receipt (including paid polling)
                            # supersedes in-flight reads of the previous state.
                            row.pop("_read_token", None)
                            row.update(
                                {
                                    key: deepcopy(normalized[key])
                                    for key in _ORDER_FIELDS
                                    if key in normalized
                                }
                            )
                        while len(self._session.orders) > 20:
                            self._session.orders.pop(next(iter(self._session.orders)))
                    state = self._session.printing_states.setdefault(order_id, None)
                    if state is not None:
                        normalized["printing_state"] = state
            if normalized.get("navigate") is not False:
                self._session.last_payload = normalized
            elif (
                normalized.get("view") == "cart"
                and self._session.last_payload.get("view") == "cart"
            ) or (
                normalized.get("view") == "bill"
                and self._session.last_payload.get("view") in {"bill", "payment_qr"}
                and self._session.last_payload.get("order_id")
                == normalized.get("order_id")
            ):
                # A reconnect replays the screen: keep the open receipt current
                # without letting a background update choose the screen.
                self._session.last_payload = {
                    key: value for key, value in normalized.items() if key != "navigate"
                }
            if normalized.get("view") == "menu":
                self._session.menu_variants = _menu_variants(
                    normalized.get("menu_items")
                )
                # A new spoken search replaces the screen the customer typed on.
                self._session.screen_search = []
            elif normalized.get("view") == "payment_qr":
                self._session.payment = normalized
                self._session.payment_conversation_id = current_conversation_id()
            elif normalized.get("view") == "bill":
                self._session.payment = None
                self._session.payment_conversation_id = ""
            if normalized.get("view") in {"cart", "bill", "payment_qr"} and (
                normalized.get("navigate") is not False
                or normalized.get("view") == "cart"
                or (
                    self._session.cart_snapshot is not None
                    and self._session.cart_snapshot.get("order_id")
                    == normalized.get("order_id")
                )
            ):
                self._session.cart_snapshot = {
                    key: deepcopy(value)
                    for key, value in normalized.items()
                    if key != "navigate"
                }
                self._session.cart_conversation_id = current_conversation_id()
            elif normalized.get("view") == "none":
                self._session.cart_snapshot = None
                self._session.cart_conversation_id = ""
            self._bus.publish(EventType.DISPLAY_UPDATE, normalized)
            return ToolResult(
                tool_name="presentation", content="presentation_published"
            )

    def update_print_status(self, data: dict[str, Any]) -> None:
        """Patch only the owning order's status, without navigation or payment edits."""
        state = data.get("printing_state")
        if state not in {"submitted", "processing", "completed", "failed", "unknown"}:
            return
        with self._lock:
            session = self._session
            if (
                session is None
                or session.session_id != data.get("presentation_session_id")
                or data.get("order_id") not in session.printing_states
            ):
                return
            if (
                session.printing_states[data["order_id"]] == "completed"
                and state != "completed"
            ):
                return
            session.printing_states[data["order_id"]] = state
            if (
                session.payment is not None
                and session.payment.get("order_id") == data["order_id"]
            ):
                session.payment["printing_state"] = state
            changed = False
            for receipt in (session.last_payload, session.cart_snapshot):
                if (
                    receipt is not None
                    and receipt.get("view") in {"bill", "payment_qr"}
                    and receipt.get("order_id") == data["order_id"]
                ):
                    receipt["printing_state"] = state
                    changed = True
            if not changed:
                return
            self._bus.publish(
                EventType.DISPLAY_UPDATE,
                {
                    "view": "print_status",
                    "order_id": data["order_id"],
                    "printing_state": state,
                    "presentation_session_id": session.session_id,
                },
            )

    def activate(self, generation: str) -> bool:
        """Make one native Voice session authoritative for publications."""
        with self._lock:
            self._active_generation = generation
            return True

    def menu_variant(self, session_id: str, variant_id: str) -> dict[str, Any] | None:
        """Return one portion of the menu this display session last showed."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            variant = self._session.menu_variants.get(variant_id)
            return dict(variant) if variant is not None else None

    def active_generation(self, session_id: str) -> str | None:
        """Return the active publication generation for one display session."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            return self._active_generation

    def reset(self, session_id: str, *, generation: str | None = None) -> bool:
        """Replace only the active session's display state with the blank view."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return False
            if generation is not None and self._active_generation != generation:
                return False
            self._active_generation = None
            self._session.menu_variants = {}
            self._session.tables = {}
            self._session.payment = None
            self._session.payment_conversation_id = ""
            self._session.cart_snapshot = None
            self._session.cart_conversation_id = ""
            self._session.printing_states.clear()
            self._session.orders.clear()
            self._session.screen_search = []
            self._session.last_payload = {
                "view": "none",
                "presentation_session_id": self._session.session_id,
            }
            self._bus.publish(EventType.DISPLAY_UPDATE, self._session.last_payload)
            return True

    def replay(self, session_id: str) -> dict[str, Any] | None:
        """Return the active session's most recent display event for reconnects."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return None
            last = self._session.last_payload
            # Touch MENU is browser-local. A later silent add replaces CART,
            # even if the server's last foreground event was an older invoice.
            if last.get("view") in {"cart", "bill", "payment_qr"}:
                last = self._session.cart_snapshot or last
            payload = deepcopy(last)
            payload["presentation_session_id"] = self._session.session_id
            if payload.get("view") == "menu":
                payload["cart_snapshot"] = deepcopy(self._session.cart_snapshot)
            return payload

    def mark_display_connected(self, session_id: str) -> bool:
        """Record that the customer-display WebSocket is connected."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return False
            self._session.display_connected = True
            return True

    def mark_display_disconnected(self, session_id: str) -> bool:
        """Record a display disconnect without discarding its replayable state."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return False
            self._session.display_connected = False
            return True

    def recover_display_tab(self) -> bool:
        """Recreate a missing customer-display page without foregrounding it."""
        with self._lock:
            if self._shared_page:
                # The Human may have intentionally navigated elsewhere.
                return False
            session = self._session
            if session is None:
                return False
            tab_list = _tool_text(self._call_tool("browser_tabs", {"action": "list"}))
            if quote(session.display_url, safe=":/?=&") in tab_list:
                return False
            self._call_tool("browser_navigate", {"url": session.display_url})
            return True

    def _require_client(self) -> Any:
        if self._client is None:
            raise PresentationUnavailableError("presentation_unavailable")
        return self._client

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call Playwright and normalize protocol and transport failures."""
        try:
            result = self._require_client().call_tool(name, arguments)
        except PresentationUnavailableError:
            raise
        except Exception as exc:
            raise PresentationUnavailableError(f"{name} unavailable") from exc
        if not isinstance(result, dict) or result.get("isError"):
            raise PresentationUnavailableError(f"{name} returned an MCP error")
        return result


def _table_order(row: dict[str, str]) -> tuple[bool, int, str]:
    """Numbered tables in numeric order, then any named ones."""
    name = row["name"]
    return (not name.isdigit(), int(name) if name.isdigit() else 0, name)


def _menu_variants(rows: Any) -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        portions = row.get("variants")
        if not isinstance(portions, list):
            portions = [{"id": row.get("id"), "price": row.get("price")}]
        for portion in portions:
            if (
                not isinstance(portion, dict)
                or not isinstance(portion.get("id"), str)
                or type(portion.get("price")) is not int
            ):
                continue
            variants[portion["id"]] = {
                "variant_id": portion["id"],
                "name": row["name"],
                "size": portion.get("size", ""),
                "unit_price": portion["price"],
                "available": row.get("available", True) is True,
            }
    return variants


def _normalize_display_origin(display_origin: str) -> str:
    parsed = urlsplit(display_origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("display_origin must be an http(s) origin without a path")
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


def _tool_text(result: dict[str, Any]) -> str:
    """Keep the current MCP response-shape parsing at this boundary."""
    content = result.get("content", [])
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    )


__all__ = [
    "TouchCheckout",
    "PresentationSession",
    "PresentationSessionManager",
    "PresentationUnavailableError",
    "find_playwright_client",
    "presentation_generation",
]
