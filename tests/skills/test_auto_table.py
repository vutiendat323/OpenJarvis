"""Select an unspecified dine-in table from a fresh merchant read."""

import json
from datetime import datetime, timezone

import httpx
import pytest
import tomllib

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus, EventType
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.loader import load_skill
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools import evidence
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.display import (
    DisplayBillTool,
    DisplayCartTool,
    DisplayPaymentQrTool,
)
from openjarvis.tools.http_request import HttpRequestTool
from openjarvis.workflow.engine import WorkflowEngine


def test_first_expression_preserves_source_order():
    assert (
        SkillExecutor._render_json_value({"$first": "{rows}"}, {"rows": [17, 2]}) == 17
    )


@pytest.mark.parametrize("rows", [[], None, {"first": 17}])
def test_first_expression_refuses_missing_or_non_array_values(rows):
    with pytest.raises(ValueError, match="first requires a non-empty array"):
        SkillExecutor._render_json_value({"$first": "{rows}"}, {"rows": rows})


def _selection(monkeypatch, rows, *, status=200):
    cart = DisplayCartTool()
    cart._bus = EventBus()
    http = HttpRequestTool()
    requests = []

    def send(method, url, **kwargs):
        requests.append((method, url))
        assert method == "GET"
        return httpx.Response(
            status,
            json={"result": rows},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(http, "_request_following_redirects", send)
    monkeypatch.setattr("openjarvis.tools.http_request.check_ssrf", lambda url: None)
    executor = SkillExecutor(
        ToolExecutor([cart, http]), workflow_engine=WorkflowEngine()
    )
    tool = SkillTool(load_skill("skills/trendcoffee-auto-table.toml"), executor)
    return cart, tool, requests


def _add(cart):
    assert cart.execute(
        action="add",
        item={
            "variant_id": "coffee",
            "name": "Coffee",
            "quantity": 1,
            "unit_price": 35000,
        },
    ).success


def test_auto_table_uses_first_exact_available_row_in_one_call(monkeypatch):
    cart, tool, requests = _selection(
        monkeypatch,
        [
            {"slug": "occupied", "name": "1", "status": "reserved"},
            {"slug": "unknown", "name": "3", "status": "Available"},
            {"slug": "floor-upstairs", "name": "17", "status": "available"},
            {"slug": "floor-ground", "name": "2", "status": "available"},
        ],
    )
    with conversation_scope("auto-table"):
        _add(cart)
        result = tool.execute(
            finish_turn=False, customer_message="Selected a free table."
        )
        assert result.success, result.content
        snapshot = cart.current_snapshot()
        assert (snapshot["table"], snapshot["table_name"], snapshot["order_type"]) == (
            "floor-upstairs",
            "17",
            "at-table",
        )
        assert result.metadata["continue_agent"] is True
        assert result.metadata["cart_revision"] == snapshot["revision"]
        assert json.loads(result.content)["cart"]["table"] == "floor-upstairs"
    assert requests == [
        ("GET", "https://trendcoffee.net/api/latest/tables?branch=ba9355f797")
    ]


@pytest.mark.parametrize(
    "rows,status",
    [
        ([], 200),
        ([{"slug": "busy", "name": "1", "status": "reserved"}], 200),
        ([{"slug": "unknown", "name": "1", "status": "Available"}], 200),
        ([{"slug": "a", "name": "1", "status": "available"}] * 2, 200),
        ([{"slug": "a", "status": "available"}], 200),
        ([{"slug": "a", "name": "1", "status": "available"}], 500),
    ],
)
def test_failed_table_read_or_no_free_table_leaves_draft_unchanged(
    monkeypatch, rows, status
):
    cart, tool, requests = _selection(monkeypatch, rows, status=status)
    with conversation_scope("auto-table-failure"):
        _add(cart)
        before = cart.current_snapshot()
        result = tool.execute(
            finish_turn=False, customer_message="Selected a free table."
        )
        assert not result.success
        assert cart.current_snapshot() == before
    assert len(requests) == 1


def test_preset_auto_table_then_checkout_uses_two_inference_rounds(monkeypatch):
    from openjarvis.agents.orchestrator import OrchestratorAgent
    from tests.agents.fake_engine import FakeEngine
    from tests.agents.test_intent_normalization import invoke

    rows = [
        {"slug": "busy", "name": "1", "status": "reserved"},
        {"slug": "floor-upstairs", "name": "17", "status": "available"},
    ]
    cart, auto, _ = _selection(monkeypatch, rows)
    http = auto._executor._tool_executor.get_tool("http_request")
    requests, events = [], []
    bus = cart._bus
    bus.subscribe(EventType.DISPLAY_UPDATE, events.append)
    variant = {
        "slug": "coffee",
        "price": 35000,
        "size": {"name": "standard"},
        "product": {"name": "Coffee"},
    }

    def send(method, url, **kwargs):
        body = json.loads(kwargs["content"]) if kwargs.get("content") else None
        requests.append((method, url, body))
        if "/tables?" in url:
            data = rows
        elif "/menu/" in url:
            data = {
                "hasNext": False,
                "items": [
                    {
                        "menuItems": [
                            {
                                "product": {
                                    "isActive": True,
                                    "variants": [variant],
                                }
                            }
                        ]
                    }
                ],
            }
        elif "/orders/public" in url:
            data = {
                "slug": "order-1",
                "status": "pending",
                "subtotal": 35000,
                "type": body["type"],
                "description": body["description"],
                "table": {"slug": body["table"], "name": "17"},
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "orderItems": [
                    {"variant": variant, "quantity": 1, "note": "", "promotion": None}
                ],
            }
        elif "/payment/initiate/public" in url:
            data = {
                "slug": "payment-1",
                "paymentMethod": "bank-transfer",
                "statusCode": "pending",
                "amount": 35000,
                "qrCode": "QR-VERIFIED",
            }
        else:
            raise AssertionError(url)
        return httpx.Response(
            201 if "/orders/public" in url else 200,
            json={"result": data},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(http, "_request_following_redirects", send)
    bill, qr = DisplayBillTool(), DisplayPaymentQrTool()
    bill._bus = qr._bus = bus
    qr._payment_trusted_origins = (("https", "trendcoffee.net", 443),)
    executor = SkillExecutor(
        ToolExecutor([cart, http, bill, qr]), workflow_engine=WorkflowEngine()
    )
    auto._executor = executor
    checkout = SkillTool(load_skill("skills/trendcoffee-checkout.toml"), executor)
    candidates = [cart, http, bill, qr, auto, checkout]
    SystemBuilder._inject_checkout_guard(candidates)
    with open("configs/openjarvis/examples/ordering-kiosk-mcp.toml", "rb") as f:
        config = tomllib.load(f)
    active = config["skills"]["active"].split(",")
    enabled = config["tools"]["enabled"].split(",")
    tools = SystemBuilder._model_visible_tools(
        [
            t
            for t in candidates
            if t.spec.name in enabled or t.spec.name.removeprefix("skill_") in active
        ],
        config["tools"]["model_hidden"],
    )
    engine = FakeEngine(
        [
            {
                "tool_calls": [
                    invoke(
                        "skill_trendcoffee-auto-table",
                        "CHECKOUT_NOW",
                        finish_turn=False,
                        customer_message="Mình chọn bàn trống nhé.",
                    )
                ]
            },
            {
                "tool_calls": [
                    invoke(
                        "skill_trendcoffee-checkout",
                        "CHECKOUT_NOW",
                        cart_revision=2,
                        order_type="at-table",
                        table="floor-upstairs",
                        table_status="available",
                        order_note="",
                        customer_message="Quét QR để thanh toán nhé.",
                    )
                ]
            },
        ]
    )
    with conversation_scope("auto-table-checkout"):
        _add(cart)
        result = OrchestratorAgent(
            engine, "fake", tools=tools, intent_normalization=True, max_turns=2
        ).run("Một cà phê tại bàn, chọn bàn trống giúp mình và thanh toán luôn.")
    assert result.tool_results and all(r.success for r in result.tool_results)
    assert result.metadata["execution_path"] == [
        "skill_trendcoffee-auto-table",
        "skill_trendcoffee-checkout",
    ]
    assert engine.call_count == 2
    writes = [request for request in requests if request[0] == "POST"]
    assert len(writes) == 2
    assert (writes[0][2]["type"], writes[0][2]["table"]) == (
        "at-table",
        "floor-upstairs",
    )
    assert writes[1][2] == {"orderSlug": "order-1", "paymentMethod": "bank-transfer"}
    assert events[-1].data["view"] == "payment_qr"
    assert events[-1].data["table_name"] == "17"


@pytest.fixture(autouse=True)
def _reset_evidence():
    evidence.reset()
    yield
    evidence.reset()
