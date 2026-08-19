"""Ordering tools reach a merchant through the builder's existing seam."""

from __future__ import annotations

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.ordering import CartViewTool, MenuSearchTool


def test_inject_tool_deps_gives_ordering_tools_a_merchant():
    merchant = FakeMerchant()
    tools = [MenuSearchTool(), CartViewTool()]

    for tool in tools:
        SystemBuilder._inject_ordering_merchant(tool, merchant)

    assert all(tool._merchant is merchant for tool in tools)


def test_non_ordering_tools_are_untouched():
    from openjarvis.tools.calculator import CalculatorTool

    tool = CalculatorTool()
    SystemBuilder._inject_ordering_merchant(tool, FakeMerchant())

    assert not hasattr(tool, "_merchant") or tool._merchant is None
