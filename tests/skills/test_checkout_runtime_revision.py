"""Bind saved-draft checkout to the inference snapshot, preserving write guards."""

import json

import pytest
from scripts.benchmark_ordering_voice import OrderingFixture

from openjarvis.agents.intent import IntentState
from openjarvis.core.conversation import agent_turn_scope, conversation_scope
from tests.skills.test_checkout_argument_recovery import _prepare


def _fixture():
    fixture = OrderingFixture()
    tool = next(t for t in fixture.tools if t.spec.name == "skill_trendcoffee-checkout")
    return fixture, tool, IntentState(fixture.tools)


def _arguments(**cart):
    return dict(
        order_type="take-out",
        table="",
        order_note="",
        customer_message="Fixture checkout.",
        **cart,
    )


def test_changed_draft_after_inference_does_not_get_silently_approved():
    fixture, tool, state = _fixture()
    with conversation_scope("checkout-snapshot-race"), agent_turn_scope() as nonce:
        fixture.seed(cart=True)
        before = fixture.cart.current_snapshot()
        state.runtime_context = {**tool.agent_context(), "turn_nonce": nonce}
        assert fixture.cart.edit_without_display(
            "set_order_note", {"order_note": "Touch edit"}
        ).success

        call = _prepare(state, tool, **_arguments(cart_revision=999))
        assert json.loads(call.arguments)["cart_revision"] == before["revision"]
        result = tool.execute(**json.loads(call.arguments))

        assert not result.success
        assert result.content == "cart revision is stale or empty"
        assert result.metadata["execution_started"] is False
        assert fixture.shop.calls == []
        assert fixture.cart.current_snapshot()["order_note"] == "Touch edit"


def test_replacement_lines_need_no_saved_draft_or_injected_revision():
    fixture, tool, state = _fixture()
    with conversation_scope("checkout-replacement-lines"), agent_turn_scope() as nonce:
        state.runtime_context = {"turn_nonce": nonce}
        call = _prepare(
            state,
            tool,
            **_arguments(
                cart_lines=[
                    {
                        "variant_id": "latte",
                        "name": "Latte",
                        "quantity": 1,
                        "unit_price": 51000,
                        "note": "",
                        "size": "",
                    }
                ]
            ),
        )
        assert "cart_revision" not in json.loads(call.arguments)
        result = tool.execute(**json.loads(call.arguments))
        assert result.success
        assert len([c for c in fixture.shop.calls if c.get("method") == "POST"]) == 2


def test_runtime_revision_binding_does_not_retry_an_unknown_write():
    fixture, tool, state = _fixture()
    fixture.shop.timeout_write = True
    with (
        conversation_scope("checkout-bound-unknown-write"),
        agent_turn_scope() as nonce,
    ):
        fixture.seed(cart=True)
        before = fixture.cart.current_snapshot()
        state.runtime_context = {**tool.agent_context(), "turn_nonce": nonce}
        arguments = _arguments(cart_revision=999)
        call = _prepare(state, tool, **arguments)
        assert json.loads(call.arguments)["cart_revision"] == before["revision"]
        result = tool.execute(**json.loads(call.arguments))
        state.observe(call, result)

        assert not result.success and result.metadata["outcome_unknown"]
        with pytest.raises(ValueError, match="target already attempted"):
            _prepare(state, tool, **arguments)
        assert len([c for c in fixture.shop.calls if c.get("method") == "POST"]) == 1


def test_binding_keeps_conflicting_cart_inputs_invalid():
    fixture, tool, state = _fixture()
    with (
        conversation_scope("checkout-conflicting-selection"),
        agent_turn_scope() as nonce,
    ):
        fixture.seed(cart=True)
        before = fixture.cart.current_snapshot()
        state.runtime_context = {**tool.agent_context(), "turn_nonce": nonce}
        call = _prepare(
            state,
            tool,
            **_arguments(cart_revision=999, cart_lines=before["lines"]),
        )
        result = tool.execute(**json.loads(call.arguments))

        assert not result.success
        assert result.content.startswith("invalid_skill_arguments:")
        assert result.metadata["execution_started"] is False
        assert fixture.shop.calls == []
        assert fixture.cart.current_snapshot() == before
