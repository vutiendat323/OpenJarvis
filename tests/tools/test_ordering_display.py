"""Only verified merchant order reads publish a customer bill."""

from __future__ import annotations

import importlib
import json
from unittest.mock import MagicMock

from openjarvis.core.config import JarvisConfig
from openjarvis.core.events import EventBus, EventType
from openjarvis.merchants.fake import FakeMerchant
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.display import DisplayBillTool
from openjarvis.tools.ordering import CartAddTool, OrderPlaceTool, OrderVerifyTool

BRANCH = "br-thu-duc"


def _wired(merchant, cls):
    tool = cls()
    tool._merchant = merchant
    return tool


def _placed_order(merchant: FakeMerchant) -> str:
    _wired(merchant, CartAddTool).execute(
        variant="ca-phe-den-std", quantity=2, note="ít đường"
    )
    placed = _wired(merchant, OrderPlaceTool).execute(
        type="take-out", branch=BRANCH
    )
    return json.loads(placed.content)["order_id"]


def test_order_verify_displays_exactly_the_merchant_readback_bill_fields():
    merchant = FakeMerchant()
    order_id = _placed_order(merchant)
    bus = EventBus(record_history=True)
    display_bill = DisplayBillTool()
    display_bill._bus = bus
    verify = _wired(merchant, OrderVerifyTool)
    verify._display_bill = display_bill

    result = verify.execute(order_id=order_id)

    assert result.success is True
    display = bus.history[-1]
    assert display.event_type is EventType.DISPLAY_UPDATE
    assert display.data == {
        "view": "bill",
        "order_id": order_id,
        "status": "placed",
        "order_type": "take-out",
        "branch": BRANCH,
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


def test_order_place_acknowledgement_never_publishes_a_bill():
    merchant = FakeMerchant()
    bus = EventBus(record_history=True)
    display_bill = DisplayBillTool()
    display_bill._bus = bus
    _wired(merchant, CartAddTool).execute(
        variant="ca-phe-den-std", quantity=1
    )
    place = _wired(merchant, OrderPlaceTool)
    place._display_bill = display_bill

    result = place.execute(type="take-out", branch=BRANCH)

    assert result.success is True
    assert bus.history == []


def test_unknown_order_never_publishes_a_bill():
    bus = EventBus(record_history=True)
    display_bill = DisplayBillTool()
    display_bill._bus = bus
    verify = _wired(FakeMerchant(), OrderVerifyTool)
    verify._display_bill = display_bill

    result = verify.execute(order_id="unknown-order")

    assert result.success is False
    assert result.content == "unknown_order"
    assert bus.history == []


def test_builder_injects_display_bill_only_into_order_verify():
    import openjarvis.tools.display as display
    import openjarvis.tools.ordering as ordering

    importlib.reload(display)
    importlib.reload(ordering)
    config = JarvisConfig()
    config.merchants.backend = "fake"
    config.tools.enabled = ["order_verify", "display_bill"]
    config.skills.enabled = False
    config.telemetry.enabled = False
    config.traces.enabled = False
    config.agent_manager.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = SystemBuilder(config).engine_instance(engine).speech(False).build()
    try:
        order_verify = next(
            tool for tool in system.tools if tool.spec.name == "order_verify"
        )
        display_bill = next(
            tool for tool in system.tools if tool.spec.name == "display_bill"
        )

        assert order_verify._display_bill is display_bill
    finally:
        system.close()
