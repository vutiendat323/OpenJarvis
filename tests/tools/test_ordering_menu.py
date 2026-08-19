"""Branch and menu tools observe. They never change anything."""

from __future__ import annotations

import json

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import BranchListTool, MenuItemTool, MenuSearchTool

BRANCH = "br-thu-duc"


def _tool(cls, merchant=None):
    tool = cls()
    tool._merchant = merchant or FakeMerchant()
    return tool


def test_all_three_declare_themselves_observations():
    for cls in (BranchListTool, MenuSearchTool, MenuItemTool):
        assert cls().spec.metadata == {"observes": True}


def test_branch_list_returns_slugs_and_names():
    payload = json.loads(_tool(BranchListTool).execute().content)
    slugs = [branch["slug"] for branch in payload["branches"]]
    assert BRANCH in slugs
    assert all(branch["name"] for branch in payload["branches"])


def test_menu_search_returns_matching_products_with_priced_variants():
    result = _tool(MenuSearchTool).execute(query="cà phê đen", branch=BRANCH)
    assert result.success
    payload = json.loads(result.content)
    assert [p["slug"] for p in payload["products"]] == ["ca-phe-den"]
    variants = payload["products"][0]["variants"]
    assert variants[0]["slug"] == "ca-phe-den-std"
    assert variants[0]["price"] == 35_000


def test_menu_search_reports_unavailable_products_rather_than_hiding_them():
    """The Agent must be able to say 'tiramisu is sold out' instead of
    silently pretending it does not exist."""
    result = _tool(MenuSearchTool).execute(query="tiramisu", branch=BRANCH)
    payload = json.loads(result.content)
    assert payload["products"][0]["available"] is False


def test_menu_search_requires_a_branch():
    """A menu without a branch is meaningless, and guessing one would put the
    customer's order at the wrong shop."""
    result = _tool(MenuSearchTool).execute(query="latte")
    assert result.success is False
    assert "branch_required" in result.content


def test_menu_search_on_an_unknown_branch_says_so():
    result = _tool(MenuSearchTool).execute(query="latte", branch="br-nowhere")
    assert result.success is False
    assert "unknown_branch" in result.content


def test_menu_item_returns_every_variant_with_its_own_slug_and_price():
    result = _tool(MenuItemTool).execute(product="latte", branch=BRANCH)
    payload = json.loads(result.content)
    assert [(v["size"], v["price"]) for v in payload["variants"]] == [
        ("vừa", 55_000),
        ("lớn", 61_000),
    ]


def test_menu_item_unknown_product_fails_without_raising():
    result = _tool(MenuItemTool).execute(product="unicorn-frappe", branch=BRANCH)
    assert result.success is False
    assert "unknown_product" in result.content


def test_tool_without_a_merchant_fails_clearly():
    result = MenuSearchTool().execute(query="latte", branch=BRANCH)
    assert result.success is False
    assert "merchant_unavailable" in result.content
