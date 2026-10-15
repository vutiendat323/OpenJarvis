"""A refused checkout names the order this conversation already awaits payment for.

On 2026-10-13 a repeated checkout was refused ("cart revision already
consumed") while the customer's order and its QR were on screen, and the model
told the customer no order had been made.
"""

from __future__ import annotations

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import ToolResult
from tests.skills.test_skills import (
    _add_checkout_tea,
    _checkout_http_harness,
    _run_checkout_turn,
)

_SHOWN = {"order_id": "order-1", "status": "pending", "total": 55000}


def _failing_write() -> ToolResult:
    return ToolResult(tool_name="http_request", content="read timed out", success=False)


def test_consumed_revision_refusal_carries_the_shown_order():
    cart, _, executor, manifest = _checkout_http_harness([_failing_write()], "POST")
    cart.shown_order = lambda: _SHOWN

    with conversation_scope("checkout-shown-order"):
        revision = _add_checkout_tea(cart)
        _run_checkout_turn(executor, manifest, revision)
        retry = _run_checkout_turn(executor, manifest, revision)

    content = retry.step_results[-1].content
    assert not retry.success
    assert content.startswith("cart revision already consumed")
    assert '"order_id": "order-1"' in content
    assert "awaiting payment" in content


def test_failed_step_also_carries_the_shown_order():
    cart, _, executor, manifest = _checkout_http_harness([_failing_write()], "POST")
    cart.shown_order = lambda: _SHOWN

    with conversation_scope("checkout-shown-order-step"):
        first = _run_checkout_turn(executor, manifest, _add_checkout_tea(cart))

    assert not first.success
    assert first.step_results[-1].content.startswith("read timed out")
    assert '"order_id": "order-1"' in first.step_results[-1].content


def test_without_a_shown_order_the_refusal_is_unchanged():
    cart, _, executor, manifest = _checkout_http_harness([_failing_write()], "POST")

    with conversation_scope("checkout-no-shown-order"):
        revision = _add_checkout_tea(cart)
        _run_checkout_turn(executor, manifest, revision)
        retry = _run_checkout_turn(executor, manifest, revision)

    assert retry.step_results[-1].content == "cart revision already consumed"
