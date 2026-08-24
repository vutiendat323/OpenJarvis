"""Customer-display sessions backed by the already-live Playwright MCP client."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Iterable
from urllib.parse import quote, urlsplit
from uuid import uuid4

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult


class PresentationUnavailableError(RuntimeError):
    """Raised when the customer display cannot be backed by Playwright."""


@dataclass(slots=True)
class PresentationSession:
    """State isolated to one customer-display browser page."""

    session_id: str
    display_url: str
    live_tab_index: int
    last_payload: dict[str, Any] = field(default_factory=lambda: {"view": "none"})
    display_connected: bool = True


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
    """Own the one customer-display tab while preserving the live kiosk tab."""

    def __init__(self, bus: EventBus, client: Any | None) -> None:
        self._bus = bus
        self._client = find_playwright_client([client]) if client is not None else None
        self._lock = RLock()
        self._session: PresentationSession | None = None

    def ensure(self, display_origin: str) -> PresentationSession:
        """Create one display tab, returning the existing session on later calls."""
        origin = _normalize_display_origin(display_origin)
        with self._lock:
            if self._session is not None:
                return self._session
            client = self._require_client()
            live_tab_index = _selected_tab_index(
                client.call_tool("browser_tabs", {"action": "list"})
            )
            client.call_tool("browser_tabs", {"action": "new"})
            session_id = uuid4().hex
            session = PresentationSession(
                session_id=session_id,
                display_url=f"{origin}/customer-display?session={session_id}",
                live_tab_index=live_tab_index,
            )
            client.call_tool("browser_navigate", {"url": session.display_url})
            client.call_tool(
                "browser_tabs", {"action": "select", "index": session.live_tab_index}
            )
            self._session = session
            return session

    def publish(self, payload: dict[str, Any]) -> ToolResult:
        """Publish a display event scoped to the active customer session."""
        with self._lock:
            if self._session is None or self._client is None:
                return ToolResult(
                    tool_name="presentation",
                    content="presentation_unavailable",
                    success=False,
                )
            if not self._session.display_connected:
                self.recover_display_tab()
            normalized = deepcopy(payload)
            normalized["presentation_session_id"] = self._session.session_id
            self._session.last_payload = normalized
            self._bus.publish(EventType.DISPLAY_UPDATE, normalized)
            return ToolResult(
                tool_name="presentation", content="presentation_published"
            )

    def reset(self, session_id: str) -> bool:
        """Replace only the active session's display state with the blank view."""
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                return False
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
        """Recreate a missing display page without touching the live page."""
        with self._lock:
            session = self._session
            if session is None:
                return False
            client = self._require_client()
            tab_list = _tool_text(client.call_tool("browser_tabs", {"action": "list"}))
            encoded_url = quote(session.display_url, safe=":/?=&")
            if encoded_url in tab_list:
                return False
            client.call_tool("browser_tabs", {"action": "new"})
            client.call_tool("browser_navigate", {"url": session.display_url})
            client.call_tool(
                "browser_tabs", {"action": "select", "index": session.live_tab_index}
            )
            return True

    def _require_client(self) -> Any:
        if self._client is None:
            raise PresentationUnavailableError("presentation_unavailable")
        return self._client


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


def _selected_tab_index(result: dict[str, Any]) -> int:
    """Parse the selected tab index from the textual Playwright tab listing."""
    text = _tool_text(result)
    for line in text.splitlines():
        if "current" not in line.lower():
            continue
        stripped = line.lstrip("- ").lstrip()
        index, separator, _ = stripped.partition(":")
        if separator and index.isdigit():
            return int(index)
    return 0


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
    "PresentationSession",
    "PresentationSessionManager",
    "PresentationUnavailableError",
    "find_playwright_client",
]
