"""Contract tests for the kiosk customer-display presentation session."""

from __future__ import annotations

import pytest

from openjarvis.core.events import EventBus, EventType
from openjarvis.kiosk.presentation import (
    PresentationSessionManager,
    PresentationUnavailableError,
    find_playwright_client,
)


class _FakeMCPClient:
    """Deterministic boundary double for the existing Playwright MCP client."""

    def __init__(self, *, server_name: str = "") -> None:
        self._server_name = server_name
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.tab_list_text = "0: http://127.0.0.1:5173/kiosk"

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        if name == "browser_tabs" and arguments == {"action": "list"}:
            return {"content": [{"type": "text", "text": self.tab_list_text}]}
        return {"content": []}


@pytest.fixture
def bus() -> EventBus:
    return EventBus(record_history=True)


def test_ensure_creates_display_tab_once_and_reselects_live_tab(bus: EventBus) -> None:
    """Would fail if bootstrap creates a second tab or leaves it selected."""
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)

    first = manager.ensure("http://127.0.0.1:5173")
    second = manager.ensure("http://127.0.0.1:5173")

    assert second is first
    assert client.calls == [
        ("browser_tabs", {"action": "list"}),
        ("browser_tabs", {"action": "new"}),
        ("browser_navigate", {"url": first.display_url}),
        ("browser_tabs", {"action": "select", "index": first.live_tab_index}),
    ]


def test_find_playwright_client_uses_only_the_playwright_server_name() -> None:
    """Would fail if an unrelated MCP client could own the display tab."""
    other = _FakeMCPClient(server_name="maps")
    playwright = _FakeMCPClient(server_name="playwright")

    assert find_playwright_client([other, playwright]) is playwright
    assert find_playwright_client([other]) is None


def test_ensure_without_a_playwright_client_is_unavailable(bus: EventBus) -> None:
    """Would fail if browser work were attempted without the required client."""
    with pytest.raises(PresentationUnavailableError):
        PresentationSessionManager(bus, None).ensure("http://127.0.0.1:5173")


@pytest.mark.parametrize(
    "origin",
    ["ftp://127.0.0.1:5173", "http://127.0.0.1:5173/kiosk", "http:///display"],
)
def test_ensure_rejects_invalid_origins_before_browser_calls(
    bus: EventBus, origin: str
) -> None:
    """Would fail if invalid display URLs reached Playwright."""
    client = _FakeMCPClient(server_name="playwright")

    with pytest.raises(ValueError):
        PresentationSessionManager(bus, client).ensure(origin)

    assert client.calls == []


def test_publish_scopes_and_replays_only_the_active_session(bus: EventBus) -> None:
    """Would fail if replay lost the display payload's session boundary."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")

    result = manager.publish({"view": "menu", "items": [{"id": "latte"}]})

    assert result.success is True
    assert manager.replay(session.session_id) == {
        "view": "menu",
        "items": [{"id": "latte"}],
        "presentation_session_id": session.session_id,
    }
    assert bus.history[-1].event_type == EventType.DISPLAY_UPDATE
    assert bus.history[-1].data == manager.replay(session.session_id)


def test_publish_without_an_active_session_returns_unavailable(bus: EventBus) -> None:
    """Would fail if display events could leak without a customer session."""
    result = PresentationSessionManager(
        bus, _FakeMCPClient(server_name="playwright")
    ).publish({"view": "menu"})

    assert result.success is False
    assert result.content == "presentation_unavailable"
    assert bus.history == []


def test_reset_clears_previous_customer_state(bus: EventBus) -> None:
    """Would fail if a new customer could replay the previous customer's cart."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    manager.publish({"view": "cart", "lines": [], "total": 0})

    assert manager.reset(session.session_id) is True
    assert manager.replay(session.session_id)["view"] == "none"


def test_publish_recovers_a_disconnected_display_tab_without_navigating_live_tab(
    bus: EventBus,
) -> None:
    """Would fail if reconnect recovery repurposed the live kiosk tab."""
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    client.tab_list_text = "0: http://127.0.0.1:5173/kiosk"
    calls_before_recovery = len(client.calls)

    manager.publish({"view": "menu"})

    assert client.calls[calls_before_recovery:] == [
        ("browser_tabs", {"action": "list"}),
        ("browser_tabs", {"action": "new"}),
        ("browser_navigate", {"url": session.display_url}),
        ("browser_tabs", {"action": "select", "index": session.live_tab_index}),
    ]


def test_mark_display_connected_skips_recovery_when_the_page_reconnects(
    bus: EventBus,
) -> None:
    """Would fail if a connected display caused needless browser tab churn."""
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    manager.mark_display_connected(session.session_id)
    calls_before_publish = len(client.calls)

    manager.publish({"view": "menu"})

    assert client.calls[calls_before_publish:] == []
