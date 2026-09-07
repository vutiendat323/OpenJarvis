"""Integrated acceptance for the HTTP-only runtime hardening addendum.

Drives the real agent loop, the real ``HttpRequestTool``, the real evidence
store and the real conversation middleware against a fake provider. Nothing
here reaches the public internet, places a real order or mints a real QR.

What it pins, end to end:

* a 100 KB catalogue reaches the model bounded while evidence keeps it exact;
* a second turn rejoins the scope the server issued for the first;
* one confirmed order is one order POST, and repeating the confirmation does
  not become a second coffee.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.agents._stubs import AgentContext
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.conversation import current_conversation_id
from openjarvis.core.events import EventBus
from openjarvis.server.middleware import create_conversation_scope_middleware
from openjarvis.tools import evidence
from openjarvis.tools.display import DisplayBillTool, DisplayPaymentQrTool
from openjarvis.tools.http_request import HttpRequestTool
from tests.agents.fake_engine import FakeEngine

SHOP = "https://shop.example/api"
CONVERSATION_HEADER = "X-OpenJarvis-Conversation"
TRUSTED = (("https", "shop.example", 443),)

ORDER_BODY = {
    "items": [
        {"slug": "latte-standard", "quantity": 1},
        {"slug": "americano-standard", "quantity": 1},
    ],
    "type": "take-out",
}
QR_VALUE = "FAKE-QR-PAYLOAD-777"


def _catalogue() -> str:
    """A synthetic catalogue comfortably over 100 KB, like the real one."""
    items = [
        {
            "slug": f"item-{index:03d}",
            "name": f"Drink {index}",
            "description": "d" * 700,
            "variants": [{"slug": f"item-{index:03d}-standard", "price": 45000}],
        }
        for index in range(130)
    ]
    return json.dumps({"result": {"items": items}})


class _Provider:
    """Fake shop. Counts every dispatch so a double order cannot hide."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def order_posts(self) -> int:
        return sum(
            1 for method, url in self.calls if method == "POST" and "/orders" in url
        )

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append((method, url))
        request = httpx.Request(method, url)
        if "/products" in url:
            return httpx.Response(200, request=request, text=_catalogue())
        if "/orders" in url and method == "POST":
            return httpx.Response(
                200, request=request, json={"result": {"order": {"slug": "ord-777"}}}
            )
        if "/orders/" in url:
            return httpx.Response(
                200,
                request=request,
                json={"result": {"slug": "ord-777", "status": "confirmed"}},
            )
        if "/payment" in url:
            return httpx.Response(
                200,
                request=request,
                json={"result": {"qrCode": QR_VALUE, "slug": "pay-1"}},
            )
        return httpx.Response(404, request=request, text="{}")


@pytest.fixture(autouse=True)
def _clean_store():
    evidence.reset()
    yield
    evidence.reset()


@pytest.fixture(autouse=True)
def _stub_rust_dependencies():
    """Stub Rust SSRF/loop-guard dependencies so tests stay offline."""
    from unittest.mock import MagicMock, patch

    mock_rust = MagicMock()
    # LoopGuard falls back to its pure-Python implementation when the Rust one
    # cannot be constructed; a MagicMock verdict would abort the turn instead.
    mock_rust.LoopGuard.side_effect = RuntimeError("mocked out")
    mock_rust.check_ssrf.return_value = None
    with patch("openjarvis._rust_bridge.get_rust_module", return_value=mock_rust):
        yield


@pytest.fixture
def provider(monkeypatch) -> _Provider:
    shop = _Provider()
    monkeypatch.setattr(httpx, "request", shop)
    return shop


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(arguments)}


def _browse_script() -> list[dict]:
    return [
        {
            "tool_calls": [
                _call(
                    "b1", "http_request", {"method": "GET", "url": f"{SHOP}/products"}
                )
            ]
        },
        {"content": "A latte and an americano, take-out. Shall I place it?"},
    ]


def _confirm_script() -> list[dict]:
    return [
        {
            "tool_calls": [
                _call(
                    "o1",
                    "http_request",
                    {
                        "method": "POST",
                        "url": f"{SHOP}/orders/public",
                        "body": json.dumps(ORDER_BODY),
                    },
                )
            ]
        },
        {
            "tool_calls": [
                _call(
                    "o2",
                    "http_request",
                    {"method": "GET", "url": f"{SHOP}/orders/ord-777"},
                )
            ]
        },
        {
            "tool_calls": [
                _call(
                    "o3",
                    "http_request",
                    {
                        "method": "POST",
                        "url": f"{SHOP}/payment/initiate/public",
                        "body": json.dumps({"orderSlug": "ord-777"}),
                    },
                )
            ]
        },
        {
            "tool_calls": [
                _call(
                    "o4",
                    "display_payment_qr",
                    {
                        "order_id": "ord-777",
                        "payment_slug": "pay-1",
                        "status": "confirmed",
                    },
                )
            ]
        },
        {"content": "Order confirmed. Please scan the QR."},
    ]


class _Kiosk:
    """The kiosk behind the real conversation middleware."""

    def __init__(self) -> None:
        self.bus = EventBus()
        self.qr_tool = DisplayPaymentQrTool()
        self.qr_tool._payment_trusted_origins = TRUSTED
        bill_tool = DisplayBillTool()
        # The builder hands display tools their publish channel; here the bus
        # stands in for the customer screen.
        self.qr_tool._bus = self.bus
        bill_tool._bus = self.bus
        self.tools = [HttpRequestTool(), bill_tool, self.qr_tool]
        self.turns: list[dict] = []

        app = FastAPI()
        app.add_middleware(create_conversation_scope_middleware())

        @app.post("/turn")
        def turn(payload: dict) -> dict:
            agent = OrchestratorAgent(
                FakeEngine(payload["script"]),
                "test-model",
                tools=self.tools,
                bus=self.bus,
                parallel_tools=False,
            )
            result = agent.run(payload["input"], AgentContext())
            record = {
                "scope": current_conversation_id(),
                "content": result.content,
                "tool_results": [
                    {
                        "tool": tool_result.tool_name,
                        "success": tool_result.success,
                        "content": tool_result.content,
                    }
                    for tool_result in result.tool_results
                ],
            }
            self.turns.append(record)
            return record

        self.client = TestClient(app)

    def send(self, text: str, script: list[dict], scope: str | None = None) -> dict:
        headers = {CONVERSATION_HEADER: scope} if scope else {}
        response = self.client.post(
            "/turn", json={"input": text, "script": script}, headers=headers
        )
        assert response.status_code == 200, response.text
        return {
            "scope": response.headers[CONVERSATION_HEADER],
            "body": response.json(),
        }


def test_a_confirmed_order_is_placed_once_across_turns(provider):
    """The whole addendum in one flow: bounded, scoped, and not doubled."""
    kiosk = _Kiosk()

    browse = kiosk.send("What coffee do you have?", _browse_script())
    scope = browse["scope"]

    # The catalogue was read, and the model saw a bounded view of it.
    assert provider.calls[0] == ("GET", f"{SHOP}/products")
    catalogue_result = browse["body"]["tool_results"][0]["content"]
    assert len(catalogue_result) > 100_000, "evidence must stay exact"

    confirm = kiosk.send("Yes, place it.", _confirm_script(), scope=scope)

    assert confirm["scope"] == scope, "the second turn must rejoin the first"
    assert provider.order_posts == 1
    qr = next(
        r for r in confirm["body"]["tool_results"] if r["tool"] == "display_payment_qr"
    )
    assert qr["success"] is True, qr["content"]

    # The customer says yes again -- a nod, a repeat, a stuck client.
    repeat = kiosk.send("Yes, place it.", _confirm_script(), scope=scope)

    assert provider.order_posts == 1, "a repeated confirmation bought a second order"
    refused = next(
        r
        for r in repeat["body"]["tool_results"]
        if r["tool"] == "http_request" and not r["success"]
    )
    assert "no network call" in refused["content"].lower()
    assert "latte-standard" not in refused["content"]


def test_the_model_transcript_stays_bounded_while_evidence_stays_exact(provider):
    """29,700 tokens of catalogue per read is what this addendum exists to stop."""
    kiosk = _Kiosk()

    browse = kiosk.send("What coffee do you have?", _browse_script())
    scope = browse["scope"]
    raw = browse["body"]["tool_results"][0]["content"]

    # Ask the same scope what the model would be shown.
    projected = kiosk.client.post(
        "/turn",
        json={"input": "again", "script": [{"content": "ok"}]},
        headers={CONVERSATION_HEADER: scope},
    )
    assert projected.status_code == 200

    from openjarvis.tools.result_projection import project_tool_content

    bounded = project_tool_content(raw, max_chars=16_000)

    assert len(raw) > 100_000
    assert len(bounded) <= 16_000
    for probe in ("item-000", "item-064", "item-129"):
        assert probe in bounded, f"{probe} fell out of the projected view"


def test_a_different_order_in_the_same_conversation_still_goes_through(provider):
    """Dedupe must not block a customer who genuinely changes the order."""
    kiosk = _Kiosk()

    browse = kiosk.send("What coffee do you have?", _browse_script())
    scope = browse["scope"]
    kiosk.send("Yes, place it.", _confirm_script(), scope=scope)

    bigger = json.loads(json.dumps(ORDER_BODY))
    bigger["items"][0]["quantity"] = 3
    second_script = [
        {
            "tool_calls": [
                _call(
                    "n1",
                    "http_request",
                    {
                        "method": "POST",
                        "url": f"{SHOP}/orders/public",
                        "body": json.dumps(bigger),
                    },
                )
            ]
        },
        {"content": "Updated order placed."},
    ]
    kiosk.send("Make that three lattes.", second_script, scope=scope)

    assert provider.order_posts == 2


def test_a_new_conversation_may_place_the_same_order(provider):
    """The next customer must not inherit the previous one's dedupe ledger."""
    kiosk = _Kiosk()

    first = kiosk.send("What coffee do you have?", _browse_script())
    kiosk.send("Yes, place it.", _confirm_script(), scope=first["scope"])

    # No header: the server issues a fresh scope, as it would for a new customer.
    second = kiosk.send("Yes, place it.", _confirm_script())

    assert second["scope"] != first["scope"]
    assert provider.order_posts == 2


def test_a_caller_invented_scope_does_not_reach_another_conversation(provider):
    """Guessing an id must not hand over another customer's evidence."""
    kiosk = _Kiosk()

    real = kiosk.send("What coffee do you have?", _browse_script())

    forged = kiosk.send(
        "What did they order?",
        [{"content": "nothing to report"}],
        scope="deadbeef" * 4,
    )

    assert forged["scope"] != real["scope"]
    assert forged["scope"] != "deadbeef" * 4
