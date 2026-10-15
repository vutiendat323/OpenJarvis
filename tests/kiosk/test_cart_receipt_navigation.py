"""Browsing and viewing CART must not begin another editable order."""

import json

import pytest
from tests.tools.test_display import _BlankPlaywright

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.kiosk.presentation import PresentationSessionManager, TouchCheckout
from openjarvis.tools.display import DisplayCartTool


@pytest.fixture
def display():
    manager = PresentationSessionManager(
        EventBus(record_history=True), _BlankPlaywright()
    )
    session = manager.ensure("http://localhost:5173")
    cart = DisplayCartTool()
    cart._presentation = manager
    return manager, session, cart


def _receipt(manager, *, view="payment_qr", order_id="coffee-order"):
    payload = {
        "view": view,
        "order_id": order_id,
        "status": "pending" if view == "payment_qr" else "paid",
        "payment_slug": "coffee-payment",
        "qr_code": "verified-qr",
        "created_at": "2026-10-15T02:15:00Z",
        "lines": [{"name": "Black coffee", "quantity": 1, "line_total": 35000}],
        "total": 35000,
        "order_type": "take-out",
        "branch": "",
    }
    assert manager.publish(payload).success
    return payload


@pytest.mark.parametrize("view", ["payment_qr", "bill"])
def test_voice_cart_view_restores_latest_invoice_without_creating_a_draft(
    display, view
):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        original = _receipt(manager, view=view)
        assert manager.publish({"view": "menu", "items": []}).success
        result = cart.execute(action="view", customer_message="Đây là đơn của bạn.")

        assert result.success
        assert json.loads(result.content)["shown"] == view
        assert result.metadata["completed_display"] is True
        assert "qr_code" not in result.content
        assert cart.current_snapshot()["revision"] == 0
        assert "customer-1" not in cart._carts
    assert session.last_payload["order_id"] == original["order_id"]
    assert session.last_payload["created_at"] == original["created_at"]


def test_menu_replay_retains_latest_invoice(display):
    manager, session, _ = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        _receipt(manager, order_id="cake-order")
        manager.publish({"view": "menu", "items": []})
    replay = manager.replay(session.session_id)
    assert replay["view"] == "menu"
    assert replay["cart_snapshot"]["order_id"] == "cake-order"


@pytest.mark.parametrize("action", ["clear", "add"])
def test_explicit_new_draft_replaces_the_invoice(display, action):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        manager.publish({"view": "menu", "items": []})
        assert cart.execute(
            action=action,
            item={
                "variant_id": "cake",
                "name": "Cake",
                "unit_price": 39000,
                "quantity": 1,
            },
            open_cart=False,
        ).success
        assert cart.execute(action="view").success
    assert session.last_payload["view"] == "cart"
    assert len(session.last_payload["lines"]) == (1 if action == "add" else 0)


@pytest.mark.parametrize("next_customer", ["other-owner", "reset"])
def test_invoice_cannot_leak_to_another_customer(display, next_customer):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        manager.publish({"view": "menu", "items": []})
    if next_customer == "reset":
        manager.reset(session.session_id)
    with conversation_scope("customer-1" if next_customer == "reset" else "customer-2"):
        assert cart.execute(action="view").success
    assert session.last_payload["view"] == "cart"
    assert session.last_payload["lines"] == []


def test_touch_add_replaces_the_retained_invoice_without_navigating(display):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        manager.publish({"view": "menu", "items": []})
        assert cart.edit_without_display(
            "add",
            {
                "item": {
                    "variant_id": "cake",
                    "name": "Cake",
                    "unit_price": 39000,
                    "quantity": 1,
                }
            },
        ).success
        replay = manager.replay(session.session_id)
        assert replay["view"] == "menu"
        assert replay["cart_snapshot"]["view"] == "cart"
        assert cart.execute(action="view").success
    assert session.last_payload["view"] == "cart"
    assert session.last_payload["lines"][0]["name"] == "Cake"


def test_invalidated_qr_cannot_return_from_the_menu_cache(display):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        manager.publish({"view": "menu", "items": []})
        manager.invalidate_order_qr(
            "coffee-order",
            {
                "order_id": "coffee-order",
                "status": "canceled",
                "lines": [],
                "total": 35000,
                "order_type": "take-out",
                "branch": "",
            },
        )
        replay = manager.replay(session.session_id)
        assert replay["view"] == "menu"
        assert replay["cart_snapshot"]["view"] == "bill"
        assert "qr_code" not in replay["cart_snapshot"]
        assert cart.execute(action="view").success
    assert session.last_payload["view"] == "bill"
    assert session.last_payload["status"] == "canceled"


def test_print_completion_updates_the_retained_invoice_while_browsing(display):
    manager, session, _ = display
    with conversation_scope("customer-1"):
        _receipt(manager, view="bill")
        manager.publish({"view": "menu", "items": []})
    manager.update_print_status(
        {
            "presentation_session_id": session.session_id,
            "order_id": "coffee-order",
            "printing_state": "completed",
        }
    )
    replay = manager.replay(session.session_id)
    assert replay["view"] == "menu"
    assert replay["cart_snapshot"]["printing_state"] == "completed"


def test_old_payment_poll_cannot_replace_a_new_draft(display):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        manager.publish({"view": "menu", "items": []})

    def paid_after_new_cart(_url):
        with conversation_scope("customer-1"):
            assert cart.execute(
                action="add",
                open_cart=False,
                item={
                    "variant_id": "cake",
                    "name": "Cake",
                    "unit_price": 39000,
                    "quantity": 1,
                },
            ).success
        return {"result": {"slug": "coffee-order", "status": "paid"}}

    manager.configure_touch_checkout(
        TouchCheckout(
            run=lambda **kwargs: None,
            tables_url="",
            order_url="/{order_id}",
            fetch_json=paid_after_new_cart,
        )
    )
    assert manager.check_payment(session.session_id) == "paid"
    with conversation_scope("customer-1"):
        assert cart.execute(action="view").success
    assert session.last_payload["view"] == "cart"
    assert session.last_payload["lines"][0]["name"] == "Cake"


def test_missing_creation_time_keeps_one_fallback_deadline(display):
    manager, session, _ = display
    with conversation_scope("customer-1"):
        receipt = _receipt(manager)
        receipt["order_id"] = "missing-time"
        receipt.pop("created_at")
        manager.publish(receipt)
        first = session.last_payload["created_at"]
        manager.publish({"view": "menu", "items": []})
        manager.publish(receipt)
        assert session.last_payload["created_at"] == first


def test_reconnect_after_reset_explicitly_forgets_the_previous_cart(display):
    manager, session, _ = display
    with conversation_scope("customer-1"):
        _receipt(manager)
    manager.reset(session.session_id)
    manager.publish({"view": "menu", "items": []})
    assert manager.replay(session.session_id)["cart_snapshot"] is None


def test_first_checkout_keeps_the_merchant_javascript_creation_time(display):
    manager, session, _ = display
    with conversation_scope("customer-1"):
        receipt = _receipt(manager)
        receipt["order_id"] = "javascript-time"
        receipt["created_at"] = "Thu Oct 15 2026 02:15:00 GMT+0000 (UTC)"
        manager.publish(
            {"view": "bill", "order_id": receipt["order_id"], "status": "pending"}
        )
        manager.publish(receipt)
    assert session.last_payload["created_at"] == receipt["created_at"]


def test_reconnect_after_silent_touch_add_replays_the_new_draft(display):
    manager, session, cart = display
    with conversation_scope("customer-1"):
        _receipt(manager)
        # Touch MENU is local to the browser: the server last published QR.
        assert cart.edit_without_display(
            "add",
            {
                "item": {
                    "variant_id": "cake",
                    "name": "Cake",
                    "unit_price": 39000,
                    "quantity": 1,
                }
            },
        ).success
    replay = manager.replay(session.session_id)
    assert replay["view"] == "cart"
    assert replay["lines"][0]["name"] == "Cake"
