"""Exercise configured intent with real skills, fake HTTP and local displays."""

import json

import pytest
from scripts.benchmark_ordering_voice import OrderingFixture

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.conversation import conversation_scope
from tests.agents.fake_engine import FakeEngine
from tests.agents.test_intent_normalization import invoke


class CheckoutEngine(FakeEngine):
    """Scripted intent, copying the actual nonce/revision required by checkout."""

    def __init__(self):
        super().__init__([{}, {}])

    def generate(self, messages, **kwargs):
        if self.call_count == 0:
            call = invoke(
                "skill_trendcoffee-add-to-cart",
                "CHECKOUT_NOW",
                items=[{"contains": "cà phê sữa", "quantity": 2, "note": ""}],
                open_cart=True,
                finish_turn=True,
                customer_message="Đã thêm hai cà phê sữa.",
            )
        else:
            runtime = next(
                json.loads(
                    m.content.split("<runtime_context>")[1].split("</runtime_context>")[
                        0
                    ]
                )
                for m in messages
                if "<runtime_context>" in m.content
            )
            call = invoke(
                "skill_trendcoffee-checkout",
                "CHECKOUT_NOW",
                cart_revision=runtime["draft_cart"]["revision"],
                turn_nonce="model-copied-the-wrong-nonce",
                order_type="take-out",
                table="",
                order_note="",
                update_order_type=True,
                customer_message="Đơn mang đi đã tạo, bạn quét mã để thanh toán.",
            )
        self._responses[self.call_count] = {"tool_calls": [call]}
        return super().generate(messages, **kwargs)


@pytest.mark.parametrize(
    "query",
    [
        "thanh toán 2 cà phê sữa mang đi",
        "bạn thanh toán 2 cà phê sữa mang đi",
    ],
)
def test_checkout_uses_existing_skills_until_verified_payment_display(query):
    fixture = OrderingFixture()
    with conversation_scope("intent-fixture-" + query):
        result = OrchestratorAgent(
            CheckoutEngine(),
            "fake",
            tools=fixture.tools,
            bus=fixture.bus,
            max_turns=2,
            intent_normalization=True,
        ).run(query)
    assert all(r.success for r in result.tool_results), result.tool_results
    assert result.metadata["normalized_intent"] == "CHECKOUT_NOW"
    assert result.metadata["intent_satisfied"] is True
    assert result.metadata["execution_path"] == [
        "skill_trendcoffee-add-to-cart",
        "skill_trendcoffee-checkout",
    ]
    assert fixture.displays[-1]["view"] == "payment_qr"
    assert fixture.shop.order["type"] == "take-out"
    assert fixture.shop.order["description"] == "AI order"
    assert fixture.shop.order["orderItems"][0]["quantity"] == 2
    assert len([p for p in fixture.shop.calls if p.get("method") == "POST"]) == 2
    assert "customer_message" not in result.tool_results[0].metadata
    assert not result.metadata.get("max_turns_exceeded")


def test_contract_metadata_survives_visible_tool_wrappers():
    from openjarvis.agents.intent import IntentState

    fixture = OrderingFixture()
    state = IntentState(fixture.tools)
    assert {target["tool"] for target in state.targets["ADD_TO_CART"]} == {
        "skill_trendcoffee-add-to-cart",
        "display_cart",
    }
    assert state.targets["CHECKOUT_NOW"] == [
        {"tool": "skill_trendcoffee-checkout", "arguments": {}},
    ]


@pytest.mark.parametrize("action", ["view", "remove"])
def test_standalone_cart_view_and_batch_remove_finish_without_exhausting_turns(action):
    fixture = OrderingFixture()
    with conversation_scope("standalone-cart-" + action):
        fixture.seed(cart=True)
        assert fixture.cart.edit_without_display(
            "add",
            {
                "items": [
                    {
                        "variant_id": variant,
                        "name": variant,
                        "unit_price": 52_000,
                        "quantity": 1,
                        "note": "",
                        "size": "",
                    }
                    for variant in ("drink-a", "drink-b")
                ]
            },
        ).success
        arguments = {"action": action, "customer_message": "Mời bạn xem giỏ hàng."}
        if action == "remove":
            arguments["line_ids"] = [
                line["line_id"]
                for line in fixture.cart.current_snapshot()["lines"]
                if line["unit_price"] == 52_000
            ]
            arguments["customer_message"] = "Đã bỏ hai món giá 52.000đ khỏi giỏ."
        engine = FakeEngine(
            [{"tool_calls": [invoke("display_cart", "NONE", **arguments)]}]
        )
        result = OrchestratorAgent(
            engine,
            "fake",
            tools=fixture.tools,
            bus=fixture.bus,
            max_turns=2,
            intent_normalization=True,
        ).run(
            "mở giỏ hàng nha"
            if action == "view"
            else "Trong giỏ hàng bạn loại giúp tôi những món có giá 52.000đ ra."
        )
        snapshot = fixture.cart.current_snapshot()
    assert result.content == arguments["customer_message"]
    assert result.turns == 1
    assert not result.metadata.get("max_turns_exceeded")
    assert result.metadata["terminal_reason"] == "ANSWER"
    assert all(r.success for r in result.tool_results)
    assert fixture.displays[-1]["view"] == "cart"
    assert snapshot["total"] == (206_000 if action == "view" else 102_000)
    assert len(snapshot["lines"]) == (3 if action == "view" else 1)
    assert fixture.shop.calls == []


@pytest.mark.parametrize(
    "edit, expected",
    [
        ({"action": "update", "quantity": 10}, {"quantity": 10, "total": 350_000}),
        ({"action": "update", "note": "ít đá"}, {"note": "ít đá"}),
        (
            {"action": "set_order_note", "order_note": "giao tại cửa"},
            {"order_note": "giao tại cửa"},
        ),
        (
            {"action": "set_order_type", "order_type": "take-out"},
            {"order_type": "take-out"},
        ),
        (
            {"action": "set_table", "table": "table-1", "table_name": "1"},
            {"table": "table-1", "order_type": "at-table"},
        ),
        (
            {"action": "set_pickup_time", "pickup_minutes": 10},
            {"pickup_minutes": 10, "order_type": "take-out"},
        ),
    ],
)
def test_standalone_cart_edits_finish_even_without_a_dining_choice(edit, expected):
    fixture = OrderingFixture()
    message = "Đã sửa giỏ hàng. Bạn muốn dùng tại quán hay mang đi?"
    with conversation_scope("cart-edit-" + json.dumps(edit)):
        assert fixture.cart.edit_without_display(
            "add",
            {
                "item": {
                    "variant_id": "americano",
                    "name": "Cà phê đen Americano",
                    "unit_price": 35_000,
                    "quantity": 1,
                    "note": "",
                    "size": "",
                }
            },
        ).success
        arguments = dict(edit, customer_message=message)
        if edit["action"] == "update":
            arguments["line_id"] = fixture.cart.current_snapshot()["lines"][0][
                "line_id"
            ]
        result = OrchestratorAgent(
            FakeEngine([{"tool_calls": [invoke("display_cart", "NONE", **arguments)]}]),
            "fake",
            tools=fixture.tools,
            bus=fixture.bus,
            max_turns=2,
            intent_normalization=True,
        ).run("Điều chỉnh giỏ hàng")
        snapshot = fixture.cart.current_snapshot()
    assert result.content == message
    assert result.turns == 1
    assert not result.metadata.get("max_turns_exceeded")
    assert all(r.success for r in result.tool_results)
    for key, value in expected.items():
        actual = (
            snapshot["lines"][0][key] if key in {"quantity", "note"} else snapshot[key]
        )
        assert actual == value
    assert fixture.shop.calls == []


def test_verified_menu_skill_finishes_in_one_model_round():
    fixture = OrderingFixture()
    engine = FakeEngine(
        [
            {
                "tool_calls": [
                    invoke(
                        "skill_trendcoffee-menu",
                        "NONE",
                        itemTerms=["latte"],
                        categoryTerms=[],
                        minPrice=0,
                        maxPrice=1_000_000_000,
                        displayMode="filtered",
                        customer_message="Mời bạn xem menu.",
                    )
                ]
            }
        ]
    )
    with conversation_scope("menu-acknowledgement-regression"):
        result = OrchestratorAgent(
            engine,
            "fake",
            tools=fixture.tools,
            bus=fixture.bus,
            intent_normalization=True,
        ).run("menu latte")
    assert engine.call_count == 1
    assert result.content == "Mời bạn xem menu."
    assert all(r.success for r in result.tool_results)
    assert result.metadata["terminal_reason"] == "ANSWER"
    assert fixture.displays[-1]["view"] == "menu"


def test_add_request_can_save_dining_choice_after_nonterminal_add():
    fixture = OrderingFixture()
    engine = FakeEngine(
        [
            {
                "tool_calls": [
                    invoke(
                        "display_cart",
                        "ADD_TO_CART",
                        action="add",
                        finish_turn=False,
                        open_cart=False,
                        item={
                            "variant_id": "latte",
                            "name": "Latte",
                            "unit_price": 51_000,
                            "quantity": 1,
                            "note": "",
                            "size": "",
                        },
                    )
                ]
            },
            {
                "tool_calls": [
                    invoke(
                        "display_cart",
                        "ADD_TO_CART",
                        action="set_order_type",
                        order_type="take-out",
                        finish_turn=True,
                        customer_message="Món mang đi đã được lưu vào giỏ.",
                    )
                ]
            },
        ]
    )
    with conversation_scope("add-with-dining-regression"):
        fixture.seed(menu=True)
        result = OrchestratorAgent(
            engine,
            "fake",
            tools=fixture.tools,
            bus=fixture.bus,
            intent_normalization=True,
        ).run("Cho một latte mang đi")
        snapshot = fixture.cart.current_snapshot()
    assert all(r.success for r in result.tool_results)
    assert snapshot["order_type"] == "take-out"
    assert snapshot["lines"][0]["quantity"] == 1
    assert result.content == "Món mang đi đã được lưu vào giỏ."
    assert fixture.shop.calls == []


def test_checkout_argument_correction_before_execution_creates_only_one_order():
    class CorrectingEngine(CheckoutEngine):
        def __init__(self):
            super().__init__()
            self._responses.append({})

        def generate(self, messages, **kwargs):
            result = super().generate(messages, **kwargs)
            if self.call_count == 2:
                call = result["tool_calls"][0]
                args = json.loads(call["arguments"])
                args.pop("order_type")
                call["arguments"] = json.dumps(args)
            return result

    fixture = OrderingFixture()
    with conversation_scope("checkout-preexecution-correction"):
        result = OrchestratorAgent(
            CorrectingEngine(),
            "fake",
            tools=fixture.tools,
            bus=fixture.bus,
            max_turns=3,
            intent_normalization=True,
        ).run("thanh toán 2 cà phê sữa mang đi")
    assert result.tool_results[1].success is False
    assert result.tool_results[1].metadata["execution_started"] is False
    assert result.tool_results[-1].success is True
    assert result.metadata["intent_satisfied"] is True
    assert len([p for p in fixture.shop.calls if p.get("method") == "POST"]) == 2
