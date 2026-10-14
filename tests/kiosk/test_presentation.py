"""Contract tests for the kiosk customer-display presentation session."""

from __future__ import annotations

import asyncio
import json

import pytest

import openjarvis.kiosk.presentation as presentation_module
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult
from openjarvis.kiosk.presentation import (
    PresentationSessionManager,
    PresentationUnavailableError,
    TouchCheckout,
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
        failure = self.failures.get((name, action if isinstance(action, str) else None))
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


def test_cancelled_worker_cannot_publish_a_late_display(bus):
    from openjarvis.agents._stubs import _RUN_WORKER_LEASE, AgentWorkerLease

    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    lease = AgentWorkerLease()
    token = _RUN_WORKER_LEASE.set(lease)
    try:
        lease._signal_cancelled()
        with pytest.raises(asyncio.CancelledError):
            manager.publish({"view": "payment_qr", "text": "stale"})
    finally:
        _RUN_WORKER_LEASE.reset(token)
    assert session.last_payload == {"view": "none"}
    assert not any(e.event_type == EventType.DISPLAY_UPDATE for e in bus.history)


def test_a_dispatched_tool_publishes_after_its_run_was_cancelled(bus):
    """Spec 2026-10-10 D2: a dispatched tool finishes its display after a barge-in."""
    from openjarvis.agents._stubs import _RUN_WORKER_LEASE, AgentWorkerLease
    from openjarvis.tools._stubs import _IN_TOOL

    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    manager.ensure("http://127.0.0.1:5173")
    lease = AgentWorkerLease()
    lease_token = _RUN_WORKER_LEASE.set(lease)
    tool_token = _IN_TOOL.set(True)
    try:
        lease._signal_cancelled()
        result = manager.publish({"view": "payment_qr", "text": "kept"})
    finally:
        _IN_TOOL.reset(tool_token)
        _RUN_WORKER_LEASE.reset(lease_token)
    assert result.success
    assert any(e.event_type == EventType.DISPLAY_UPDATE for e in bus.history)


def test_ensure_navigates_the_display_tab_without_creating_a_blank_tab(
    bus: EventBus,
) -> None:
    """Would fail if the kiosk created a visible about:blank tab for customers."""
    client = _FakeMCPClient(server_name="playwright")
    client.tab_list_text = "0: (current) about:blank"
    manager = PresentationSessionManager(bus, client)

    first = manager.ensure("http://127.0.0.1:5173")
    client.tab_list_text = f"0: (current) {first.display_url}"
    second = manager.ensure("http://127.0.0.1:5173")

    assert second is first
    assert client.calls == [
        ("browser_navigate", {"url": first.display_url}),
        ("browser_tabs", {"action": "list"}),
    ]


def test_shared_page_presentation_never_hijacks_external_navigation(bus) -> None:
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client, shared_page=True)
    session = manager.ensure("http://127.0.0.1:5173")
    assert client.calls == [("browser_navigate", {"url": session.display_url})]

    client.calls.clear()
    manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    manager.publish({"view": "cart", "items": [], "navigate": False})

    assert client.calls == []

    manager.publish({"view": "menu", "items": []})
    assert client.calls == [("browser_navigate", {"url": session.display_url})]


def test_initial_display_loads_once_and_cannot_overwrite_a_new_voice_turn(
    bus: EventBus,
) -> None:
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    calls: list[str] = []

    def load() -> ToolResult:
        calls.append("load")
        manager.publish({"view": "menu", "items": [{"id": "latte"}]})
        return ToolResult(tool_name="skill_menu", content="shown", success=True)

    manager.configure_initial_display(load)

    assert manager.preload_initial_display() is True
    assert manager.preload_initial_display() is False
    assert calls == ["load"]
    assert manager.replay(session.session_id)["view"] == "menu"

    manager.activate("voice-1")
    assert manager.preload_initial_display() is False


def test_active_session_reloads_the_initial_menu_after_reset(bus: EventBus) -> None:
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    calls: list[str] = []

    def load() -> ToolResult:
        calls.append("load")
        manager.publish(
            {
                "view": "menu",
                "items": [{"name": "Cà phê sữa"}],
                "result_complete": True,
                "projected_count": 1,
                "published_count": 1,
            }
        )
        return ToolResult(tool_name="skill_menu", content="shown", success=True)

    manager.configure_initial_display(load)
    assert manager.preload_initial_display() is True
    assert manager.reset(session.session_id) is True
    assert manager.activate("voice-1") is True

    assert manager.load_initial_display() is True
    assert calls == ["load", "load"]
    assert manager.replay(session.session_id)["view"] == "menu"


def test_initial_display_logs_a_recipe_failure(bus: EventBus, caplog) -> None:
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    manager.ensure("http://127.0.0.1:5173")
    manager.configure_initial_display(
        lambda: ToolResult(
            tool_name="skill_menu", content="recipe_stale", success=False
        )
    )

    assert manager.preload_initial_display() is False
    assert "Initial display recipe failed." in caplog.messages


def test_kiosk_lifecycle_never_foregrounds_the_display_window(
    bus: EventBus,
) -> None:
    """Would fail if the kiosk stole OS focus for the customer display.

    Playwright MCP's ``browser_tabs select`` calls ``page.bringToFront()``,
    which raises the Chromium window over the operator's browser.
    """
    client = _FakeMCPClient(server_name="playwright")
    client.tab_list_text = "0: (current) about:blank"
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    client.tab_list_text = f"0: (current) {session.display_url}"

    for _kiosk_state in ("approaching", "prompting", "active"):
        assert manager.ensure("http://127.0.0.1:5173") is session
    assert manager.activate("voice-2") is True
    manager.mark_display_disconnected(session.session_id)
    client.tab_list_text = "0: (current) about:blank"
    assert manager.publish({"view": "menu"}).success is True

    assert ("browser_navigate", {"url": session.display_url}) in client.calls
    assert not any(
        name == "browser_tabs" and arguments.get("action") == "select"
        for name, arguments in client.calls
    )


@pytest.mark.parametrize("failure", ["is_error", RuntimeError("transport failed")])
def test_ensure_rejects_a_navigation_failure_without_committing_a_session(
    bus: EventBus,
    failure: Exception | str,
) -> None:
    """Would fail if a failed bootstrap operation became an active session."""
    client = _FakeMCPClient(server_name="playwright")
    client.failures[("browser_navigate", None)] = failure
    manager = PresentationSessionManager(bus, client)

    with pytest.raises(PresentationUnavailableError):
        manager.ensure("http://127.0.0.1:5173")

    result = manager.publish({"view": "menu"})
    assert result.success is False
    assert result.content == "presentation_unavailable"
    assert [name for name, _ in client.calls] == ["browser_navigate"]


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


def test_published_menu_prices_touch_orders_until_the_session_resets(
    bus: EventBus,
) -> None:
    """Would fail if a tap could order a portion the customer was never shown."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    manager.publish(
        {
            "view": "menu",
            "items": [],
            "menu_items": [
                {
                    "id": "v-latte",
                    "name": "Latte",
                    "price": 45000,
                    "available": True,
                    "variants": [
                        {"id": "v-latte", "size": "nhỏ", "price": 45000},
                        {"id": "v-latte-l", "size": "lớn", "price": 55000},
                    ],
                },
                {"id": "v-tea", "name": "Trà", "price": 30000, "available": False},
                {"name": "No identity", "price": 1},
            ],
        }
    )
    manager.publish({"view": "cart", "lines": [], "total": 0})

    assert manager.menu_variant(session.session_id, "v-latte-l") == {
        "variant_id": "v-latte-l",
        "name": "Latte",
        "size": "lớn",
        "unit_price": 55000,
        "available": True,
    }
    assert manager.menu_variant(session.session_id, "v-tea") == {
        "variant_id": "v-tea",
        "name": "Trà",
        "size": "",
        "unit_price": 30000,
        "available": False,
    }
    assert manager.menu_variant(session.session_id, "v-unknown") is None
    assert manager.menu_variant("another-session", "v-latte") is None

    assert manager.reset(session.session_id) is True
    assert manager.menu_variant(session.session_id, "v-latte") is None


def test_publish_without_an_active_session_returns_unavailable(bus: EventBus) -> None:
    """Would fail if display events could leak without a customer session."""
    result = PresentationSessionManager(
        bus, _FakeMCPClient(server_name="playwright")
    ).publish({"view": "menu"})

    assert result.success is False
    assert result.content == "presentation_unavailable"
    assert bus.history == []


def test_voice_generation_can_activate_before_display_ensure(bus: EventBus) -> None:
    """Would fail if non-blocking Voice start outran presentation bootstrap."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))

    assert manager.activate("thread-1") is True
    session = manager.ensure("http://127.0.0.1:5173")
    with presentation_module.presentation_generation("thread-1"):
        result = manager.publish({"view": "menu", "items": [{"id": "latte"}]})

    assert result.success is True
    assert manager.active_generation(session.session_id) == "thread-1"


def test_reset_clears_previous_customer_state(bus: EventBus) -> None:
    """Would fail if a new customer could replay the previous customer's cart."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    manager.publish({"view": "cart", "lines": [], "total": 0})

    assert manager.reset(session.session_id) is True
    assert manager.replay(session.session_id)["view"] == "none"


def test_publish_recovers_a_disconnected_display_tab(
    bus: EventBus,
) -> None:
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    client.tab_list_text = "0: http://127.0.0.1:5173/kiosk"
    calls_before_recovery = len(client.calls)

    manager.publish({"view": "menu"})

    assert client.calls[calls_before_recovery:] == [
        ("browser_tabs", {"action": "list"}),
        ("browser_navigate", {"url": session.display_url}),
    ]


def test_ensure_recovers_a_disconnected_display_without_creating_another_tab(
    bus: EventBus,
) -> None:
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    manager.mark_display_disconnected(session.session_id)
    client.tab_list_text = "0: (current) about:blank"
    calls_before_recovery = len(client.calls)

    recovered = manager.ensure("http://127.0.0.1:5173")

    assert recovered is session
    assert client.calls[calls_before_recovery:] == [
        ("browser_tabs", {"action": "list"}),
        ("browser_navigate", {"url": session.display_url}),
    ]


def test_ensure_recovers_when_the_tab_closes_before_websocket_disconnect(
    bus: EventBus,
) -> None:
    client = _FakeMCPClient(server_name="playwright")
    manager = PresentationSessionManager(bus, client)
    session = manager.ensure("http://127.0.0.1:5173")
    client.tab_list_text = "0: (current) about:blank"
    calls_before_recovery = len(client.calls)

    recovered = manager.ensure("http://127.0.0.1:5173")

    assert recovered is session
    assert client.calls[calls_before_recovery:] == [
        ("browser_tabs", {"action": "list"}),
        ("browser_navigate", {"url": session.display_url}),
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
    ]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("browser_tabs", {"action": "list"}),
        ("browser_navigate", {"url": "ignored"}),
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
    expected = ["browser_tabs"]
    if tool_name == "browser_navigate":
        expected.append("browser_navigate")
    assert [name for name, _ in client.calls[calls_before_recovery:]] == expected


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


class _Merchant:
    """Read-only merchant double: URL -> JSON, recording every request."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.requests: list[str] = []

    def __call__(self, url: str) -> object:
        self.requests.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def _touch_manager(bus: EventBus, merchant: _Merchant):
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    manager.configure_touch_checkout(
        TouchCheckout(
            run=lambda **_: ToolResult(tool_name="checkout", content="ok"),
            tables_url="https://merchant.example/tables",
            order_url="https://merchant.example/orders/{order_id}",
            fetch_json=merchant,
        )
    )
    return manager, manager.ensure("http://127.0.0.1:5173")


def test_live_tables_are_trimmed_ordered_and_remembered_per_session(
    bus: EventBus,
) -> None:
    """Would fail if the picker showed stale, unordered, or unverifiable tables."""
    merchant = _Merchant(
        {
            "https://merchant.example/tables": {
                "result": [
                    {"slug": "t10", "name": "10", "status": "available"},
                    {"slug": "t2", "name": "2", "status": "reserved", "xPosition": 4},
                    {"slug": "t1", "name": "1", "status": "available"},
                    {"slug": "", "name": "broken", "status": "available"},
                ]
            }
        }
    )
    manager, session = _touch_manager(bus, merchant)

    tables = manager.live_tables(session.session_id)

    assert tables == [
        {"slug": "t1", "name": "1", "status": "available"},
        {"slug": "t2", "name": "2", "status": "reserved"},
        {"slug": "t10", "name": "10", "status": "available"},
    ]
    assert manager.table(session.session_id, "t2") == tables[1]
    assert manager.table(session.session_id, "t404") is None
    assert manager.live_tables("another-session") is None
    assert manager.table("another-session", "t2") is None
    manager.reset(session.session_id)
    assert manager.table(session.session_id, "t2") is None


def test_live_tables_fail_closed_when_the_merchant_is_unreachable(
    bus: EventBus,
) -> None:
    merchant = _Merchant({"https://merchant.example/tables": OSError("offline")})
    manager, session = _touch_manager(bus, merchant)

    with pytest.raises(PresentationUnavailableError):
        manager.live_tables(session.session_id)
    assert manager.table(session.session_id, "t1") is None


def test_payment_check_shows_the_paid_bill_once_the_merchant_confirms(
    bus: EventBus,
    monkeypatch,
) -> None:
    """Would fail if a pending order looked paid or a paid one stayed on the QR."""
    from openjarvis.agents.action_ledger import ActionLedger
    from openjarvis.core.conversation import conversation_scope

    ledger = ActionLedger()
    monkeypatch.setattr(presentation_module, "ACTION_LEDGER", ledger, raising=False)
    order_url = "https://merchant.example/orders/order%2F1"
    merchant = _Merchant(
        {order_url: {"result": {"slug": "order/1", "status": "pending"}}}
    )
    manager, session = _touch_manager(bus, merchant)
    assert manager.check_payment(session.session_id) == "none"
    line = {"name": "Latte", "quantity": 1, "unit_price": 45000, "line_total": 45000}
    with conversation_scope("payment-owner"):
        manager.publish(
            {
                "view": "payment_qr",
                "order_id": "order/1",
                "payment_slug": "payment-1",
                "status": "pending",
                "qr_code": "qr",
                "order_type": "take-out",
                "branch": "branch-1",
                "lines": [line],
                "total": 45000,
            }
        )

    pending = manager.check_payment(session.session_id)
    assert ledger.entries("payment-owner") == []
    merchant.responses[order_url] = {"result": {"slug": "order/1", "status": "paid"}}
    paid = manager.check_payment(session.session_id)
    after = manager.check_payment(session.session_id)

    assert (pending, paid, after) == ("pending", "paid", "none")
    [notice] = ledger.unreported_finished("payment-owner")
    assert notice.customer_message == ""
    assert notice.tool == "payment_completed" and notice.status == "succeeded"
    assert json.loads(notice.as_context()["event_context"]) == {
        "event": "payment_completed",
        "order_id": "order/1",
        "payment_status": "paid",
        "view": "bill",
    }
    assert merchant.requests == [order_url, order_url]
    assert manager.check_payment("another-session") is None
    bills = [
        event.data
        for event in bus.history
        if event.event_type == EventType.DISPLAY_UPDATE and event.data["view"] == "bill"
    ]
    assert bills == [
        {
            "view": "bill",
            "order_id": "order/1",
            "status": "paid",
            "order_type": "take-out",
            "branch": "branch-1",
            "lines": [line],
            "total": 45000,
            "presentation_session_id": session.session_id,
        }
    ]


def test_wrong_order_payment_never_confirms_or_notifies(bus, monkeypatch):
    from openjarvis.agents.action_ledger import ActionLedger
    from openjarvis.core.conversation import conversation_scope

    ledger = ActionLedger()
    monkeypatch.setattr(presentation_module, "ACTION_LEDGER", ledger, raising=False)
    merchant = _Merchant(
        {
            "https://merchant.example/orders/order-1": {
                "result": {"slug": "another-order", "status": "paid"}
            }
        }
    )
    manager, session = _touch_manager(bus, merchant)
    with conversation_scope("payment-owner"):
        manager.publish({"view": "payment_qr", "order_id": "order-1"})

    with pytest.raises(PresentationUnavailableError, match="order"):
        manager.check_payment(session.session_id)

    assert ledger.entries("payment-owner") == []
    assert session.last_payload["view"] == "payment_qr"


def test_paid_receipt_replay_does_not_repeat_notice_to_another_voice(bus, monkeypatch):
    from openjarvis.agents.action_ledger import ActionLedger
    from openjarvis.core.conversation import conversation_scope

    ledger = ActionLedger()
    monkeypatch.setattr(presentation_module, "ACTION_LEDGER", ledger, raising=False)
    merchant = _Merchant(
        {
            "https://merchant.example/orders/order-1": {
                "result": {"slug": "order-1", "status": "paid"}
            }
        }
    )
    manager, session = _touch_manager(bus, merchant)
    receipt = {"view": "payment_qr", "order_id": "order-1"}
    with conversation_scope("payment-owner"):
        manager.publish(receipt)
    with conversation_scope("poll-request"):
        assert manager.check_payment(session.session_id) == "paid"
    [first] = ledger.unreported_finished("payment-owner")
    ledger.mark_reported("payment-owner", first.key, via="test")
    with conversation_scope("payment-owner"):
        manager.publish(receipt)
    assert manager.check_payment(session.session_id) == "paid"

    assert ledger.unreported_finished("payment-owner") == []
    assert len(ledger.entries("payment-owner")) == 1
    assert ledger.entries("poll-request") == []


def test_screen_search_exposes_only_shown_menu_rows_until_the_menu_changes(
    bus: EventBus,
) -> None:
    """Would fail if typed results could name items the display never showed,
    or outlived the menu they were typed over."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    menu = {
        "view": "menu",
        "items": [],
        "menu_items": [
            {"id": "v-latte", "name": "Latte", "price": 55000, "available": True},
            {"id": "v-tea", "name": "Trà", "price": 30000, "available": False},
        ],
    }
    manager.publish(menu)

    shared = manager.share_screen_search(
        session.session_id, ["v-tea", "v-invented", "v-latte", "v-tea"]
    )
    rows = manager.screen_search()

    assert shared == 2
    assert rows == [
        {"id": "v-tea", "name": "Trà", "price": 30000, "available": False},
        {"id": "v-latte", "name": "Latte", "price": 55000, "available": True},
    ]
    assert manager.share_screen_search("another-session", ["v-latte"]) is None
    assert manager.share_screen_search(session.session_id, []) == 0
    assert manager.screen_search() == []

    manager.share_screen_search(session.session_id, ["v-latte"])
    manager.publish(menu)
    assert manager.screen_search() == []
    manager.share_screen_search(session.session_id, ["v-latte"])
    manager.reset(session.session_id)
    assert manager.screen_search() == []


def test_a_background_cart_update_keeps_the_screen_a_reconnect_replays(
    bus: EventBus,
) -> None:
    """Would fail if a silent "add to cart" made a reconnecting display jump to
    the cart, or left an open cart receipt stale."""
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    menu = {"view": "menu", "items": [], "menu_items": []}
    cart = {"view": "cart", "lines": [], "total": 45000}
    manager.publish(menu)

    manager.publish({**cart, "navigate": False})
    on_menu = manager.replay(session.session_id)
    manager.publish(cart)
    manager.publish({**cart, "total": 90000, "navigate": False})
    on_cart = manager.replay(session.session_id)

    assert on_menu["view"] == "menu"
    assert (on_cart["view"], on_cart["total"]) == ("cart", 90000)
    assert "navigate" not in on_cart
    assert [event.data.get("navigate") for event in bus.history][-1] is False


def test_set_language_never_waits_for_the_presentation_lock(bus):
    """A route runs it on the event loop: ``ensure`` holds the lock across a
    browser call that needs that same loop to answer, so waiting here froze the
    whole server (2026-10-11 live: /health timed out until restart)."""
    import threading

    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    held, release = threading.Event(), threading.Event()

    def hold() -> None:
        with manager._lock:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert held.wait(2)
    done = threading.Event()
    threading.Thread(
        target=lambda: (manager.set_language("en"), done.set()), daemon=True
    ).start()
    try:
        assert done.wait(1), "set_language blocked on the presentation lock"
    finally:
        release.set()
        holder.join(2)
    assert manager._ui_language == "en"


def test_a_slow_first_navigation_does_not_hold_the_state_lock(bus):
    """The display page connects back to this process while ``ensure`` is still
    waiting for it to load. Holding the lock across that wait made every route
    that touched the manager (on the event loop) block, so the page never
    loaded and the whole server froze (2026-10-11 live)."""
    import threading

    client = _FakeMCPClient(server_name="playwright")
    navigating, release = threading.Event(), threading.Event()
    original = client.call_tool

    def slow(name, arguments):
        if name == "browser_navigate":
            navigating.set()
            assert release.wait(5)
        return original(name, arguments)

    client.call_tool = slow  # type: ignore[method-assign]
    manager = PresentationSessionManager(bus, client)
    result: dict[str, object] = {}
    ensuring = threading.Thread(
        target=lambda: result.update(session=manager.ensure("http://127.0.0.1:5173")),
        daemon=True,
    )
    ensuring.start()
    assert navigating.wait(2)

    probes: dict[str, object] = {}
    probe = threading.Thread(
        target=lambda: (
            probes.update(replay=manager.replay("x")),
            manager.activate("generation-1"),
            probes.update(done=True),
        ),
        daemon=True,
    )
    probe.start()
    probe.join(1)
    try:
        assert probes.get("done") is True, "state accessors waited on ensure()"
    finally:
        release.set()
        ensuring.join(2)
    session = result["session"]
    assert manager.replay(session.session_id) is not None  # type: ignore[attr-defined]


def test_a_failed_first_navigation_leaves_no_session(bus):
    client = _FakeMCPClient(server_name="playwright")
    client.failures[("browser_navigate", None)] = RuntimeError("boom")
    manager = PresentationSessionManager(bus, client)
    with pytest.raises(PresentationUnavailableError):
        manager.ensure("http://127.0.0.1:5173")
    assert manager._session is None


def test_shown_payment_is_only_for_the_conversation_that_showed_it(bus):
    from openjarvis.core.conversation import conversation_scope

    manager, _ = _touch_manager(bus, _Merchant({}))
    with conversation_scope("payment-owner"):
        manager.publish(
            {
                "view": "payment_qr",
                "order_id": "order-1",
                "status": "pending",
                "total": 35000,
                "qr_code": "large-qr-value",
            }
        )
        shown = manager.shown_payment()
    with conversation_scope("someone-else"):
        other = manager.shown_payment()

    assert shown == {"order_id": "order-1", "status": "pending", "total": 35000}
    assert other is None
    assert manager.shown_payment() is None  # outside any conversation


def test_print_status_is_scoped_replayed_and_never_reverts_completion(bus):
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    manager.publish(
        {
            "view": "bill",
            "order_id": "order-1",
            "status": "paid",
            "lines": [],
            "total": 35000,
        }
    )
    update = {
        "order_id": "order-1",
        "presentation_session_id": session.session_id,
        "printing_state": "completed",
    }
    manager.update_print_status({**update, "order_id": "another-order"})
    assert "printing_state" not in manager.replay(session.session_id)
    manager.update_print_status(
        {**update, "presentation_session_id": "another-display"}
    )
    assert "printing_state" not in manager.replay(session.session_id)
    manager.update_print_status(update)
    manager.update_print_status({**update, "printing_state": "submitted"})
    replay = manager.replay(session.session_id)
    assert replay["status"] == "paid"
    assert replay["printing_state"] == "completed"
    assert replay["view"] == "bill"
    assert replay["total"] == 35000


@pytest.mark.parametrize("navigate_away", [False, True])
def test_print_completion_survives_invoice_redisplay(bus, navigate_away):
    manager = PresentationSessionManager(bus, _FakeMCPClient(server_name="playwright"))
    session = manager.ensure("http://127.0.0.1:5173")
    bill = {"view": "bill", "order_id": "order-1", "status": "paid", "total": 35000}
    manager.publish(bill)
    if navigate_away:
        manager.publish({"view": "menu", "menu_items": []})
    calls = len(manager._client.calls)
    manager.update_print_status(
        {
            "order_id": "order-1",
            "presentation_session_id": session.session_id,
            "printing_state": "completed",
        }
    )
    assert len(manager._client.calls) == calls
    if navigate_away:
        assert manager.replay(session.session_id)["view"] == "menu"
    manager.publish(bill)
    replay = manager.replay(session.session_id)
    assert replay["printing_state"] == "completed"
    assert replay["status"] == "paid"
    assert replay["total"] == 35000
    manager.reset(session.session_id)
    manager.publish(bill)
    assert "printing_state" not in manager.replay(session.session_id)
