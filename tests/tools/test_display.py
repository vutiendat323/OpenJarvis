"""Display tools publish structured data. They never emit markup."""

from __future__ import annotations

import json

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult
from openjarvis.kiosk.presentation import PresentationSessionManager
from openjarvis.tools import evidence
from openjarvis.tools.display import (
    DisplayBillTool,
    DisplayCartTool,
    DisplayClearTool,
    DisplayMenuTool,
    DisplayPaymentQrTool,
)


class _Recorder:
    def __init__(self, bus):
        self.events = []
        bus.subscribe(EventType.DISPLAY_UPDATE, self.events.append)


class _FakePresentationManager:
    def __init__(self) -> None:
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)
        return ToolResult(tool_name="presentation", content="presentation_published")


def _wired(cls):
    bus = EventBus()
    recorder = _Recorder(bus)
    tool = cls()
    tool._bus = bus
    return tool, recorder


def test_display_tools_are_neither_mutations_nor_observations():
    """They draw; they do not touch merchant state, so the doctrine test
    must not classify them."""
    for cls in (
        DisplayMenuTool,
        DisplayCartTool,
        DisplayBillTool,
        DisplayPaymentQrTool,
        DisplayClearTool,
    ):
        metadata = cls().spec.metadata
        assert metadata == {"displays": True}
        assert cls().spec.category == "display"


def test_display_menu_publishes_the_items_it_was_given():
    tool, recorder = _wired(DisplayMenuTool)
    result = tool.execute(
        items=[
            {"id": "latte", "name": "Latte", "price": 45000, "available": True},
        ]
    )

    assert result.success
    assert len(recorder.events) == 1
    data = recorder.events[0].data
    assert data["view"] == "menu"
    assert data["items"][0]["name"] == "Latte"


def test_display_menu_keeps_only_the_fields_the_page_renders():
    """Anything else the model invents must not reach the page."""
    tool, recorder = _wired(DisplayMenuTool)
    tool.execute(
        items=[
            {
                "id": "latte",
                "name": "Latte",
                "price": 45000,
                "available": True,
                "html": "<script>alert(1)</script>",
                "onclick": "steal()",
            }
        ]
    )

    item = recorder.events[0].data["items"][0]
    assert set(item) <= {"id", "name", "price", "available", "image_url", "note"}


def test_display_cart_publishes_lines_and_total():
    tool, recorder = _wired(DisplayCartTool)
    tool.execute(
        lines=[{"name": "Latte", "quantity": 2, "line_total": 102000}],
        total=102000,
    )

    data = recorder.events[0].data
    assert data["view"] == "cart"
    assert data["total"] == 102000
    assert data["lines"][0]["quantity"] == 2


def test_display_cart_keeps_size_and_note_but_drops_invented_fields():
    """The screen is what a person at the shop reads to make the drink --
    size and note must survive, and a model-invented key must not."""
    tool, recorder = _wired(DisplayCartTool)
    tool.execute(
        lines=[
            {
                "name": "Latte",
                "size": "Lớn",
                "note": "ít đường",
                "quantity": 2,
                "line_total": 122000,
                "html": "<b>x</b>",
            }
        ],
        total=122000,
    )

    line = recorder.events[0].data["lines"][0]
    assert line["size"] == "Lớn"
    assert line["note"] == "ít đường"
    assert set(line) <= {"name", "size", "note", "quantity", "line_total"}
    assert "html" not in line


def test_display_bill_keeps_only_merchant_bill_fields_and_normalizes_total():
    tool, recorder = _wired(DisplayBillTool)

    result = tool.execute(
        order_id="order-1",
        status="placed",
        order_type="take-out",
        branch="br-thu-duc",
        lines=[
            {
                "name": "Cà phê đen",
                "size": "tiêu chuẩn",
                "note": "ít đường",
                "quantity": 2,
                "line_total": 70_000,
                "html": "<script>x</script>",
            }
        ],
        total="70000",
        receipt_id="invented-receipt",
    )

    assert result.success is True
    assert recorder.events[0].data == {
        "view": "bill",
        "order_id": "order-1",
        "status": "placed",
        "order_type": "take-out",
        "branch": "br-thu-duc",
        "lines": [
            {
                "name": "Cà phê đen",
                "size": "tiêu chuẩn",
                "note": "ít đường",
                "quantity": 2,
                "line_total": 70_000,
            }
        ],
        "total": 70_000,
    }


def test_display_payment_qr_drops_every_field_outside_the_verified_view():
    tool, recorder = _wired(DisplayPaymentQrTool)
    tool._payment_trusted_origins = (("https", "merchant.example", 443),)

    evidence.reset()
    try:
        with conversation_scope("test-display-payment-qr-drops-fields"):
            evidence.record(
                "http_request",
                "merchant-opaque-qr",
                200,
                "https://merchant.example/pay",
            )
            result = tool.execute(
                order_id="order-1",
                payment_slug="payment-1",
                status="pending",
                qr_code="merchant-opaque-qr",
                html="<img src=x onerror=alert(1)>",
                receipt_id="receipt-that-must-not-be-shown",
            )
    finally:
        evidence.reset()

    assert result.success is True
    assert recorder.events[0].data == {
        "view": "payment_qr",
        "order_id": "order-1",
        "payment_slug": "payment-1",
        "status": "pending",
        "qr_code": "merchant-opaque-qr",
    }


def test_display_payment_qr_rejects_an_arbitrary_nonempty_value_without_publishing():
    tool, recorder = _wired(DisplayPaymentQrTool)
    tool._payment_trusted_origins = (("https", "merchant.example", 443),)

    evidence.reset()
    try:
        with conversation_scope("test-display-payment-qr-rejects-invented"):
            result = tool.execute(
                order_id="order-1",
                payment_slug="payment-invented",
                status="pending",
                qr_code="agent-invented-qr",
            )
    finally:
        evidence.reset()

    assert result == ToolResult(
        tool_name="display_payment_qr",
        content="payment_evidence_missing",
        success=False,
    )
    assert recorder.events == []


def test_display_payment_qr_rejects_a_missing_payment_identifier():
    """Evidence alone must not be enough -- the payload must be complete too.

    Records matching evidence so the ``observed_in_tool_output`` term is
    satisfied, then omits ``payment_slug`` so completeness is the only thing
    that can still cause the refusal.
    """
    tool, recorder = _wired(DisplayPaymentQrTool)
    tool._payment_trusted_origins = (("https", "merchant.example", 443),)

    evidence.reset()
    try:
        with conversation_scope("test-display-payment-qr-rejects-incomplete"):
            evidence.record(
                "http_request",
                "merchant-opaque-qr",
                200,
                "https://merchant.example/pay",
            )
            result = tool.execute(
                order_id="order-1",
                status="pending",
                qr_code="merchant-opaque-qr",
            )
    finally:
        evidence.reset()

    assert result == ToolResult(
        tool_name="display_payment_qr",
        content="payment_evidence_missing",
        success=False,
    )
    assert recorder.events == []


def test_display_payment_qr_rejects_an_empty_qr_without_publishing():
    tool, recorder = _wired(DisplayPaymentQrTool)

    result = tool.execute(
        order_id="order-1",
        payment_slug="payment-1",
        status="pending",
        qr_code="  ",
    )

    assert result == ToolResult(
        tool_name="display_payment_qr",
        content="qr_code_required",
        success=False,
    )
    assert recorder.events == []


def test_display_clear_publishes_an_empty_view():
    tool, recorder = _wired(DisplayClearTool)
    tool.execute()
    assert recorder.events[0].data == {"view": "none"}


def test_a_display_tool_without_a_bus_fails_rather_than_silently_doing_nothing():
    result = DisplayMenuTool().execute(items=[{"id": "latte", "name": "Latte"}])
    assert result.success is False
    assert "display_unavailable" in result.content


def test_display_menu_publishes_through_the_presentation_manager():
    presentation = _FakePresentationManager()
    tool = DisplayMenuTool()
    tool._presentation = presentation

    result = tool.execute(items=[{"id": "latte", "name": "Latte"}])

    assert result.success is True
    assert presentation.payloads == [
        {"view": "menu", "items": [{"id": "latte", "name": "Latte"}]}
    ]


def test_display_menu_with_a_manager_fails_before_a_session_is_active():
    client = type("PlaywrightClient", (), {"_server_name": "playwright"})()
    tool = DisplayMenuTool()
    tool._presentation = PresentationSessionManager(EventBus(), client)

    result = tool.execute(items=[{"id": "latte", "name": "Latte"}])

    assert result.success is False
    assert "presentation_unavailable" in result.content


def test_display_menu_rejects_an_empty_item_list_without_publishing():
    # A model with nothing real to show (no fresh read, nothing usable left
    # in context) must not get a false "success" it can narrate as if the
    # menu were actually on screen.
    tool, recorder = _wired(DisplayMenuTool)

    result = tool.execute(items=[])

    assert result == ToolResult(
        tool_name="display_menu",
        content="items_required",
        success=False,
    )
    assert recorder.events == []


def test_display_menu_schema_requires_at_least_one_item():
    assert DisplayMenuTool().spec.parameters["properties"]["items"]["minItems"] == 1


def test_display_menu_schema_offers_latest_http_evidence_for_complete_browsing():
    spec = DisplayMenuTool().spec.parameters

    assert spec["properties"]["all_from_latest_http"]["type"] == "boolean"
    assert spec["required"] == []


def test_display_menu_can_publish_every_product_from_latest_http_evidence():
    tool, recorder = _wired(DisplayMenuTool)
    payload = {
        "result": {
            "items": [
                {
                    "menuItems": [
                        {
                            "product": {
                                "slug": "product-a",
                                "name": "Taco gà",
                                "isActive": True,
                                "variants": [
                                    {"slug": "variant-a", "price": 86_000}
                                ],
                            }
                        },
                        {
                            "product": {
                                "slug": "product-b",
                                "name": "Burger gà",
                                "isActive": False,
                                "variants": [
                                    {"slug": "variant-b", "price": 129_000}
                                ],
                            }
                        },
                    ]
                }
            ]
        }
    }

    evidence.reset()
    try:
        with conversation_scope("test-display-complete-menu-evidence"):
            evidence.record(
                "http_request",
                json.dumps(payload),
                200,
                "https://merchant.example/menu/specific/public",
            )
            result = tool.execute(all_from_latest_http=True)
    finally:
        evidence.reset()

    assert result.success is True
    assert recorder.events[0].data == {
        "view": "menu",
        "items": [
            {
                "id": "variant-a",
                "name": "Taco gà",
                "price": 86_000,
                "available": True,
            },
            {
                "id": "variant-b",
                "name": "Burger gà",
                "price": 129_000,
                "available": False,
            },
        ],
    }


def test_display_menu_keeps_explicit_filtered_items_over_full_http_evidence():
    tool, recorder = _wired(DisplayMenuTool)

    evidence.reset()
    try:
        with conversation_scope("test-display-filter-wins"):
            evidence.record(
                "http_request",
                json.dumps(
                    {
                        "result": {
                            "items": [
                                {
                                    "menuItems": [
                                        {
                                            "product": {
                                                "name": "Taco gà",
                                                "variants": [
                                                    {
                                                        "slug": "variant-a",
                                                        "price": 86_000,
                                                    }
                                                ],
                                            }
                                        },
                                        {
                                            "product": {
                                                "name": "Mì Ý tôm",
                                                "variants": [
                                                    {
                                                        "slug": "variant-b",
                                                        "price": 172_000,
                                                    }
                                                ],
                                            }
                                        },
                                    ]
                                }
                            ]
                        }
                    }
                ),
                200,
                "https://merchant.example/menu/specific/public",
            )
            result = tool.execute(
                items=[{"id": "variant-a", "name": "Taco gà", "price": 86_000}],
                all_from_latest_http=True,
            )
    finally:
        evidence.reset()

    assert result.success is True
    assert recorder.events[0].data["items"] == [
        {"id": "variant-a", "name": "Taco gà", "price": 86_000}
    ]


def test_display_menu_rejects_latest_http_mode_without_menu_evidence():
    tool, recorder = _wired(DisplayMenuTool)

    evidence.reset()
    try:
        with conversation_scope("test-display-missing-menu-evidence"):
            result = tool.execute(all_from_latest_http=True)
    finally:
        evidence.reset()

    assert result == ToolResult(
        tool_name="display_menu",
        content="menu_evidence_missing",
        success=False,
    )
    assert recorder.events == []


def test_display_menu_rejects_items_that_carry_no_recognized_field():
    # Rows that survive field-picking down to nothing are the same failure
    # as an empty list -- there is still nothing displayable.
    tool, recorder = _wired(DisplayMenuTool)

    result = tool.execute(items=[{"onclick": "alert(1)"}, {}])

    assert result.success is False
    assert result.content == "items_required"
    assert recorder.events == []


def test_display_update_is_forwarded_to_websocket_clients():
    from openjarvis.server.ws_bridge import _AGENT_EVENTS

    assert EventType.DISPLAY_UPDATE in _AGENT_EVENTS
