"""The order flow forces a reasoning turn between every action.

If ordering ever completes in one or two inference turns, a tool has been
built that both changes something and reports the result -- the giant
commerce_checkout() this design exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import (
    BranchListTool,
    CartAddTool,
    CartViewTool,
    MenuSearchTool,
    OrderPlaceTool,
    OrderVerifyTool,
)

pytestmark = pytest.mark.integration


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_the_real_request_takes_six_observed_steps():
    """"Chọn cà phê đen ít đường, đặt mang đi" -- the whole flow.

    Each step is a place the Agent must look at a result before it can choose
    the next call. There is no shortcut through them, because no tool returns
    both an effect and its consequence.
    """
    merchant = FakeMerchant()
    branches, search, add, view, place, verify = _wired(
        merchant,
        BranchListTool,
        MenuSearchTool,
        CartAddTool,
        CartViewTool,
        OrderPlaceTool,
        OrderVerifyTool,
    )

    # 1. which shop -- nothing is orderable until this is known
    branch = json.loads(branches.execute().content)["branches"][0]["slug"]

    # 2. what exists there, and at what price
    products = json.loads(
        search.execute(query="cà phê đen", branch=branch).content
    )["products"]
    variant = products[0]["variants"][0]
    assert variant["slug"] == "ca-phe-den-std"
    assert variant["price"] == 35_000

    # 3. change something -- and learn nothing about the result
    added = json.loads(
        add.execute(variant=variant["slug"], quantity=1, note="ít đường").content
    )
    assert set(added) == {"added", "line_id"}

    # 4. so the cart has to be read
    cart = json.loads(view.execute().content)
    assert cart["total"] == 35_000
    assert cart["lines"][0]["note"] == "ít đường"

    # 5. change something again -- again learning nothing
    placed = json.loads(place.execute(type="take-out", branch=branch).content)
    assert set(placed) == {"placed", "order_id"}

    # 6. so the order has to be read back from the merchant
    order = json.loads(verify.execute(order_id=placed["order_id"]).content)
    assert order["status"] == "placed"
    assert order["order_type"] == "take-out"
    assert order["total"] == cart["total"]

    # ...and the one thing that is still not confirmed says so.
    assert order["notes_are_unverified"] is True


def test_the_merchant_is_the_authority_when_the_agent_is_wrong():
    """A belief that disagrees with the merchant loses."""
    merchant = FakeMerchant()
    add, view = _wired(merchant, CartAddTool, CartViewTool)

    merchant.set_price("ca-phe-den-std", 80_000)
    add.execute(variant="ca-phe-den-std", quantity=1)

    assert json.loads(view.execute().content)["total"] == 80_000


def test_ordering_and_display_tools_never_overlap():
    """A display tool must not be able to change an order, and an ordering
    tool must not be able to draw.

    Enumerated from the modules, not from ToolRegistry: conftest clears the
    registry autouse before every test, so a registry walk would iterate
    nothing and pass without checking anything.
    """
    import inspect

    from openjarvis.tools import display, ordering
    from openjarvis.tools._stubs import BaseTool

    checked = 0
    for module in (ordering, display):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(member, BaseTool)
                or member.__module__ != module.__name__
                or inspect.isabstract(member)
            ):
                continue
            spec = member().spec
            kinds = {
                key
                for key in ("mutates", "observes", "displays")
                if spec.metadata.get(key)
            }
            assert len(kinds) == 1, f"{spec.name} declares {kinds}"
            checked += 1

    assert checked == 11, f"expected 8 ordering + 3 display tools, saw {checked}"
