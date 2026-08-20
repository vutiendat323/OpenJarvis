from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from openjarvis.data_plane.adapters import SourceAdapterRegistry, TrendCoffeeAdapter
from openjarvis.data_plane.types import DiscoveryEvidence, TrustState

FIXTURES = Path(__file__).parent / "fixtures" / "trendcoffee"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fixture_evidence() -> tuple[DiscoveryEvidence, ...]:
    return (
        DiscoveryEvidence(
            kind="publisher_html",
            source_url="https://trendcoffee.net/",
            status_code=200,
            content_type="text/html",
            body_hash="sha256:" + "a" * 64,
            provenance="publisher_document",
        ),
        DiscoveryEvidence(
            kind="api_read",
            source_url="https://trendcoffee.net/api/latest/branch",
            status_code=200,
            content_type="application/json",
            body_hash="sha256:" + "b" * 64,
            provenance="publisher_response",
        ),
        DiscoveryEvidence(
            kind="api_read",
            source_url="https://trendcoffee.net/api/latest/products",
            status_code=200,
            content_type="application/json",
            body_hash="sha256:" + "c" * 64,
            provenance="publisher_response",
        ),
        DiscoveryEvidence(
            kind="bundle_fingerprint",
            source_url="https://trendcoffee.net/",
            status_code=200,
            content_type="application/javascript",
            body_hash="sha256:" + "d" * 64,
            provenance="publisher_bundle",
        ),
    )


def test_trend_adapter_normalizes_branch_product_and_variant(fixture_evidence):
    adapter = TrendCoffeeAdapter()

    capability = adapter.compile(fixture_evidence)
    branches = adapter.normalize("branch", load_fixture("branch.json"))
    menu = adapter.normalize("menu_item", load_fixture("products.json"))

    coffee = next(
        record for record in menu.records if record.resource_id == "23f99adf51"
    )
    assert capability.origin == "https://trendcoffee.net"
    assert capability.base_url == "https://trendcoffee.net/api/latest"
    assert set(capability.operations) == {
        "branch.list",
        "menu.list",
        "order.read",
        "order.place",
        "payment.initiate",
    }
    assert (
        capability.operations["menu.list"].path == "/products?page={page}&size={size}"
    )
    assert capability.operations["menu.list"].trust is TrustState.READ_VALIDATED
    assert capability.operations["order.place"].trust is TrustState.QUARANTINED
    assert branches.records[0].resource_id == "ba9355f797"
    assert coffee.payload["variants"][0] == {
        "slug": "d5de540d4c",
        "size": "tiêu chuẩn",
        "price": 35000,
    }


def test_trend_adapter_is_registered_from_the_adapter_package():
    from openjarvis.data_plane.adapters import trendcoffee

    SourceAdapterRegistry.clear()
    module = importlib.reload(trendcoffee)

    assert isinstance(
        SourceAdapterRegistry.create("trendcoffee"), module.TrendCoffeeAdapter
    )


def test_trend_adapter_rediscovers_bundle_path_but_fingerprints_only_content():
    adapter = TrendCoffeeAdapter()
    home_html = (FIXTURES / "home.html").read_text()

    evidence = adapter.bundle_evidence(
        "https://trendcoffee.net/", b"fixture bundle content"
    )

    assert adapter.discover_bundle_paths(home_html) == ("/assets/app.js",)
    assert evidence.source_url == "https://trendcoffee.net"
    assert evidence.body_hash != "/assets/app.js"


def test_trend_adapter_uses_provider_pagination_flags_not_returned_item_count():
    adapter = TrendCoffeeAdapter()
    pages = iter(
        (
            {
                "statusCode": 200,
                "result": {
                    "items": [{"slug": "first", "name": "First", "variants": []}],
                    "hasNext": True,
                    "hasPrevios": False,
                },
            },
            {
                "statusCode": 200,
                "result": {
                    "items": [{"slug": "second", "name": "Second", "variants": []}],
                    "hasNext": False,
                    "hasPrevios": True,
                },
            },
        )
    )

    batches = tuple(adapter.iter_menu_pages(lambda page, size: next(pages), size=1))

    assert [batch.records[0].resource_id for batch in batches] == ["first", "second"]


def test_trend_adapter_builds_only_initial_qr_payment_request():
    adapter = TrendCoffeeAdapter()

    assert adapter.build_request(
        "payment.initiate",
        {"order": "example-order", "paymentMethod": "bank-transfer"},
    ) == {"order": "example-order", "paymentMethod": "bank-transfer"}
    with pytest.raises(ValueError, match="allowlisted"):
        adapter.build_request("payment.initiate", {"order": "example-order"})
    with pytest.raises(ValueError, match="allowlisted"):
        adapter.build_request(
            "payment.initiate",
            {
                "order": "example-order",
                "paymentMethod": "bank-transfer",
                "amount": 35000,
            },
        )


def test_trend_adapter_builds_only_allowlisted_take_out_order_request():
    adapter = TrendCoffeeAdapter()

    assert adapter.build_request(
        "order.place",
        {
            "order_type": "take-out",
            "branch_slug": "ba9355f797",
            "items": [
                {
                    "quantity": 1,
                    "variant_slug": "d5de540d4c",
                    "note": "ít đá",
                }
            ],
        },
    ) == {
        "type": "take-out",
        "timeLeftTakeOut": 0,
        "deliveryTo": "",
        "deliveryPhone": "",
        "table": "",
        "branch": "ba9355f797",
        "owner": "",
        "approvalBy": "",
        "orderItems": [
            {
                "quantity": 1,
                "variant": "d5de540d4c",
                "promotion": None,
                "note": "ít đá",
            }
        ],
        "voucher": None,
        "description": "",
    }
    with pytest.raises(ValueError, match="allowlisted"):
        adapter.build_request(
            "order.place",
            {
                "order_type": "take-out",
                "branch_slug": "ba9355f797",
                "items": [],
            },
        )
    with pytest.raises(ValueError, match="allowlisted"):
        adapter.build_request(
            "order.place",
            {
                "order_type": "take-out",
                "branch_slug": "ba9355f797",
                "items": [
                    {
                        "quantity": 0,
                        "variant_slug": "d5de540d4c",
                        "note": "",
                    }
                ],
            },
        )


@pytest.mark.parametrize(
    "payload",
    (
        {
            "order_type": "take-out",
            "branch_slug": "",
            "items": [{"quantity": 1, "variant_slug": "d5de540d4c", "note": "ít đá"}],
        },
        {
            "order_type": "take-out",
            "branch_slug": "ba9355f797",
            "items": [{"quantity": 1, "variant_slug": "d5de540d4c", "note": ""}],
        },
        {
            "order_type": "take-out",
            "branch_slug": "ba9355f797",
            "items": [{"quantity": 1, "variant_slug": "d5de540d4c", "note": "ít đá"}],
            "coupon": "unexpected",
        },
    ),
)
def test_trend_adapter_rejects_empty_or_extra_take_out_order_fields(payload):
    with pytest.raises(ValueError, match="allowlisted"):
        TrendCoffeeAdapter().build_request("order.place", payload)


def test_trend_adapter_rejects_whitespace_only_allowlisted_fields():
    adapter = TrendCoffeeAdapter()

    with pytest.raises(ValueError, match="allowlisted"):
        adapter.build_request(
            "payment.initiate",
            {"order": " ", "paymentMethod": "bank-transfer"},
        )
    with pytest.raises(ValueError, match="allowlisted"):
        adapter.build_request(
            "order.place",
            {
                "order_type": "take-out",
                "branch_slug": "ba9355f797",
                "items": [{"quantity": 1, "variant_slug": "d5de540d4c", "note": " "}],
            },
        )


def test_trend_adapter_normalizes_reconstructed_order_and_payment_examples():
    adapter = TrendCoffeeAdapter()

    order = adapter.normalize("order", load_fixture("order.json"))
    payment = adapter.normalize("payment", load_fixture("payment.json"))

    assert order.records[0].payload["status"] == "pending"
    assert payment.records[0].payload == {
        "qrCode": "redacted-qr-code",
        "slug": "example-payment",
        "status": "pending",
        "order": "example-order",
    }
