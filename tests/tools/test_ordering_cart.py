"""cart_add acknowledges. Only cart_view says what is true."""

from __future__ import annotations

import json

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import CartAddTool, CartRemoveTool, CartViewTool


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_cart_add_declares_a_mutation_and_cart_view_an_observation():
    assert CartAddTool().spec.metadata == {"mutates": True}
    assert CartRemoveTool().spec.metadata == {"mutates": True}
    assert CartViewTool().spec.metadata == {"observes": True}


def test_cart_add_returns_only_an_acknowledgement():
    """The doctrine, enforced at the payload level: no total, no lines,
    nothing the Agent could mistake for the state of the cart."""
    add, = _wired(FakeMerchant(), CartAddTool)
    result = add.execute(variant="ca-phe-den-std", quantity=1, note="ít đường")

    assert result.success
    payload = json.loads(result.content)
    assert set(payload) == {"added", "line_id"}
    assert payload["added"] is True


def test_cart_view_reports_lines_total_and_the_note_verbatim():
    merchant = FakeMerchant()
    add, view = _wired(merchant, CartAddTool, CartViewTool)
    add.execute(variant="ca-phe-den-std", quantity=2, note="ít đường")

    payload = json.loads(view.execute().content)
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["quantity"] == 2
    assert payload["lines"][0]["note"] == "ít đường"
    assert payload["total"] == 70_000


def test_cart_add_works_without_a_note():
    add, view = _wired(FakeMerchant(), CartAddTool, CartViewTool)
    add.execute(variant="ca-phe-den-std", quantity=1)
    assert json.loads(view.execute().content)["lines"][0]["note"] == ""


def test_cart_add_rejects_an_unavailable_product():
    add, = _wired(FakeMerchant(), CartAddTool)
    result = add.execute(variant="tiramisu-std", quantity=1)
    assert result.success is False
    assert "variant_unavailable" in result.content


def test_cart_add_rejects_an_unknown_variant():
    """A product slug is not a variant slug; passing one must fail loudly
    rather than silently ordering something else."""
    add, = _wired(FakeMerchant(), CartAddTool)
    result = add.execute(variant="ca-phe-den", quantity=1)
    assert result.success is False
    assert "variant_unavailable" in result.content


def test_cart_remove_acknowledges_and_cart_view_confirms():
    merchant = FakeMerchant()
    add, remove, view = _wired(merchant, CartAddTool, CartRemoveTool, CartViewTool)
    line_id = json.loads(
        add.execute(variant="ca-phe-den-std", quantity=1).content
    )["line_id"]

    removed = json.loads(remove.execute(line_id=line_id).content)
    assert set(removed) == {"removed"}
    assert removed["removed"] is True
    assert json.loads(view.execute().content)["lines"] == []


def test_cart_remove_of_an_unknown_line_fails():
    remove, = _wired(FakeMerchant(), CartRemoveTool)
    result = remove.execute(line_id="L999")
    assert result.success is False
    assert "unknown_line" in result.content
