"""Existing-order payment reads cannot create orders or affect another draft."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
import tomllib

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.core.types import ToolResult
from openjarvis.kiosk.presentation import PresentationSessionManager, TouchCheckout
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.loader import load_skill
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools import evidence
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.display import DisplayCartTool, DisplayPaymentQrTool
from tests.kiosk.test_presentation import _FakeMCPClient


def _order(oid="coffee", *, status="pending", payment_status="pending", qr="QR-FRESH"):
    return {
        "slug": oid,
        "status": status,
        "subtotal": 40000,
        "type": "take-out",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "table": None,
        "payment": {
            "slug": "payment-coffee",
            "statusCode": payment_status,
            "amount": 40000,
            "qrCode": qr,
        },
        "orderItems": [
            {
                "quantity": 1,
                "note": "",
                "variant": {
                    "slug": "milk-coffee",
                    "price": 40000,
                    "size": {"name": "standard"},
                    "product": {"name": "Milk coffee"},
                },
            }
        ],
    }


class _OrderHTTP(BaseTool):
    def __init__(self, order, *, ok=True):
        self.order, self.ok, self.calls = order, ok, []
        self.final_url = None

    @property
    def spec(self):
        return ToolSpec(name="http_request", description="Merchant read fixture")

    def execute(self, **params):
        self.calls.append(params)
        assert params.get("method", "GET") == "GET", (
            "Existing-order payment must never POST"
        )
        return ToolResult(
            tool_name="http_request",
            content=json.dumps({"result": self.order}) if self.ok else "HTTP 500",
            success=self.ok,
            metadata={
                "status_code": 200 if self.ok else 500,
                "final_url": self.final_url or params["url"],
            },
        )


def _presentation():
    manager = PresentationSessionManager(
        EventBus(), _FakeMCPClient(server_name="playwright")
    )
    session = manager.ensure("http://127.0.0.1:5173")
    cart = DisplayCartTool()
    cart._presentation = manager
    with conversation_scope("owner"):
        for oid, amount in [("coffee", 40000), ("choco", 50000)]:
            assert manager.publish(
                {
                    "view": "bill",
                    "order_id": oid,
                    "status": "pending",
                    "total": amount,
                    "order_type": "take-out",
                    "lines": [{"name": oid, "quantity": 1}],
                }
            ).success
            assert manager.publish(
                {
                    "view": "payment_qr",
                    "order_id": oid,
                    "payment_slug": "payment-" + oid,
                    "status": "pending",
                    "qr_code": "QR-CACHED-" + oid,
                    "total": amount,
                    "order_type": "take-out",
                    "lines": [{"name": oid, "quantity": 1}],
                }
            ).success
    return manager, session, cart


def _procedure(order=None, *, ok=True):
    from openjarvis.tools.existing_order import DisplayExistingOrderTool

    manager, session, cart = _presentation()
    qr = DisplayPaymentQrTool()
    qr._presentation = manager
    qr._payment_trusted_origins = SystemBuilder._parse_trusted_origins(
        "https://trendcoffee.net"
    )
    qr._cart_settler = cart.settle_current
    display = DisplayExistingOrderTool()
    display._presentation = manager
    http = _OrderHTTP(order if order is not None else _order(), ok=ok)
    tools = [cart, qr, display, http]
    SystemBuilder._inject_existing_order_display(tools)
    executor = SkillExecutor(ToolExecutor(tools))
    tool = SkillTool(load_skill("skills/trendcoffee-order-payment.toml"), executor)
    return manager, session, cart, http, tool


def test_new_receipt_and_menu_do_not_lose_owned_order_references():
    manager, _, cart = _presentation()
    with conversation_scope("owner"):
        manager.publish({"view": "menu"})
        orders = cart.agent_context()["created_orders"]
        assert [o["order_id"] for o in orders] == ["coffee", "choco"]
        assert all("qr_code" not in o and "conversation_id" not in o for o in orders)
        assert manager.shown_payment()["order_id"] == "choco"
    with conversation_scope("other-owner"):
        assert manager.known_orders() == []
        assert "created_orders" not in cart.agent_context()


def test_reopening_older_qr_reads_current_order_and_preserves_new_draft():
    _, session, cart, http, tool = _procedure()
    with conversation_scope("owner"):
        assert cart.execute(
            action="add",
            item={
                "variant_id": "tea",
                "name": "Tea",
                "unit_price": 35000,
                "quantity": 2,
            },
        ).success
        before = cart.current_snapshot()
        result = tool.execute(order_id="coffee")
        assert result.success
        assert result.metadata["continue_agent"]
        assert session.last_payload["order_id"] == "coffee"
        assert session.last_payload["qr_code"] == "QR-FRESH"
        assert cart.current_snapshot() == before
    assert len(http.calls) == 1
    assert http.calls[0]["url"].endswith("/orders/coffee")


def test_reopened_qr_with_a_reply_ends_the_turn_without_another_round():
    _, session, _, _, tool = _procedure()
    with conversation_scope("owner"):
        result = tool.execute(order_id="coffee", customer_message="Quét mã nhé.")
    assert result.success
    assert session.last_payload["qr_code"] == "QR-FRESH"
    assert result.metadata["customer_message"] == "Quét mã nhé."
    assert result.metadata.get("continue_agent") is not True


def test_paid_order_still_lets_the_agent_answer_despite_a_reply():
    # The reply was written before the read; it may expect a QR.
    _, session, _, _, tool = _procedure(
        _order(status="paid", payment_status="completed")
    )
    with conversation_scope("owner"):
        result = tool.execute(order_id="coffee", customer_message="Quét mã nhé.")
    assert result.success and session.last_payload["view"] == "bill"
    assert result.metadata["continue_agent"] is True


@pytest.mark.parametrize(
    "owner, oid", [("other-owner", "coffee"), ("owner", "unknown")]
)
def test_unowned_order_is_rejected_before_http(owner, oid):
    _, session, _, http, tool = _procedure()
    before = dict(session.last_payload)
    with conversation_scope(owner):
        result = tool.execute(order_id=oid)
    assert not result.success and "order_not_owned" in result.content
    assert http.calls == [] and session.last_payload == before


def test_reset_discards_the_previous_customers_order_references():
    manager, session, _, http, tool = _procedure()
    assert manager.reset(session.session_id)
    with conversation_scope("owner"):
        assert manager.known_orders() == []
        result = tool.execute(order_id="coffee")
    assert not result.success and http.calls == []


def test_paid_order_shows_paid_bill_without_payment_or_clearing_new_draft():
    manager, session, cart, http, tool = _procedure(
        _order(status="paid", payment_status="completed")
    )
    with conversation_scope("owner"):
        cart.execute(
            action="add",
            item={
                "variant_id": "tea",
                "name": "Tea",
                "unit_price": 35000,
                "quantity": 1,
            },
        )
        before = cart.current_snapshot()
        result = tool.execute(order_id="coffee")
        assert result.success
        assert (
            session.last_payload["view"] == "bill"
            and session.last_payload["status"] == "paid"
        )
        assert cart.current_snapshot() == before
        assert (
            next(o for o in manager.known_orders() if o["order_id"] == "coffee")[
                "status"
            ]
            == "paid"
        )
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "status,payment_status,qr",
    [
        ("canceled", "pending", "QR-FRESH"),
        ("pending", "expired", "QR-FRESH"),
        ("pending", "pending", ""),
        ("pending", "completed", "QR-FRESH"),
    ],
)
def test_unpayable_order_never_replaces_the_current_qr(status, payment_status, qr):
    _, session, _, http, tool = _procedure(
        _order(status=status, payment_status=payment_status, qr=qr)
    )
    before = dict(session.last_payload)
    with conversation_scope("owner"):
        result = tool.execute(order_id="coffee")
    assert not result.success
    assert session.last_payload == before
    assert len(http.calls) == 1


@pytest.mark.parametrize("display", [True, False])
@pytest.mark.parametrize("problem", ["canceled", "expired", "missing-qr", "window"])
def test_unpayable_displayed_order_removes_its_qr(problem, display):
    order = _order("choco")
    if problem == "canceled":
        order["status"] = "canceled"
    elif problem == "expired":
        order["payment"]["statusCode"] = "expired"
    elif problem == "missing-qr":
        order["payment"]["qrCode"] = ""
    else:
        order["createdAt"] = (
            datetime.now(timezone.utc) - timedelta(minutes=16)
        ).isoformat()
    _, session, _, _, tool = _procedure(order)
    with conversation_scope("owner"):
        result = tool.execute(order_id="choco", display=display)
    assert session.payment is None
    assert session.last_payload["order_id"] == "choco"
    assert session.last_payload["view"] == "bill"
    assert "qr_code" not in session.last_payload
    if display:
        assert not result.success


@pytest.mark.parametrize("later", ["read", "paid-publication", "paid-poll"])
def test_late_pending_read_cannot_replace_verified_paid_order(later, monkeypatch):
    manager, session, _, http, tool = _procedure()
    if later == "paid-poll":
        with conversation_scope("owner"):
            assert tool.execute(order_id="coffee").success
        http.calls.clear()
        manager.configure_touch_checkout(
            TouchCheckout(
                run=lambda **kwargs: None,
                tables_url="https://trendcoffee.net/tables",
                order_url="https://trendcoffee.net/orders/{order_id}",
                fetch_json=lambda url: {"result": _order(status="paid")},
            )
        )
    started, release = Event(), Event()
    original = http.execute

    def delayed(**params):
        result = original(**params)
        if len(http.calls) == 1:
            started.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(http, "execute", delayed)

    def first_read():
        with conversation_scope("owner"):
            return tool.execute(order_id="coffee")

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_read)
        try:
            assert started.wait(5)
            if later == "paid-poll":
                assert manager.check_payment(session.session_id) == "paid"
            else:
                with conversation_scope("owner"):
                    if later == "read":
                        http.order = _order(status="paid", payment_status="completed")
                        assert tool.execute(order_id="coffee").success
                    else:
                        assert manager.publish(
                            {"view": "bill", "order_id": "coffee", "status": "paid"}
                        ).success
        finally:
            release.set()
        assert not first.result(timeout=5).success
    assert session.last_payload["view"] == "bill"
    assert session.last_payload["status"] == "paid"
    with conversation_scope("owner"):
        assert manager.known_orders()[0]["status"] == "paid"


@pytest.mark.parametrize(
    "problem", ["wrong-order", "wrong-amount", "http-error", "expired-window"]
)
def test_failed_verification_does_not_show_a_payment(problem):
    order = _order()
    if problem == "wrong-order":
        order["slug"] = "someone-else"
    if problem == "wrong-amount":
        order["payment"]["amount"] = 1
    if problem == "expired-window":
        order["createdAt"] = (
            datetime.now(timezone.utc) - timedelta(minutes=16)
        ).isoformat()
    _, session, _, http, tool = _procedure(order, ok=problem != "http-error")
    before = dict(session.last_payload)
    with conversation_scope("owner"):
        result = tool.execute(order_id="coffee")
    assert not result.success
    assert session.last_payload == before and len(http.calls) == 1


def test_status_only_read_does_not_navigate():
    manager, session, _, http, tool = _procedure(
        _order(status="paid", payment_status="completed")
    )
    before = dict(session.last_payload)
    with conversation_scope("owner"):
        result = tool.execute(order_id="coffee", display=False)
        assert result.success
        assert json.loads(result.content)["status"] == "paid"
        assert (
            next(o for o in manager.known_orders() if o["order_id"] == "coffee")[
                "status"
            ]
            == "paid"
        )
    assert session.last_payload == before and len(http.calls) == 1


def test_preset_agent_can_read_and_show_an_older_order():
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from openjarvis.tools.display import DisplayClearTool, DisplayMenuTool
    from tests.agents.fake_engine import FakeEngine
    from tests.agents.test_intent_normalization import invoke

    _, session, cart, http, tool = _procedure()
    with open("configs/openjarvis/examples/ordering-kiosk-mcp.toml", "rb") as f:
        config = tomllib.load(f)
    enabled = config["tools"]["enabled"].split(",")
    active = config["skills"]["active"].split(",")
    internal = tool._executor._tool_executor
    candidates = [
        cart,
        http,
        DisplayMenuTool(),
        DisplayClearTool(),
        tool,
        internal.get_tool("display_payment_qr"),
        internal.get_tool("display_existing_order"),
    ]
    configured = [
        t
        for t in candidates
        if t.spec.name in enabled or t.spec.name.removeprefix("skill_") in active
    ]
    visible = SystemBuilder._model_visible_tools(
        configured, config["tools"]["model_hidden"]
    )
    engine = FakeEngine(
        [
            {
                "tool_calls": [
                    invoke("skill_trendcoffee-order-payment", "NONE", order_id="coffee")
                ]
            },
            {
                "content": json.dumps(
                    {
                        "_intent": "NONE",
                        "state": "ANSWER",
                        "message": "Quét QR đơn cà phê sữa để thanh toán nhé.",
                    }
                )
            },
        ]
    )
    with conversation_scope("owner"):
        result = OrchestratorAgent(
            engine, "fake", tools=visible, intent_normalization=True, max_turns=2
        ).run("Thanh toán đơn cà phê sữa trước.")
    assert result.tool_results and all(r.success for r in result.tool_results)
    assert result.metadata["execution_path"] == ["skill_trendcoffee-order-payment"]
    assert session.last_payload["order_id"] == "coffee"
    assert session.last_payload["qr_code"] == "QR-FRESH"
    assert len(http.calls) == 1


def test_order_reference_storage_is_bounded():
    manager, _, _ = _presentation()
    with conversation_scope("owner"):
        for index in range(25):
            manager.publish(
                {"view": "bill", "order_id": f"order-{index}", "status": "pending"}
            )
        orders = manager.known_orders()
        assert len(orders) == 20
        assert orders[0]["order_id"] == "order-5"
        assert orders[-1]["order_id"] == "order-24"


def test_merchant_javascript_timestamp_keeps_original_payment_deadline():
    order = _order()
    order["createdAt"] = (
        datetime.now(timezone.utc).strftime("%a %b %d %Y %H:%M:%S GMT%z") + " (UTC)"
    )
    _, session, _, _, tool = _procedure(order)
    with conversation_scope("owner"):
        assert tool.execute(order_id="coffee").success
    assert session.last_payload["created_at"] == order["createdAt"]


def test_redirected_order_read_cannot_claim_paid_or_display_a_qr():
    _, session, _, http, tool = _procedure(
        _order(status="paid", payment_status="completed")
    )
    http.final_url = "https://untrusted.example/orders/coffee"
    before = dict(session.last_payload)
    with conversation_scope("owner"):
        result = tool.execute(order_id="coffee")
    assert not result.success
    assert "order_source_mismatch" in result.content
    assert session.last_payload == before


@pytest.fixture(autouse=True)
def _reset_evidence():
    evidence.reset()
    yield
    evidence.reset()


def test_preset_agent_reopens_an_older_qr_in_one_model_round():
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from tests.agents.fake_engine import FakeEngine
    from tests.agents.test_intent_normalization import invoke

    _, session, cart, http, tool = _procedure()
    internal = tool._executor._tool_executor
    visible = [cart, tool, internal.get_tool("display_payment_qr")]
    # Only one round is scripted: a second model call would fail the run.
    engine = FakeEngine(
        [
            {
                "tool_calls": [
                    invoke(
                        "skill_trendcoffee-order-payment",
                        "NONE",
                        order_id="coffee",
                        customer_message="Quét mã để thanh toán nhé.",
                    )
                ]
            }
        ]
    )
    with conversation_scope("owner"):
        result = OrchestratorAgent(
            engine, "fake", tools=visible, intent_normalization=True, max_turns=2
        ).run("Mở lại QR đơn cà phê sữa.")
    assert result.turns == 1
    assert result.content == "Quét mã để thanh toán nhé."
    assert session.last_payload["qr_code"] == "QR-FRESH"
