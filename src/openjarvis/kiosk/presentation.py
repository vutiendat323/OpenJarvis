"""Customer-display sessions backed by the already-live Playwright MCP client."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Iterable, Iterator
from urllib.parse import quote, urlsplit
from uuid import uuid4

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

    def __init__(self, bus: EventBus, client: Any | None) -> None:
        self._bus = bus
        self._client = find_playwright_client([client]) if client is not None else None
        self._lock = RLock()
        self._session: PresentationSession | None = None
        self._active_generation: str | None = None
        self._initial_display: Callable[[], ToolResult] | None = None
        self._touch_checkout: TouchCheckout | None = None
        self._ui_language: str | None = None

    def set_language(self, language: str) -> None:
        """Update active language for customer presentation."""
        with self._lock:
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
        if payment is None:
            return "none"
        if checkout is None:
            raise PresentationUnavailableError("touch_checkout_unavailable")
        url = checkout.order_url.replace(
            "{order_id}", quote(str(payment["order_id"]), safe="")
        )
        try:
            order = checkout.fetch_json(url)["result"]
            status = order["status"]
        except Exception as exc:
            raise PresentationUnavailableError("order_unavailable") from exc
        if status != "paid":
            return status if isinstance(status, str) else "unknown"
        with self._lock:
            # A concurrent poll may already have shown this bill.
            if self._session is None or self._session.payment is not payment:
                return "none"
        self.publish(
            {
                "view": "bill",
                "order_id": payment["order_id"],
                "status": "paid",
                "order_type": payment.get("order_type", ""),
                "branch": payment.get("branch", ""),
                "lines": payment.get("lines", []),
                "total": payment.get("total", 0),
            }
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
            self._call_tool("browser_navigate", {"url": session.display_url})
            self._session = session
            return session

    def publish(self, payload: dict[str, Any]) -> ToolResult:
        """Publish a display event scoped to the active customer session."""
        from openjarvis.agents._stubs import check_agent_cancelled

        with self._lock:
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
                self.recover_display_tab()
            check_agent_cancelled()
            normalized = deepcopy(payload)
            normalized["presentation_session_id"] = self._session.session_id
            if normalized.get("navigate") is not False:
                self._session.last_payload = normalized
            elif self._session.last_payload.get("view") == "cart":
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
            elif normalized.get("view") == "bill":
                self._session.payment = None
            self._bus.publish(EventType.DISPLAY_UPDATE, normalized)
            return ToolResult(
                tool_name="presentation", content="presentation_published"
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
            payload = deepcopy(self._session.last_payload)
            payload["presentation_session_id"] = self._session.session_id
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
