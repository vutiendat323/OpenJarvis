"""Trend Coffee maps authoritative snapshots into the ordering port."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import NormalizedBatch, ResourceRecord
from openjarvis.system.bundles import DataPlaneRuntime


def _runtime(tmp_path) -> DataPlaneRuntime:
    snapshots = StructuredSnapshotStore(tmp_path / "structured.db")
    snapshots.upsert_many(
        (
            NormalizedBatch(
                source_id="trend-coffee",
                resource_type="branch",
                records=(
                    ResourceRecord(
                        "ba9355f797",
                        {
                            "slug": "ba9355f797",
                            "name": "TREND Coffee",
                            "address": "1 Đường Cà Phê",
                            "available": True,
                        },
                    ),
                ),
                synced_at="2026-08-20T00:00:00+00:00",
            ),
            NormalizedBatch(
                source_id="trend-coffee",
                resource_type="menu_item",
                records=(
                    ResourceRecord(
                        "23f99adf51",
                        {
                            "slug": "23f99adf51",
                            "name": "Cà phê đen",
                            "category": "coffee",
                            "available": True,
                            "variants": [
                                {
                                    "slug": "d5de540d4c",
                                    "size": "tiêu chuẩn",
                                    "price": 35000,
                                    "available": True,
                                },
                                {
                                    "slug": "sold-out",
                                    "size": "lớn",
                                    "price": 45000,
                                    "available": False,
                                },
                            ],
                        },
                    ),
                    ResourceRecord(
                        "unavailable-product",
                        {
                            "slug": "unavailable-product",
                            "name": "Hết hàng",
                            "available": False,
                            "variants": [],
                        },
                    ),
                ),
                synced_at="2026-08-20T00:00:00+00:00",
            ),
            NormalizedBatch(
                source_id="trend-coffee",
                resource_type="order",
                records=(
                    ResourceRecord(
                        "verified-order",
                        {
                            "slug": "verified-order",
                            "type": "take-out",
                            "branch": "ba9355f797",
                            "status": "placed",
                            "orderItems": [
                                {
                                    "quantity": 2,
                                    "variant": "d5de540d4c",
                                    "note": "ít đá",
                                    "name": "Cà phê đen",
                                    "size": "tiêu chuẩn",
                                    "price": 35000,
                                }
                            ],
                        },
                    ),
                ),
                synced_at="2026-08-20T00:00:00+00:00",
            ),
        )
    )
    return DataPlaneRuntime(
        capabilities=MagicMock(),
        snapshots=snapshots,
        discovery=MagicMock(),
        direct=MagicMock(),
    )


@pytest.fixture
def runtime(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        yield runtime
    finally:
        runtime.close()


def test_reads_branch_and_menu_from_structured_snapshot(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)

    assert merchant.list_branches()[0].slug == "ba9355f797"
    coffee = merchant.search_menu("CÀ PHÊ ĐEN", "ba9355f797")[0]
    assert coffee.variants[0].slug == "d5de540d4c"
    assert merchant.get_product("unavailable-product", "ba9355f797") is not None


def test_cart_is_local_and_only_accepts_available_snapshot_variants(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)

    line_id = merchant.add_to_cart("d5de540d4c", 2, "mang đi")
    cart = merchant.read_cart()

    assert cart.lines[0].line_id == line_id
    assert cart.lines[0].note == "mang đi"
    assert cart.lines[0].line_total == 70_000
    assert merchant.remove_from_cart(line_id) is True
    assert merchant.remove_from_cart(line_id) is False
    for unavailable in ("invented", "sold-out"):
        with pytest.raises(ValueError, match="variant_unavailable"):
            merchant.add_to_cart(unavailable, 1, "")


def test_place_order_is_allowlisted_then_quarantined_without_clearing_cart(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)
    merchant.add_to_cart("d5de540d4c", 2, "đừng bỏ ống hút")

    with pytest.raises(DataPlaneError) as raised:
        merchant.place_order("take-out", "ba9355f797")

    assert raised.value.code is DataPlaneErrorCode.CAPABILITY_QUARANTINED
    assert runtime.direct.execute.call_count == 0
    assert [line.note for line in merchant.read_cart().lines] == ["đừng bỏ ống hút"]
    with pytest.raises(ValueError, match="unknown_branch"):
        merchant.place_order("take-out", "unknown")
    with pytest.raises(ValueError, match="invalid_order_type"):
        merchant.place_order("teleport", "ba9355f797")


def test_place_order_preserves_an_empty_free_text_note(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)
    merchant.add_to_cart("d5de540d4c", 1, "")

    with pytest.raises(DataPlaneError) as raised:
        merchant.place_order("take-out", "ba9355f797")

    assert raised.value.code is DataPlaneErrorCode.CAPABILITY_QUARANTINED
    assert runtime.direct.execute.call_count == 0
    assert merchant.read_cart().lines[0].note == ""


def test_read_order_uses_authoritative_order_snapshot_only(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    order = TrendCoffeeMerchant(runtime).read_order("verified-order")

    assert order is not None
    assert order.lines[0].note == "ít đá"
    assert order.total == 70_000
    assert TrendCoffeeMerchant(runtime).read_order("not-in-snapshot") is None
