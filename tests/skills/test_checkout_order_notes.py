"""Order annotations through the real checkout recipe, without live writes."""

import json

import pytest
from scripts.benchmark_ordering_voice import OrderingFixture

from openjarvis.core.conversation import agent_turn_scope, conversation_scope


@pytest.mark.parametrize("saved_draft", [False, True])
@pytest.mark.parametrize(
    ("customer_note", "expected"),
    [
        (
            "",
            "AI ORDER Vui lòng chờ khoảng 15 phút để nhận món tại quầy nhé.",
        ),
        (
            "Làm nhanh giúp mình",
            "AI ORDER Vui lòng chờ khoảng 15 phút để nhận món tại quầy nhé."
            " | Làm nhanh giúp mình",
        ),
        (
            'Không "đường"\n{giữ nguyên}',
            "AI ORDER Vui lòng chờ khoảng 15 phút để nhận món tại quầy nhé."
            ' | Không "đường"\n{giữ nguyên}',
        ),
    ],
)
def test_checkout_tags_order_and_preserves_customer_notes(
    saved_draft, customer_note, expected
):
    fixture = OrderingFixture()
    checkout = next(
        t for t in fixture.skills if t.spec.name == "skill_trendcoffee-checkout"
    )
    with conversation_scope(f"order-note-{saved_draft}-{customer_note}"):
        fixture.seed(cart=True)
        assert fixture.cart.edit_without_display(
            "set_order_note", {"order_note": customer_note}
        ).success
        assert fixture.cart.edit_without_display(
            "update",
            {
                "line_id": fixture.cart.current_snapshot()["lines"][0]["line_id"],
                "note": "Ít đá",
            },
        ).success
        snapshot = fixture.cart.current_snapshot()
        assert snapshot["order_note"] == customer_note
        cart_source = (
            {"cart_revision": snapshot["revision"]}
            if saved_draft
            else {"cart_lines": snapshot["lines"]}
        )
        with agent_turn_scope() as nonce:
            result = checkout.execute(
                **cart_source,
                order_type="take-out",
                table="",
                order_note=customer_note,
                turn_nonce=nonce,
                customer_message="Mời bạn quét mã để thanh toán.",
            )

    assert result.success, result.content
    writes = [p for p in fixture.shop.calls if p.get("method") == "POST"]
    assert len(writes) == 2
    assert json.loads(writes[0]["body"])["description"] == expected
    assert fixture.shop.order["description"] == expected
    assert fixture.shop.order["orderItems"][0]["note"] == "Ít đá"
    assert result.metadata["customer_message"] == "Mời bạn quét mã để thanh toán."
    assert fixture.displays[-1]["view"] == "payment_qr"
