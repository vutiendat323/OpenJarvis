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
                    ResourceRecord(
                        "other-branch",
                        {
                            "slug": "other-branch",
                            "name": "Other Trend Coffee",
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
                            "branch": "ba9355f797",
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


@pytest.mark.parametrize(
    ("quantity", "note"),
    ((1.0, ""), (True, ""), (1, None), (1, object())),
)
def test_cart_rejects_non_port_quantity_or_note_types(runtime, quantity, note):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    with pytest.raises(ValueError, match="variant_unavailable"):
        TrendCoffeeMerchant(runtime).add_to_cart("d5de540d4c", quantity, note)


def test_place_order_revalidates_cart_line_membership_for_requested_branch(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)
    merchant.add_to_cart("d5de540d4c", 1, "ít đá")

    with pytest.raises(ValueError):
        merchant.place_order("take-out", "other-branch")

    assert runtime.direct.execute.call_count == 0
    assert [line.variant_slug for line in merchant.read_cart().lines] == ["d5de540d4c"]


def test_place_order_treats_a_branchless_menu_record_as_global(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    runtime.snapshots.upsert(
        NormalizedBatch(
            source_id="trend-coffee",
            resource_type="menu_item",
            records=(
                _menu_record(
                    slug="global-product", variant_slug="global-variant", branch=None
                ),
            ),
            synced_at="2026-08-20T00:01:00+00:00",
        )
    )
    merchant = TrendCoffeeMerchant(runtime)
    merchant.add_to_cart("global-variant", 1, "ít đá")

    with pytest.raises(DataPlaneError) as raised:
        merchant.place_order("take-out", "other-branch")

    assert raised.value.code is DataPlaneErrorCode.CAPABILITY_QUARANTINED
    assert runtime.direct.execute.call_count == 0


@pytest.mark.parametrize(
    ("available", "variant_available"), ((False, True), (True, False))
)
def test_place_order_rejects_a_line_that_became_unavailable(
    runtime, available, variant_available
):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)
    merchant.add_to_cart("d5de540d4c", 1, "ít đá")
    runtime.snapshots.upsert(
        NormalizedBatch(
            source_id="trend-coffee",
            resource_type="menu_item",
            records=(
                _menu_record(available=available, variant_available=variant_available),
            ),
            synced_at="2026-08-20T00:01:00+00:00",
        )
    )

    with pytest.raises(ValueError):
        merchant.place_order("take-out", "ba9355f797")

    assert runtime.direct.execute.call_count == 0
    assert merchant.read_cart().lines[0].line_total == 35_000


def test_place_order_rejects_price_drift_since_cart_add(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    merchant = TrendCoffeeMerchant(runtime)
    merchant.add_to_cart("d5de540d4c", 2, "ít đá")
    runtime.snapshots.upsert(
        NormalizedBatch(
            source_id="trend-coffee",
            resource_type="menu_item",
            records=(_menu_record(price=40_000),),
            synced_at="2026-08-20T00:01:00+00:00",
        )
    )

    with pytest.raises(ValueError):
        merchant.place_order("take-out", "ba9355f797")

    assert runtime.direct.execute.call_count == 0
    assert merchant.read_cart().lines[0].line_total == 70_000


def test_snapshot_menu_reads_do_not_truncate_after_one_hundred_records(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    runtime.snapshots.upsert(
        NormalizedBatch(
            source_id="trend-coffee",
            resource_type="menu_item",
            records=tuple(
                _menu_record(slug=f"item-{index:03d}") for index in range(101)
            ),
            synced_at="2026-08-20T00:01:00+00:00",
        )
    )

    assert TrendCoffeeMerchant(runtime).get_product("item-100", "ba9355f797")


def test_read_order_uses_authoritative_order_snapshot_only(runtime):
    from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

    order = TrendCoffeeMerchant(runtime).read_order("verified-order")

    assert order is not None
    assert order.lines[0].note == "ít đá"
    assert order.total == 70_000
    assert TrendCoffeeMerchant(runtime).read_order("not-in-snapshot") is None


def _menu_record(
    *,
    slug: str = "23f99adf51",
    variant_slug: str = "d5de540d4c",
    branch: str | None = "ba9355f797",
    available: bool = True,
    variant_available: bool = True,
    price: int = 35_000,
) -> ResourceRecord:
    return ResourceRecord(
        slug,
        {
            "slug": slug,
            "name": "Cà phê đen",
            "category": "coffee",
            "branch": branch,
            "available": available,
            "variants": [
                {
                    "slug": variant_slug,
                    "size": "tiêu chuẩn",
                    "price": price,
                    "available": variant_available,
                }
            ],
        },
    )
