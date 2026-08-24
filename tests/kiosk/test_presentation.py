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
        self.failures: dict[tuple[str, str | None], Exception | str] = {}

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        action = arguments.get("action")
        failure = self.failures.get(
            (name, action if isinstance(action, str) else None)
        )
        if isinstance(failure, Exception):
            raise failure
        if failure == "is_error":
            return {"content": [], "isError": True}
        if name == "browser_tabs" and arguments == {"action": "list"}:
            return {"content": [{"type": "text", "text": self.tab_list_text}]}
        return {"content": []}


@pytest.fixture
def bus() -> EventBus:
    return EventBus(record_history=True)


def test_ensure_creates_display_tab_once_and_reselects_live_tab(bus: EventBus) -> None:
    """Would fail if bootstrap creates a second tab or leaves it selected."""
    client = _FakeMCPClient(server_name="playwright")
    client.tab_list_text = "0: http://127.0.0.1:5173/kiosk\n2: (current) kiosk home"
    manager = PresentationSessionManager(bus, client)

    first = manager.ensure("http://127.0.0.1:5173")
    second = manager.ensure("http://127.0.0.1:5173")

    assert second is first
    assert first.live_tab_index == 2
    assert client.calls == [
        ("browser_tabs", {"action": "list"}),
        ("browser_tabs", {"action": "new"}),
        ("browser_navigate", {"url": first.display_url}),
        ("browser_tabs", {"action": "select", "index": first.live_tab_index}),
    ]


def test_ensure_reselects_the_live_tab_when_display_navigation_raises(
    bus: EventBus,
) -> None:
    """Would fail if a transient navigation error left the new tab selected."""
    client = _FakeMCPClient(server_name="playwright")
    client.tab_list_text = "3: (current) kiosk home"
    client.failures[("browser_navigate", None)] = RuntimeError("navigation failed")
    manager = PresentationSessionManager(bus, client)

    with pytest.raises(PresentationUnavailableError) as exc_info:
        manager.ensure("http://127.0.0.1:5173")

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert client.calls[-1] == ("browser_tabs", {"action": "select", "index": 3})


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("browser_tabs", {"action": "list"}),
        ("browser_tabs", {"action": "new"}),
        ("browser_navigate", {"url": "ignored"}),
        ("browser_tabs", {"action": "select", "index": 0}),
    ],
)
@pytest.mark.parametrize("failure", ["is_error", RuntimeError("transport failed")])
def test_ensure_rejects_each_mcp_lifecycle_failure_without_committing_a_session(
    bus: EventBus,
    tool_name: str,
    arguments: dict[str, object],
    failure: Exception | str,
) -> None:
    """Would fail if a failed bootstrap operation became an active session."""
    client = _FakeMCPClient(server_name="playwright")
    action = arguments.get("action")
    client.failures[(tool_name, action if isinstance(action, str) else None)] = failure
    manager = PresentationSessionManager(bus, client)

    with pytest.raises(PresentationUnavailableError):
        manager.ensure("http://127.0.0.1:5173")

    result = manager.publish({"view": "menu"})
    assert result.success is False
    assert result.content == "presentation_unavailable"
    if tool_name == "browser_tabs" and arguments == {"action": "list"}:
        assert client.calls == [("browser_tabs", {"action": "list"})]
    else:
        assert client.calls[-1] == ("browser_tabs", {"action": "select", "index": 0})


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


def test_publish_fails_safely_when_recovery_tab_listing_is_an_mcp_error(
    bus: EventBus,
) -> None:
    """Would fail if a failed tab listing created a duplicate display tab."""
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    client.failures[("browser_tabs", "list")] = "is_error"
    calls_before_recovery = len(client.calls)

    with pytest.raises(PresentationUnavailableError, match="browser_tabs"):
        manager.publish({"view": "menu"})

    assert client.calls[calls_before_recovery:] == [
        ("browser_tabs", {"action": "list"}),
        ("browser_tabs", {"action": "select", "index": session.live_tab_index}),
    ]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("browser_tabs", {"action": "list"}),
        ("browser_tabs", {"action": "new"}),
        ("browser_navigate", {"url": "ignored"}),
        ("browser_tabs", {"action": "select", "index": 0}),
    ],
)
@pytest.mark.parametrize("failure", ["is_error", RuntimeError("transport failed")])
def test_recovery_rejects_each_mcp_lifecycle_failure_without_publishing(
    bus: EventBus,
    tool_name: str,
    arguments: dict[str, object],
    failure: Exception | str,
) -> None:
    """Would fail if a failed recovery operation emitted a display update."""
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    action = arguments.get("action")
    client.failures[(tool_name, action if isinstance(action, str) else None)] = failure
    calls_before_recovery = len(client.calls)

    with pytest.raises(PresentationUnavailableError):
        manager.publish({"view": "menu"})

    assert bus.history == []
    assert manager.replay(session.session_id) == {
        "view": "none",
        "presentation_session_id": session.session_id,
    }
    if tool_name == "browser_tabs" and arguments == {"action": "list"}:
        assert client.calls[calls_before_recovery:] == [
            ("browser_tabs", {"action": "list"}),
            ("browser_tabs", {"action": "select", "index": session.live_tab_index}),
        ]
    else:
        assert client.calls[-1] == (
            "browser_tabs",
            {"action": "select", "index": session.live_tab_index},
        )


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
