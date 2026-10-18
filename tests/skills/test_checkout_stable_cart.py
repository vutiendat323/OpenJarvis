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
from openjarvis.tools.await_requests import AwaitEarlierRequestsTool
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
    def __init__(
        self, monkeypatch, conversation, *, on_post=None, accepts_cart_lines=False
    ):
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
            accepts_cart_lines=accepts_cart_lines,
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

    def checkout(self, lease, run, revision, **extra):
        def go():
            _RUN_WORKER_LEASE.set(lease)
            with (
                conversation_scope(self.conversation),
                voice_run_scope(run),
                agent_turn_scope() as nonce,
                committed_dispatch(),
            ):
                arguments = {"turn_nonce": nonce, **extra}
                if revision is not None:
                    arguments["cart_revision"] = revision
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


LINES = [{"variant_id": "tea", "name": "Tea", "unit_price": 35000, "quantity": 1}]
COFFEE_LINES = [
    {"variant_id": "coffee", "name": "Coffee", "unit_price": 35000, "quantity": 1}
]


def _superseded(result):
    return (
        not result.success
        and result.metadata.get("dispatched") is False
        and result.metadata.get("checkout_superseded") is True
        and result.content.startswith("checkout_superseded:")
    )


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


def test_a_change_still_thinking_when_the_snapshot_ends_drops_the_checkout(
    monkeypatch, conversation
):
    """C1: request C began during the wait and writes only after A has ended."""
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    lease_b, lease_c = fx.lease(), fx.lease()

    with ThreadPoolExecutor(2) as pool:
        waiting = pool.submit(fx.checkout, lease_b, run_b, revision)
        time.sleep(0.15)
        run_c = RUN_TRACKER.begin(conversation, "add cake")  # model still thinking
        time.sleep(0.05)
        RUN_TRACKER.end(conversation, run_a)  # the snapshot is over
        time.sleep(0.2)
        assert not waiting.done(), "the checkout went ahead without C's change"
        late = fx.add_as(lease_c, run_c, "cake")
        result = waiting.result(5)
    RUN_TRACKER.end(conversation, run_c)

    assert late.success, late.content
    assert not result.success
    assert result.metadata["cart_changed"] is True
    assert result.metadata["dispatched"] is False
    assert fx.posts() == []
    with conversation_scope(conversation):
        lines = fx.cart.current_snapshot()["lines"]
    assert [line["variant_id"] for line in lines] == ["coffee", "cake"]


def test_a_question_asked_during_the_wait_only_delays_the_checkout(
    monkeypatch, conversation
):
    """Ruling 16 cost: a late run that writes nothing delays, never drops."""
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b = RUN_TRACKER.begin(conversation, "pay")
    lease_b = fx.lease()

    with ThreadPoolExecutor(2) as pool:
        waiting = pool.submit(fx.checkout, lease_b, run_b, revision)
        time.sleep(0.1)
        run_q = RUN_TRACKER.begin(conversation, "how long does it take?")
        RUN_TRACKER.end(conversation, run_a)
        time.sleep(0.2)
        assert not waiting.done()
        RUN_TRACKER.end(conversation, run_q)
        result = waiting.result(5)

    assert result.success, result.content
    assert fx.order_posts() == ["total=35000"]


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
    # Ruling 17: refused before dispatch, ahead of the revision claim that
    # refused it before ("cart revision already consumed").
    assert _superseded(refused[0]), refused[0].content
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
    assert _superseded(refused), refused.content
    assert fx.posts() == [(ORDERS, "total=70000"), (PAYMENT, "")]
    assert ACTION_LEDGER.entries(conversation) == []


def test_two_waiting_checkouts_that_set_the_order_type_create_one_order(
    monkeypatch, conversation
):
    """The production shape: update_order_type bumps the revision on its own.

    B1's begin_checkout applies the type and bumps the revision; B2, waiting on
    the same snapshot, then holds a revision that is no longer current.
    """
    fx = _Fixture(monkeypatch, conversation)
    revision = fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add tea")
    run_b1 = RUN_TRACKER.begin(conversation, "pay")
    run_b2 = RUN_TRACKER.begin(conversation, "pay again")
    lease_a, lease_1, lease_2 = fx.lease(), fx.lease(), fx.lease()
    dining = {"update_order_type": True, "order_type": "take-out"}

    def background():
        time.sleep(0.2)
        try:
            assert fx.add_as(lease_a, run_a, "tea").success
        finally:
            RUN_TRACKER.end(conversation, run_a)

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(fx.checkout, lease_1, run_b1, revision, **dining)
        second = pool.submit(fx.checkout, lease_2, run_b2, revision, **dining)
        added = pool.submit(background)
        results = [first.result(10), second.result(10)]
        added.result(5)

    assert fx.order_posts() == ["total=70000"]
    assert [r.success for r in results].count(True) == 1
    refused = next(r for r in results if not r.success)
    assert _superseded(refused), refused.content
    # The type reached begin_checkout, so the revision did move on its own.
    assert fx.cart._order_types[conversation] == "take-out"
    assert len([1 for url, _ in fx.posts() if url == ORDERS]) == 1


def test_two_waiting_cart_lines_checkouts_create_one_order(monkeypatch, conversation):
    """C2: the replacement-lines path has no revision claim to refuse B2."""
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    fx.seed()
    run_a = RUN_TRACKER.begin(conversation, "add cake")
    run_b1 = RUN_TRACKER.begin(conversation, "tea, pay")
    run_b2 = RUN_TRACKER.begin(conversation, "tea, pay (repeated)")
    lines = {"cart_lines": LINES, "order_type": "take-out", "table": ""}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(fx.checkout, fx.lease(), run_b1, None, **lines)
        second = pool.submit(fx.checkout, fx.lease(), run_b2, None, **lines)
        time.sleep(0.15)
        RUN_TRACKER.end(conversation, run_a)
        results = [first.result(10), second.result(10)]

    assert fx.order_posts() == ["total=35000"]
    assert [r.success for r in results].count(True) == 1, [r.content for r in results]
    refused = next(r for r in results if not r.success)
    assert _superseded(refused), refused.content
    assert ACTION_LEDGER.entries(conversation) == []


def test_a_checkout_that_waits_while_another_is_ordering_is_refused(
    monkeypatch, conversation
):
    """Ruling 17: B1 already holds the freeze when B2 begins its wait."""
    order_started, release = threading.Event(), threading.Event()

    def on_post():
        order_started.set()
        assert release.wait(5)

    fx = _Fixture(monkeypatch, conversation, on_post=on_post, accepts_cart_lines=True)
    fx.seed()
    run_b1 = RUN_TRACKER.begin(conversation, "tea, pay")
    lines = {"cart_lines": LINES, "order_type": "take-out", "table": ""}

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(fx.checkout, fx.lease(), run_b1, None, **lines)
        assert order_started.wait(5)
        # Asked again while B1 is already ordering.
        run_b2 = RUN_TRACKER.begin(conversation, "tea, pay (repeated)")
        second = pool.submit(fx.checkout, fx.lease(), run_b2, None, **lines)
        time.sleep(0.2)
        release.set()
        results = [first.result(10), second.result(10)]

    assert results[0].success, results[0].content
    assert _superseded(results[1]), results[1].content
    assert fx.order_posts() == ["total=35000"]


def test_a_correction_cannot_claim_completion_while_an_earlier_order_is_posting(
    monkeypatch, conversation
):
    """N1: await_earlier_requests must see the real checkout's frozen POST."""
    monkeypatch.setattr("openjarvis.tools.await_requests.AWAIT_EARLIER_SECONDS", 0.05)
    order_started, release = threading.Event(), threading.Event()

    def on_post():
        order_started.set()
        assert release.wait(5)

    fx = _Fixture(monkeypatch, conversation, on_post=on_post)
    revision = fx.seed()
    checkout = RUN_TRACKER.begin(conversation, "pay")
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(fx.checkout, fx.lease(), checkout, revision)
        try:
            assert order_started.wait(5)
            correction = RUN_TRACKER.begin(conversation, "remove the coffee")
            with conversation_scope(conversation), voice_run_scope(correction):
                result = AwaitEarlierRequestsTool().execute()
            assert not result.success
            assert result.content.startswith("earlier_requests_still_running:")
        finally:
            release.set()
        assert first.result(10).success
    RUN_TRACKER.end(conversation, checkout)
    with conversation_scope(conversation), voice_run_scope(correction):
        result = AwaitEarlierRequestsTool().execute()
    assert result.success
    assert result.content == "earlier_requests_finished"
    assert fx.posts() == [(ORDERS, "total=35000"), (PAYMENT, "")]
    RUN_TRACKER.end(conversation, correction)


@pytest.mark.parametrize("end_first", [False, True])
@pytest.mark.parametrize("cart_lines", [False, True])
def test_a_repeat_pay_begun_during_ordering_is_refused_after_the_order_finishes(
    monkeypatch, conversation, end_first, cart_lines
):
    """C2 residual: begin B2 before thaw, delay its checkout until after thaw."""
    order_started, release = threading.Event(), threading.Event()

    def on_post():
        order_started.set()
        assert release.wait(5)

    fx = _Fixture(
        monkeypatch, conversation, on_post=on_post, accepts_cart_lines=cart_lines
    )
    revision = fx.seed()
    extra = {"cart_lines": LINES, "order_type": "take-out", "table": ""}
    run_b1 = RUN_TRACKER.begin(conversation, "tea, pay")
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(
            fx.checkout,
            fx.lease(),
            run_b1,
            None if cart_lines else revision,
            **(extra if cart_lines else {}),
        )
        try:
            assert order_started.wait(5)
            run_b2 = RUN_TRACKER.begin(conversation, "tea, pay (repeated)")
        finally:
            release.set()
        result = first.result(10)
    assert result.success, result.content
    if end_first:
        RUN_TRACKER.end(conversation, run_b1)

    repeated = fx.checkout(
        fx.lease(),
        run_b2,
        None if cart_lines else revision,
        **(extra if cart_lines else {}),
    )

    assert _superseded(repeated), repeated.content
    assert fx.posts() == [(ORDERS, "total=35000"), (PAYMENT, "")]
    RUN_TRACKER.end(conversation, run_b1)
    RUN_TRACKER.end(conversation, run_b2)


def test_a_later_checkout_is_not_refused_by_an_earlier_finished_one(
    monkeypatch, conversation
):
    """A run begun after the earlier checkout finished may create a new order."""
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    fx.seed()
    lines = {"cart_lines": LINES, "order_type": "take-out", "table": ""}
    run_b1 = RUN_TRACKER.begin(conversation, "tea, pay")
    assert fx.checkout(fx.lease(), run_b1, None, **lines).success
    RUN_TRACKER.end(conversation, run_b1)
    run_b2 = RUN_TRACKER.begin(conversation, "another tea, pay")

    result = fx.checkout(fx.lease(), run_b2, None, **lines)

    assert result.success, result.content
    assert fx.order_posts() == ["total=35000", "total=35000"]


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


@pytest.mark.parametrize("initially_empty", [False, True])
def test_cart_lines_preserves_a_touch_correction_during_the_wait(
    monkeypatch, conversation, initially_empty
):
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    if not initially_empty:
        fx.seed()
    earlier = RUN_TRACKER.begin(conversation, "read the menu")
    checkout = RUN_TRACKER.begin(conversation, "one coffee, pay")
    waiting = threading.Event()
    original_wait = RUN_TRACKER._cond.wait

    def observe_wait(timeout=None):
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(RUN_TRACKER._cond, "wait", observe_wait)
    with ThreadPoolExecutor(1) as pool:
        ordered = pool.submit(
            fx.checkout,
            fx.lease(),
            checkout,
            None,
            cart_lines=COFFEE_LINES,
            order_type="take-out",
            table="",
        )
        try:
            assert waiting.wait(2)
            with conversation_scope(conversation):
                if initially_empty:
                    fx.add()
                line_id = fx.cart.current_snapshot()["lines"][0]["line_id"]
                assert fx.cart.edit_without_display(
                    "update", {"line_id": line_id, "quantity": 2}
                ).success
                corrected = fx.cart.current_snapshot()
                assert corrected["total"] == 70000
        finally:
            RUN_TRACKER.end(conversation, earlier)
        result = ordered.result(5)

    assert not result.success, result.content
    assert "cart_changed_during_checkout" in result.content
    assert result.metadata["execution_started"] is False
    assert fx.sent == []
    with conversation_scope(conversation):
        assert fx.cart.current_snapshot() == corrected


@pytest.mark.parametrize("initially_empty", [False, True])
def test_cart_lines_does_not_restore_a_voice_removal_before_freeze(
    monkeypatch, conversation, initially_empty
):
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    if not initially_empty:
        fx.seed()
    checkout = RUN_TRACKER.begin(conversation, "one coffee, pay")
    freeze = RUN_TRACKER.freeze
    corrected = []

    def freeze_after_correction(cid, run):
        if run == checkout and not corrected:
            correction = RUN_TRACKER.begin(conversation, "no coffee")

            def clear():
                _RUN_WORKER_LEASE.set(fx.lease())
                with conversation_scope(conversation), voice_run_scope(correction):
                    return fx.executor.execute(
                        ToolCall("remove", "display_cart", '{"action":"clear"}')
                    )

            try:
                assert _in_copy(clear).success
                corrected.append(fx.cart.current_snapshot())
                assert corrected[0]["lines"] == []
            finally:
                RUN_TRACKER.end(conversation, correction)
        return freeze(cid, run)

    monkeypatch.setattr(RUN_TRACKER, "freeze", freeze_after_correction)
    result = fx.checkout(
        fx.lease(),
        checkout,
        None,
        cart_lines=COFFEE_LINES,
        order_type="take-out",
        table="",
    )

    assert corrected
    assert not result.success, result.content
    assert "cart_changed_during_checkout" in result.content
    assert result.metadata["execution_started"] is False
    assert fx.sent == []
    with conversation_scope(conversation):
        assert fx.cart.current_snapshot() == corrected[0]


def test_cart_lines_checks_for_touch_edits_at_the_atomic_claim(
    monkeypatch, conversation
):
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    fx.seed()
    checkout = RUN_TRACKER.begin(conversation, "one coffee, pay")
    begin_checkout = fx.cart.begin_checkout
    corrected = []

    def claim_after_touch(*args, **kwargs):
        # Already frozen and inside the tool gate: touch edits use the cart lock.
        with voice_run_scope(""):
            line_id = fx.cart.current_snapshot()["lines"][0]["line_id"]
            assert fx.cart.edit_without_display(
                "update", {"line_id": line_id, "quantity": 2}
            ).success
            corrected.append(fx.cart.current_snapshot())
        return begin_checkout(*args, **kwargs)

    monkeypatch.setattr(fx.cart, "begin_checkout", claim_after_touch)
    result = fx.checkout(
        fx.lease(),
        checkout,
        None,
        cart_lines=COFFEE_LINES,
        order_type="take-out",
        table="",
    )

    assert not result.success, result.content
    assert "cart_changed_during_checkout" in result.content
    assert result.metadata["execution_started"] is False
    assert fx.sent == []
    with conversation_scope(conversation):
        assert fx.cart.current_snapshot() == corrected[0]


@pytest.mark.parametrize("seed", [False, True])
def test_cart_lines_can_replace_an_unchanged_draft(monkeypatch, conversation, seed):
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    if seed:
        fx.seed()
    checkout = RUN_TRACKER.begin(conversation, "one tea instead, pay")

    result = fx.checkout(
        fx.lease(),
        checkout,
        None,
        cart_lines=LINES,
        order_type="take-out",
        table="",
    )

    assert result.success, result.content
    assert fx.posts() == [(ORDERS, "total=35000"), (PAYMENT, "")]
    with conversation_scope(conversation):
        assert fx.cart.current_snapshot()["lines"][0]["variant_id"] == "tea"


@pytest.mark.parametrize("change", ["touch", "touch_noop", "settled", "evicted"])
def test_cart_lines_preserves_a_cart_change_before_checkout_is_called(
    monkeypatch, conversation, change
):
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    fx.seed()
    checkout = RUN_TRACKER.begin(conversation, "one coffee, pay")
    with conversation_scope(conversation):
        if change in {"touch", "touch_noop"}:
            line_id = fx.cart.current_snapshot()["lines"][0]["line_id"]
            assert fx.cart.edit_without_display(
                "update",
                {"line_id": line_id, "quantity": 1 if change == "touch_noop" else 2},
            ).success
        elif change == "settled":
            assert fx.cart.settle_current()
        else:
            for index in range(64):
                with conversation_scope(f"{conversation}-{index}"):
                    fx.add()
            assert fx.cart.current_snapshot()["lines"] == []
        corrected = fx.cart.current_snapshot()

    # Retrying within this run must not rebase its older replacement lines.
    lines = (
        [{**COFFEE_LINES[0], "quantity": 2}] if change == "touch_noop" else COFFEE_LINES
    )
    for _ in range(2):
        result = fx.checkout(
            fx.lease(),
            checkout,
            None,
            cart_lines=lines,
            order_type="take-out",
            table="",
        )
        assert not result.success, result.content
        assert "cart_changed_during_checkout" in result.content
        assert result.metadata["execution_started"] is False
    assert fx.sent == []
    with conversation_scope(conversation):
        assert fx.cart.current_snapshot() == corrected


def test_cart_lines_allows_the_runs_own_cart_edits(monkeypatch, conversation):
    fx = _Fixture(monkeypatch, conversation, accepts_cart_lines=True)
    fx.seed()
    checkout = RUN_TRACKER.begin(conversation, "add tea, pay")
    assert fx.add_as(fx.lease(), checkout, "tea").success
    result = fx.checkout(
        fx.lease(),
        checkout,
        None,
        cart_lines=COFFEE_LINES + LINES,
        order_type="take-out",
        table="",
    )
    assert result.success, result.content
    assert fx.posts() == [(ORDERS, "total=70000"), (PAYMENT, "")]
