"""A dispatched checkout runs every step through a barge-in; the session end stops it.

Spec 2026-10-17 barge-in §5.3/§5.8. Live 2026-10-17 12:00–12:05: four orders
were created and their payments initiated, but the customer's "Hủy đơn" ended
the turn and the checkout skipped its QR step, so the ledger said ``unknown``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from openjarvis.agents._stubs import committed_dispatch
from openjarvis.core import conversation
from openjarvis.core.conversation import (
    agent_turn_scope,
    conversation_scope,
    current_conversation_id,
    revoke_committed_turns,
)
from openjarvis.core.events import EventBus
from openjarvis.core.types import ToolResult
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.skills.types import SkillManifest, SkillStep
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools import evidence
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.display import DisplayCartTool
from openjarvis.tools.http_request import HttpRequestTool

MENU = "https://shop.example/menu"
ORDERS = "https://shop.example/orders"
PAYMENT = "https://shop.example/payment"


class _Display(BaseTool):
    def __init__(self, name):
        self.tool_id = name
        self._name = name
        self.calls = 0

    @property
    def spec(self):
        return ToolSpec(
            name=self._name,
            description="test display",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params):
        del params
        self.calls += 1
        return ToolResult(tool_name=self._name, content="shown", success=True)


def _end_turn_now():
    """The customer barged in: the dispatching turn is no longer active."""
    with conversation._TURN_LOCK:
        state = conversation._TURN_STATE.get()
        state.active = False
        conversation._ACTIVE_TURNS.pop(current_conversation_id(), None)


def _step(tool, arguments=None):
    return SkillStep(tool_name=tool, arguments_template=json.dumps(arguments or {}))


def _checkout(monkeypatch, *, on_send=None):
    cart = DisplayCartTool()
    cart._bus = EventBus()
    http = HttpRequestTool()
    bill, qr = _Display("display_bill"), _Display("display_payment_qr")
    sent = []

    def send(method, url, **kwargs):
        del kwargs
        sent.append((method, url))
        if on_send is not None:
            on_send(method, url)
        return httpx.Response(
            201, json={"ok": True}, request=httpx.Request(method, url)
        )

    monkeypatch.setattr(http, "_request_following_redirects", send)
    monkeypatch.setattr("openjarvis.tools.http_request.check_ssrf", lambda _url: None)
    manifest = SkillManifest(
        name="checkout",
        checkout=True,
        steps=[
            _step("http_request", {"url": MENU, "method": "GET"}),
            _step("http_request", {"url": ORDERS, "method": "POST", "body": "{}"}),
            _step("display_bill"),
            _step("http_request", {"url": PAYMENT, "method": "POST", "body": "{}"}),
            _step("display_payment_qr"),
        ],
    )
    tools = [cart, http, bill, qr]
    executor = SkillExecutor(ToolExecutor(tools))
    SystemBuilder._inject_checkout_guard(tools + [SkillTool(manifest, executor)])
    return cart, http, executor, manifest, sent, qr


def _add(cart):
    result = cart.execute(
        action="add",
        item={
            "variant_id": "coffee",
            "name": "Coffee",
            "unit_price": 35000,
            "quantity": 1,
        },
    )
    assert result.success
    return result.metadata["cart_revision"]


def _run(executor, manifest, cart, *, committed=True):
    revision = _add(cart)
    with agent_turn_scope() as nonce:
        context = {"turn_nonce": nonce, "cart_revision": revision}
        if not committed:
            return executor.run(manifest, initial_context=context)
        with committed_dispatch():
            return executor.run(manifest, initial_context=context)


def _cut_on(target):
    def on_send(method, url):
        if url == target:
            _end_turn_now()

    return on_send


def _posts(sent):
    return [url for method, url in sent if method == "POST"]


def test_checkout_cut_after_the_order_post_runs_every_step(monkeypatch):
    cart, _, executor, manifest, sent, qr = _checkout(
        monkeypatch, on_send=_cut_on(ORDERS)
    )
    with conversation_scope("barge-after-post"):
        result = _run(executor, manifest, cart)

    assert result.success
    assert _posts(sent) == [ORDERS, PAYMENT]
    assert qr.calls == 1


def test_checkout_cut_during_the_reads_still_creates_the_order(monkeypatch):
    cart, _, executor, manifest, sent, qr = _checkout(
        monkeypatch, on_send=_cut_on(MENU)
    )
    with conversation_scope("barge-during-reads"):
        result = _run(executor, manifest, cart)

    assert result.success
    assert _posts(sent) == [ORDERS, PAYMENT]
    assert qr.calls == 1


def test_outside_a_committed_dispatch_a_cut_turn_still_stops_checkout(monkeypatch):
    cart, _, executor, manifest, sent, qr = _checkout(
        monkeypatch, on_send=_cut_on(ORDERS)
    )
    with conversation_scope("barge-uncommitted"):
        result = _run(executor, manifest, cart, committed=False)

    assert not result.success
    assert result.step_results[-1].content.startswith("checkout turn expired")
    assert _posts(sent) == [ORDERS]
    assert qr.calls == 0


def test_session_end_stops_a_committed_checkout_before_payment(monkeypatch):
    def on_send(method, url):
        if url == ORDERS:
            revoke_committed_turns(current_conversation_id())

    cart, _, executor, manifest, sent, qr = _checkout(monkeypatch, on_send=on_send)
    with conversation_scope("session-ends"):
        result = _run(executor, manifest, cart)

    assert not result.success
    assert result.step_results[-1].content.startswith("checkout turn expired")
    assert _posts(sent) == [ORDERS]
    assert qr.calls == 0
    assert result.context["_checkout_write_attempted"] is True


def test_a_raw_write_outside_a_reserved_checkout_is_still_refused(monkeypatch):
    _, http, _, _, sent, _ = _checkout(monkeypatch)
    with conversation_scope("raw-write"), agent_turn_scope(), committed_dispatch():
        result = http.execute(url=ORDERS, method="POST", body="{}")

    assert not result.success
    assert sent == []


def test_a_consumed_revision_is_still_refused_in_a_committed_dispatch(monkeypatch):
    cart, _, executor, manifest, _, _ = _checkout(monkeypatch)
    with conversation_scope("consumed"):
        revision = _add(cart)
        with agent_turn_scope() as nonce, committed_dispatch():
            context = {"turn_nonce": nonce, "cart_revision": revision}
            assert executor.run(manifest, initial_context=context).success
        with agent_turn_scope() as nonce, committed_dispatch():
            with pytest.raises(ValueError, match="already consumed"):
                cart.begin_checkout(nonce, revision, manifest.manifest_bytes())


@pytest.fixture(autouse=True)
def _reset_evidence():
    evidence.reset()
    yield
    evidence.reset()
