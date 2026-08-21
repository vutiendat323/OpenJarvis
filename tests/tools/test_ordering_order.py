"""order_place hands back an id. order_verify says what the merchant thinks."""

from __future__ import annotations

import json

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.merchants.port import OrderApprovalRequired
from openjarvis.tools.ordering import (
    CartAddTool,
    MenuSearchTool,
    OrderPlaceTool,
    OrderVerifyTool,
)


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_metadata_declares_one_kind_each():
    assert OrderPlaceTool().spec.metadata == {"mutates": True}
    assert OrderVerifyTool().spec.metadata == {"observes": True}


def test_only_order_place_declares_the_resume_approval_parameter():
    assert "approval_id" in OrderPlaceTool().spec.parameters["properties"]
    assert "approval_id" not in MenuSearchTool().spec.parameters["properties"]


BRANCH = "br-thu-duc"


def test_order_place_returns_only_an_order_id():
    merchant = FakeMerchant()
    add, place = _wired(merchant, CartAddTool, OrderPlaceTool)
    add.execute(variant="ca-phe-den-std", quantity=1)

    payload = json.loads(place.execute(type="take-out", branch=BRANCH).content)
    assert set(payload) == {"placed", "order_id"}
    assert payload["placed"] is True


def test_order_place_refuses_an_empty_cart():
    (place,) = _wired(FakeMerchant(), OrderPlaceTool)
    result = place.execute(type="take-out", branch=BRANCH)
    assert result.success is False
    assert "cart_empty" in result.content


def test_order_place_requires_a_type_because_the_customer_chose_one():
    """'Mang đi' is take-out. Defaulting silently would hand a takeaway
    customer a dine-in order."""
    merchant = FakeMerchant()
    add, place = _wired(merchant, CartAddTool, OrderPlaceTool)
    add.execute(variant="ca-phe-den-std", quantity=1)

    result = place.execute(branch=BRANCH)
    assert result.success is False
    assert "order_type_required" in result.content


def test_order_place_rejects_an_unknown_type():
    merchant = FakeMerchant()
    add, place = _wired(merchant, CartAddTool, OrderPlaceTool)
    add.execute(variant="ca-phe-den-std", quantity=1)

    result = place.execute(type="teleport", branch=BRANCH)
    assert result.success is False
    assert "invalid_order_type" in result.content


def test_order_place_surfaces_the_merchants_exact_pending_approval():
    merchant = FakeMerchant()
    add, place = _wired(merchant, CartAddTool, OrderPlaceTool)
    add.execute(variant="ca-phe-den-std", quantity=1)
    merchant.place_order = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        OrderApprovalRequired("approval-1", "request-hash")
    )

    result = place.execute(type="take-out", branch=BRANCH)

    assert result.success is False
    assert result.metadata == {
        "pending_approval": True,
        "approval_id": "approval-1",
        "request_hash": "request-hash",
    }


def test_order_verify_reports_what_the_merchant_recorded():
    merchant = FakeMerchant()
    add, place, verify = _wired(merchant, CartAddTool, OrderPlaceTool, OrderVerifyTool)
    add.execute(variant="ca-phe-den-std", quantity=2, note="ít đường")
    order_id = json.loads(place.execute(type="take-out", branch=BRANCH).content)[
        "order_id"
    ]

    payload = json.loads(verify.execute(order_id=order_id).content)
    assert payload["order_id"] == order_id
    assert payload["status"] == "placed"
    assert payload["order_type"] == "take-out"
    assert payload["branch"] == BRANCH
    assert payload["lines"][0]["quantity"] == 2
    assert payload["total"] == 70_000


def test_verify_echoes_the_note_but_that_is_not_confirmation():
    """The note reads back because it is the string that was sent. Nothing
    here confirms the drink will be made that way -- a person reads it.
    The tool payload therefore says so, so the Agent does not overclaim."""
    merchant = FakeMerchant()
    add, place, verify = _wired(merchant, CartAddTool, OrderPlaceTool, OrderVerifyTool)
    add.execute(variant="ca-phe-den-std", quantity=1, note="ít đường")
    order_id = json.loads(place.execute(type="take-out", branch=BRANCH).content)[
        "order_id"
    ]

    payload = json.loads(verify.execute(order_id=order_id).content)
    assert payload["lines"][0]["note"] == "ít đường"
    assert payload["notes_are_unverified"] is True


def test_order_verify_of_an_unknown_order_fails():
    (verify,) = _wired(FakeMerchant(), OrderVerifyTool)
    result = verify.execute(order_id="ORD9999")
    assert result.success is False
    assert "unknown_order" in result.content


def test_a_later_price_change_does_not_rewrite_a_placed_order():
    """The merchant is the authority, and a placed order was priced when
    placed."""
    merchant = FakeMerchant()
    add, place, verify = _wired(merchant, CartAddTool, OrderPlaceTool, OrderVerifyTool)
    add.execute(variant="ca-phe-den-std", quantity=1)
    order_id = json.loads(place.execute(type="take-out", branch=BRANCH).content)[
        "order_id"
    ]

    merchant.set_price("ca-phe-den-std", 80_000)

    payload = json.loads(verify.execute(order_id=order_id).content)
    assert payload["total"] == 35_000
