"""A cart add refused before any change says it never started.

Live 2026-10-10 12:59: the model added an item without fresh menu evidence;
the agent-facing cart refused it (``cart_item_unverified``) before touching
the draft, but the intent guard counted it as an attempted target and refused
the correct retry through the prepared add skill. Marking the refusal
``execution_started: False`` lets the guard allow that retry.
"""

from __future__ import annotations

from openjarvis.core.conversation import conversation_scope
from openjarvis.tools.display import (
    DisplayCartMemoryTool,
    DisplayCartTool,
    DisplayMenuTool,
)

UNVERIFIED = {
    "variant_id": "never-shown",
    "name": "Cà phê đen",
    "unit_price": 35_000,
    "quantity": 2,
    "note": "",
}


def test_unverified_item_refusal_is_marked_not_started():
    cart = DisplayCartTool()
    tool = DisplayCartMemoryTool(cart, DisplayMenuTool())

    with conversation_scope("unverified-add"):
        result = tool.execute(action="add", item=UNVERIFIED)
        snapshot = cart.current_snapshot()

    assert result.success is False
    assert result.content == "cart_item_unverified"
    assert result.metadata.get("execution_started") is False
    assert snapshot["lines"] == []


def test_malformed_item_refusal_is_marked_not_started():
    tool = DisplayCartMemoryTool(DisplayCartTool(), DisplayMenuTool())

    with conversation_scope("malformed-add"):
        result = tool.execute(action="add", items=["not an item"])

    assert result.content == "cart_item_unverified"
    assert result.metadata.get("execution_started") is False
