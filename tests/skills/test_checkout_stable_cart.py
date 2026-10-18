"""A checkout waits until other runs stop changing the cart (spec P8, §7.7).

Live 2026-10-17: "thanh toán" said while "thêm sinh tố" was still thinking
would otherwise order the cart without the smoothies.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from openjarvis.agents._stubs import (
    _RUN_WORKER_LEASE,
    AgentToolGate,
    AgentWorkerLease,
    committed_dispatch,
)
from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.core.conversation import agent_turn_scope, conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.core.runs import RUN_TRACKER, voice_run_scope
from openjarvis.core.types import ToolCall
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.skills.types import SkillManifest
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools import _stubs as tool_stubs
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.display import DisplayCartTool
from openjarvis.tools.http_request import HttpRequestTool
from tests.skills.test_checkout_barge_in import (
    MENU,
    ORDERS,
    PAYMENT,
    _Display,
    _step,
)


class _Fixture:
    def __init__(self, monkeypatch, conversation, *, on_post=None):
        self.conversation = conversation
        self.cart = DisplayCartTool()
        self.cart._bus = EventBus()
        http = HttpRequestTool()
        bill, qr = _Display("display_bill"), _Display("display_payment_qr")
        self.sent: list[tuple[str, str, str]] = []
        self._on_post = on_post

        def send(method, url, **kwargs):
            self.sent.append((method, url, str(kwargs.get("content"))))
            if method == "POST" and url == ORDERS and self._on_post is not None:
                self._on_post()
            return httpx.Response(
                201, json={"ok": True}, request=httpx.Request(method, url)
            )

        monkeypatch.setattr(http, "_request_following_redirects", send)
        monkeypatch.setattr(
            "openjarvis.tools.http_request.check_ssrf", lambda _url: None
        )
        manifest = SkillManifest(
            name="checkout",
            checkout=True,
            steps=[
                _step("http_request", {"url": MENU, "method": "GET"}),
                _step(
                    "http_request",
                    {
                        "url": ORDERS,
                        "method": "POST",
                        "body": "total={draft_cart.total}",
                    },
                ),
                _step("display_bill"),
                _step("http_request", {"url": PAYMENT, "method": "POST", "body": ""}),
                _step("display_payment_qr"),
            ],
        )
        inner_tools = [self.cart, http, bill, qr]
        skill = SkillTool(manifest, SkillExecutor(ToolExecutor(inner_tools)))
        SystemBuilder._inject_checkout_guard(inner_tools + [skill])
        self.executor = ToolExecutor([self.cart, skill])
        self.gate = AgentToolGate()

    def lease(self):
        return AgentWorkerLease(self.gate, conversation_id=self.conversation)

    def add(self, variant="coffee"):
        result = self.cart.execute(
            action="add",
            item={
                "variant_id": variant,
                "name": variant.title(),
                "unit_price": 35000,
                "quantity": 1,
            },
        )
        assert result.success
        return result.metadata["cart_revision"]

    def seed(self):
        with conversation_scope(self.conversation):
            return self.add()

    def posts(self):
        return [(url, body) for method, url, body in self.sent if method == "POST"]

    def order_posts(self):
        return [body for url, body in self.posts() if url == ORDERS]

    def checkout(self, lease, run, revision):
        def go():
            _RUN_WORKER_LEASE.set(lease)
            with (
                conversation_scope(self.conversation),
                voice_run_scope(run),
                agent_turn_scope() as nonce,
                committed_dispatch(),
            ):
                arguments = {"turn_nonce": nonce, "cart_revision": revision}
                return self.executor.execute(
                    ToolCall(f"checkout-{run}", "skill_checkout", json.dumps(arguments))
                )

        return _in_copy(go)

    def add_as(self, lease, run, variant):
        def go():
            _RUN_WORKER_LEASE.set(lease)
            with conversation_scope(self.conversation), voice_run_scope(run):
                arguments = {
                    "action": "add",
                    "item": {
                        "variant_id": variant,
                        "name": variant.title(),
                        "unit_price": 35000,
                        "quantity": 1,
                    },
                }
                return self.executor.execute(
                    ToolCall(f"add-{run}", "display_cart", json.dumps(arguments))
                )

        return _in_copy(go)


def _in_copy(fn):
    import contextvars

    return contextvars.copy_context().run(fn)


@pytest.fixture
def conversation():
    name = f"stable-{time.monotonic_ns()}"
    yield name
    RUN_TRACKER.close(name)


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached")


def test_checkout_waits_for_a_background_run_then_orders_the_full_cart(
    monkeypatch, conversation
):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    lease_a, lease_b = fx.lease(), fx.lease()

    def background():
        time.sleep(0.2)
        try:
            assert fx.add_as(lease_a, run_a, "tea").success
        finally:
            RUN_TRACKER.end(conversation, run_a)

    with ThreadPoolExecutor(2) as pool:
        added = pool.submit(background)
        started = time.monotonic()
        result = pool.submit(fx.checkout, lease_b, run_b, revision).result(10)
        added.result(5)

    assert result.success, result.content
    assert time.monotonic() - started >= 0.2
    assert fx.order_posts() == ["total=70000"]


def test_checkout_gives_up_after_the_stable_wait(monkeypatch, conversation):
    monkeypatch.setattr(tool_stubs, "STABLE_CART_WAIT_SECONDS", 0.2)
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")  # never ends
    run_b = RUN_TRACKER.begin(conversation, "pay")

    started = time.monotonic()
    result = fx.checkout(fx.lease(), run_b, revision)

    assert 0.2 <= time.monotonic() - started < 2.0
    assert not result.success
    assert result.metadata["cart_not_settled"] is True
    assert result.metadata["dispatched"] is False
    assert result.content.startswith("cart_not_settled:")
    assert fx.posts() == []
    assert ACTION_LEDGER.entries(conversation) == []
    RUN_TRACKER.end(conversation, run_a)


def test_a_new_runs_cart_write_drops_the_waiting_checkout(monkeypatch, conversation):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    lease_b, lease_c = fx.lease(), fx.lease()

    with ThreadPoolExecutor(2) as pool:
        waiting = pool.submit(fx.checkout, lease_b, run_b, revision)
        time.sleep(0.15)  # B is waiting for A
        run_c = RUN_TRACKER.begin(conversation, "add cake")
        assert fx.add_as(lease_c, run_c, "cake").success
        result = waiting.result(5)

    assert not result.success
    assert result.metadata["cart_changed"] is True
    assert result.metadata["dispatched"] is False
    assert result.content.startswith("cart_changed_during_checkout:")
    assert fx.posts() == []
    for run in (run_a, run_c):
        RUN_TRACKER.end(conversation, run)


def test_a_cart_write_cannot_slip_in_between_freeze_and_dispatch(
    monkeypatch, conversation
):
    order_started, release = threading.Event(), threading.Event()
    events: list[str] = []

    def on_post():
        events.append("order-post")
        order_started.set()
        assert release.wait(5)

    fx = _Fixture(monkeypatch, conversation, on_post=on_post)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    lease_b, lease_c = fx.lease(), fx.lease()

    def add_cake():
        run_c = RUN_TRACKER.begin(conversation, "add cake")
        try:
            result = fx.add_as(lease_c, run_c, "cake")
            events.append("cake-written")
            return result
        finally:
            RUN_TRACKER.end(conversation, run_c)

    with ThreadPoolExecutor(2) as pool:
        checkout = pool.submit(fx.checkout, lease_b, run_b, revision)
        time.sleep(0.1)
        RUN_TRACKER.end(conversation, run_a)  # B is stable and starts
        assert order_started.wait(5)
        late = pool.submit(add_cake)  # arrives while the checkout is frozen
        time.sleep(0.3)
        assert events == ["order-post"]
        release.set()
        result = checkout.result(5)
        assert late.result(5).success

    assert result.success, result.content
    assert events == ["order-post", "cake-written"]
    assert fx.order_posts() == ["total=35000"]


def test_two_waiting_checkouts_create_one_order(monkeypatch, conversation):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b1 = RUN_TRACKER.begin(conversation, "pay")
    run_b2 = RUN_TRACKER.begin(conversation, "pay again")
    lease_1, lease_2 = fx.lease(), fx.lease()

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(fx.checkout, lease_1, run_b1, revision)
        second = pool.submit(fx.checkout, lease_2, run_b2, revision)
        time.sleep(0.15)
        RUN_TRACKER.end(conversation, run_a)
        results = [first.result(10), second.result(10)]

    assert len(fx.order_posts()) == 1
    succeeded = [r for r in results if r.success]
    refused = [r for r in results if not r.success]
    assert len(succeeded) == 1 and len(refused) == 1
    assert "already consumed" in refused[0].content
    assert refused[0].metadata["execution_started"] is False
    assert ACTION_LEDGER.entries(conversation) == []


def test_two_waiting_checkouts_after_an_add_create_one_order_with_the_full_cart(
    monkeypatch, conversation
):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b1 = RUN_TRACKER.begin(conversation, "pay")
    run_b2 = RUN_TRACKER.begin(conversation, "pay again")
    lease_a, lease_1, lease_2 = fx.lease(), fx.lease(), fx.lease()

    def background():
        time.sleep(0.2)
        try:
            assert fx.add_as(lease_a, run_a, "tea").success
        finally:
            RUN_TRACKER.end(conversation, run_a)

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(fx.checkout, lease_1, run_b1, revision)
        second = pool.submit(fx.checkout, lease_2, run_b2, revision)
        added = pool.submit(background)
        results = [first.result(10), second.result(10)]
        added.result(5)

    assert fx.order_posts() == ["total=70000"]
    assert [r.success for r in results].count(True) == 1
    refused = next(r for r in results if not r.success)
    assert refused.metadata["execution_started"] is False
    assert fx.posts() == [(ORDERS, "total=70000"), (PAYMENT, "")]
    assert ACTION_LEDGER.entries(conversation) == []


def test_a_touch_edit_during_the_wait_is_not_carried_into_the_order(
    monkeypatch, conversation
):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    lease_a, lease_b = fx.lease(), fx.lease()

    def background():
        time.sleep(0.2)
        try:
            assert fx.add_as(lease_a, run_a, "tea").success
            with conversation_scope(conversation):  # a touch: no run id
                assert fx.cart.edit_without_display(
                    "set_order_note", {"order_note": "Touch edit"}
                ).success
        finally:
            RUN_TRACKER.end(conversation, run_a)

    with ThreadPoolExecutor(2) as pool:
        added = pool.submit(background)
        result = pool.submit(fx.checkout, lease_b, run_b, revision).result(10)
        added.result(5)

    assert not result.success
    assert result.content == "cart revision is stale or empty"
    assert result.metadata["execution_started"] is False
    assert fx.posts() == []


def test_a_new_runs_write_between_stable_and_freeze_is_not_carried(
    monkeypatch, conversation
):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    freeze = RUN_TRACKER.freeze
    slipped = []

    def freeze_after_a_slip(cid, run):
        if run == run_b and not slipped:
            run_c = RUN_TRACKER.begin(conversation, "add cake")
            slipped.append(run_c)
            with voice_run_scope(run_c):  # lands after STABLE, before the freeze
                assert fx.cart.execute(
                    action="add",
                    item={
                        "variant_id": "cake",
                        "name": "Cake",
                        "unit_price": 35000,
                        "quantity": 1,
                    },
                ).success
        return freeze(cid, run)

    monkeypatch.setattr(RUN_TRACKER, "freeze", freeze_after_a_slip)
    lease_a, lease_b = fx.lease(), fx.lease()

    def background():
        time.sleep(0.2)
        try:
            assert fx.add_as(lease_a, run_a, "tea").success
        finally:
            RUN_TRACKER.end(conversation, run_a)

    with ThreadPoolExecutor(2) as pool:
        added = pool.submit(background)
        result = pool.submit(fx.checkout, lease_b, run_b, revision).result(10)
        added.result(5)

    assert slipped
    assert not result.success
    assert result.content == "cart revision is stale or empty"
    assert fx.posts() == []
    RUN_TRACKER.end(conversation, slipped[0])


def test_a_checkout_does_not_follow_a_revision_that_was_already_stale(
    monkeypatch, conversation
):
    fx = _Fixture(monkeypatch, conversation)
    stale = fx.seed()
    with conversation_scope(conversation):
        fx.add("tea")  # changed before checkout began
    run_b = RUN_TRACKER.begin(conversation, "pay")

    result = fx.checkout(fx.lease(), run_b, stale)

    assert not result.success
    assert result.content == "cart revision is stale or empty"
    assert fx.posts() == []


def test_a_checkout_without_other_runs_dispatches_at_once(monkeypatch, conversation):
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_b = RUN_TRACKER.begin(conversation, "pay")

    started = time.monotonic()
    result = fx.checkout(fx.lease(), run_b, revision)

    assert result.success, result.content
    assert time.monotonic() - started < 1.0
    assert fx.order_posts() == ["total=35000"]
    # The freeze is released: another run can write straight away.
    assert RUN_TRACKER.freeze(conversation, "someone-else")
