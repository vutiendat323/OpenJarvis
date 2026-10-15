"""A rejected checkout input can be repaired before any merchant write."""

from __future__ import annotations

import json

import pytest

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
