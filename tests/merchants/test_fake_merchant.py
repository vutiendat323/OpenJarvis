"""The fake merchant is the ground truth Phase 1 reasons against.

Its shape mirrors a real merchant API: variants rather than modifiers,
branch-scoped menus, free-text notes, and an order type.
"""

from __future__ import annotations

import pytest

from openjarvis.merchants.fake import FakeMerchant

BRANCH = "br-thu-duc"


def test_branches_are_listed_with_a_slug_and_a_name():
    branches = FakeMerchant().list_branches()
    assert BRANCH in {branch.slug for branch in branches}
    assert all(branch.name for branch in branches)


def test_search_finds_by_name_case_insensitively():
    results = FakeMerchant().search_menu("CÀ PHÊ ĐEN", BRANCH)
    assert [product.slug for product in results] == ["ca-phe-den"]


def test_search_with_an_empty_query_returns_the_whole_branch_menu():
    assert len(FakeMerchant().search_menu("", BRANCH)) >= 5


def test_menus_are_branch_scoped():
    """A product on one branch's menu need not be on another's."""
    merchant = FakeMerchant()
    here = {p.slug for p in merchant.search_menu("", BRANCH)}
    there = {p.slug for p in merchant.search_menu("", "br-quan-1")}
    assert here != there


def test_search_on_an_unknown_branch_returns_nothing():
    assert FakeMerchant().search_menu("", "br-nowhere") == []


def test_a_product_carries_priced_variants():
    product = FakeMerchant().get_product("ca-phe-den", BRANCH)
    assert product.name == "Cà phê đen"
    assert [(v.slug, v.size, v.price) for v in product.variants] == [
        ("ca-phe-den-std", "tiêu chuẩn", 35_000)
    ]


def test_add_returns_a_line_id_and_does_not_return_the_cart():
    """The port mirrors the doctrine: a mutation acknowledges, it does not report."""
    line_id = FakeMerchant().add_to_cart("ca-phe-den-std", 1, "ít đường")
    assert isinstance(line_id, str) and line_id


def test_the_note_survives_verbatim_because_nothing_interprets_it():
    merchant = FakeMerchant()
    merchant.add_to_cart("ca-phe-den-std", 1, "ít đường")
    assert merchant.read_cart().lines[0].note == "ít đường"


def test_cart_total_is_variant_price_times_quantity():
    merchant = FakeMerchant()
    merchant.add_to_cart("ca-phe-den-std", 2, "")
    cart = merchant.read_cart()
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 2
    assert cart.total == 70_000


def test_add_rejects_an_unknown_variant():
    with pytest.raises(ValueError):
        FakeMerchant().add_to_cart("no-such-variant", 1, "")


def test_add_rejects_a_non_positive_quantity():
    with pytest.raises(ValueError):
        FakeMerchant().add_to_cart("ca-phe-den-std", 0, "")


def test_remove_empties_the_cart():
    merchant = FakeMerchant()
    line_id = merchant.add_to_cart("ca-phe-den-std", 1, "")
    assert merchant.remove_from_cart(line_id) is True
    assert merchant.read_cart().lines == ()


def test_place_order_records_type_and_branch_then_clears_the_cart():
    merchant = FakeMerchant()
    merchant.add_to_cart("ca-phe-den-std", 1, "ít đường")
    order_id = merchant.place_order("take-out", BRANCH)

    order = merchant.read_order(order_id)
    assert order is not None
    assert order.status == "placed"
    assert order.order_type == "take-out"
    assert order.branch_slug == BRANCH
    assert order.lines[0].note == "ít đường"
    assert merchant.read_cart().lines == ()


def test_place_order_rejects_an_unknown_order_type():
    merchant = FakeMerchant()
    merchant.add_to_cart("ca-phe-den-std", 1, "")
    with pytest.raises(ValueError):
        merchant.place_order("teleport", BRANCH)


def test_read_order_of_an_unknown_id_is_none():
    assert FakeMerchant().read_order("nope") is None


def test_set_price_changes_what_the_merchant_reports():
    """The seam the fast-path safety test will need in Phase 2."""
    merchant = FakeMerchant()
    merchant.set_price("ca-phe-den-std", 80_000)
    product = merchant.get_product("ca-phe-den", BRANCH)
    assert product.variants[0].price == 80_000
