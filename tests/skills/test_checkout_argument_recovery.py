"""A rejected checkout input can be repaired before any merchant write."""

from __future__ import annotations

import json

import pytest
from scripts.benchmark_ordering_voice import OrderingFixture

from openjarvis.agents.intent import IntentState
from openjarvis.core.conversation import agent_turn_scope, conversation_scope
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.skills.tool_adapter import SkillTool
from tests.skills.test_skills import _add_checkout_tea, _checkout_http_harness

LINE = {"variant_id": "tea", "name": "Tea", "unit_price": 55000, "quantity": 1}


def _checkout(result):
    cart, http, executor, manifest = _checkout_http_harness([result], "POST")
    manifest.accepts_cart_lines = True
    manifest.metadata = {
        "openjarvis": {
            "intent_contract": {"name": "CHECKOUT_NOW"},
            "runtime_arguments": {"turn_nonce": "turn_nonce"},
        }
    }
    # The screen may still show an older order awaiting payment.
    cart.shown_order = lambda: {"order_id": "old-order", "status": "pending"}
    tool = SkillTool(manifest, executor)
    return cart, http, tool, IntentState([tool])


def _prepare(state, tool, **arguments):
    [call] = state.prepare(
        [
            ToolCall(
                id="checkout",
                name=tool.spec.name,
                arguments=json.dumps({"_intent": "CHECKOUT_NOW", **arguments}),
            )
        ]
    )
    return call


@pytest.mark.parametrize("invalid", [{"cart_lines": [LINE], "cart_revision": 0}, {}])
@pytest.mark.parametrize("repair", ["cart_lines", "cart_revision"])
def test_checkout_input_refusal_allows_repair_in_the_same_turn(invalid, repair):
    # Catches validation being classified as an attempted write, which prevents
    # the Agent from fixing cart_lines + cart_revision=0 in the same turn.
    cart, http, tool, state = _checkout(
        ToolResult(tool_name="http_request", content='{"order_id":"new-order"}')
    )
    with conversation_scope("checkout-argument-repair"), agent_turn_scope() as nonce:
        revision = _add_checkout_tea(cart)
        before = cart.current_snapshot()
        state.runtime_context = {"turn_nonce": nonce}
        call = _prepare(state, tool, **invalid)

        refusal = tool.execute(**json.loads(call.arguments))

        assert not refusal.success
        assert refusal.metadata.get("execution_started") is False
        assert refusal.content.startswith("invalid_skill_arguments:")
        assert "cart_lines" in refusal.content and "cart_revision" in refusal.content
        assert "old-order" not in refusal.content
        assert http.calls == []
        assert cart.current_snapshot() == before
        state.observe(call, refusal)

        fixed = (
            {"cart_lines": [LINE]}
            if repair == "cart_lines"
            else {"cart_revision": revision}
        )
        corrected = _prepare(state, tool, **fixed)
        result = tool.execute(**json.loads(corrected.arguments))
        state.observe(corrected, result)

        assert result.success and state.reached
        assert len(http.calls) == 1
        assert http.calls[0]["method"] == "POST"
        with pytest.raises(ValueError, match="requested outcome already completed"):
            _prepare(state, tool, **fixed)
        assert len(http.calls) == 1


def test_checkout_write_with_unknown_outcome_still_blocks_retry():
    cart, http, tool, state = _checkout(
        ToolResult(tool_name="http_request", content="Request timed out", success=False)
    )
    with conversation_scope("checkout-unknown-write"), agent_turn_scope() as nonce:
        revision = _add_checkout_tea(cart)
        state.runtime_context = {"turn_nonce": nonce}
        call = _prepare(state, tool, cart_revision=revision)
        result = tool.execute(**json.loads(call.arguments))
        state.observe(call, result)

        assert not result.success and result.metadata["outcome_unknown"]
        assert result.metadata.get("execution_started") is not False
        assert len(http.calls) == 1
        with pytest.raises(ValueError, match="target already attempted"):
            _prepare(state, tool, cart_revision=revision)
        assert len(http.calls) == 1


@pytest.mark.parametrize("refusal", ["dining", "stale_revision"])
def test_checkout_draft_refusal_allows_same_turn_repair_before_any_write(refusal):
    fixture = OrderingFixture()
    tool = next(
        tool for tool in fixture.tools if tool.spec.name == "skill_trendcoffee-checkout"
    )
    state = IntentState(fixture.tools)
    with conversation_scope("checkout-draft-repair"), agent_turn_scope() as nonce:
        fixture.seed(cart=True, order_type="" if refusal == "dining" else "take-out")
        before = fixture.cart.current_snapshot()
        arguments = {
            "cart_revision": before["revision"]
            + (1 if refusal == "stale_revision" else 0),
            "order_type": "take-out",
            "table": "",
            "order_note": "",
            "customer_message": "Fixture checkout completed.",
        }
        state.runtime_context = {"turn_nonce": nonce}
        call = _prepare(state, tool, **arguments)
        result = tool.execute(**json.loads(call.arguments))

        assert not result.success
        assert result.content == (
            "checkout order type does not match the draft"
            if refusal == "dining"
            else "cart revision is stale or empty"
        )
        assert fixture.shop.calls == []
        assert fixture.cart.current_snapshot() == before
        assert result.metadata.get("execution_started") is False
        state.observe(call, result)

        arguments.update(cart_revision=before["revision"], update_order_type=True)
        corrected = _prepare(state, tool, **arguments)
        completed = tool.execute(**json.loads(corrected.arguments))
        state.observe(corrected, completed)

        assert completed.success and state.reached
        assert fixture.shop.order["type"] == "take-out"
        assert len([c for c in fixture.shop.calls if c.get("method") == "POST"]) == 2
        with pytest.raises(ValueError, match="requested outcome already completed"):
            _prepare(state, tool, **arguments)
        assert len([c for c in fixture.shop.calls if c.get("method") == "POST"]) == 2
