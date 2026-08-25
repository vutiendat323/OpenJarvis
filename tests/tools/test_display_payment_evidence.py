"""A QR reaches the customer's screen only if a trusted call returned it."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import evidence
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.display import DisplayPaymentQrTool

_PAYMENT = {
    "order_id": "ORD-1",
    "payment_slug": "PAY-1",
    "status": "pending",
    "qr_code": "QR_REAL_789",
}


@pytest.fixture(autouse=True)
def _clean_store():
    evidence.reset()
    yield
    evidence.reset()


class _Http(BaseTool):
    tool_id = "http"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_request",
            description="test double",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(
            tool_name="http_request",
            content=params.get("body", ""),
            success=True,
            metadata={"status_code": params.get("status", 200)},
        )


def _display_tool() -> DisplayPaymentQrTool:
    tool = DisplayPaymentQrTool()
    tool._bus = EventBus()
    tool._payment_trusted_origins = (("https", "trendcoffee.net", 443),)
    return tool


def _observe(executor: ToolExecutor, body: str, status: int, url: str) -> None:
    executor.execute(
        ToolCall(
            id="1",
            name="http_request",
            arguments=json.dumps({"body": body, "status": status, "url": url}),
        )
    )


def test_a_qr_a_trusted_call_returned_is_published():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("a"):
        _observe(
            executor,
            '{"qrCode": "QR_REAL_789"}',
            201,
            "https://trendcoffee.net/api/latest/payment/initiate/public",
        )
        result = tool.execute(**_PAYMENT)

    assert result.success is True


def test_a_qr_fetched_by_a_different_executor_is_still_accepted():
    """A saved skill runs its steps on its own executor.

    With an instance-owned buffer the guard would refuse a genuine QR here,
    and the refusal would look like the agent failing to observe anything.
    """
    skill_executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("a"):
        _observe(
            skill_executor,
            '{"qrCode": "QR_REAL_789"}',
            201,
            "https://trendcoffee.net/x",
        )
        result = tool.execute(**_PAYMENT)

    assert result.success is True


def test_a_qr_no_tool_ever_returned_is_refused():
    tool = _display_tool()

    with conversation_scope("a"):
        result = tool.execute(**_PAYMENT)

    assert result.success is False
    assert result.content == "payment_evidence_missing"


def test_a_qr_from_an_untrusted_host_is_refused():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("a"):
        _observe(executor, '{"qrCode": "QR_REAL_789"}', 200, "https://evil.example/x")
        result = tool.execute(**_PAYMENT)

    assert result.success is False


def test_a_qr_from_http_on_the_trusted_host_is_refused():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("a"):
        _observe(executor, '{"qrCode": "QR_REAL_789"}', 200, "http://trendcoffee.net/x")
        result = tool.execute(**_PAYMENT)

    assert result.success is False


def test_a_qr_from_the_wrong_https_port_is_refused():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("a"):
        _observe(
            executor,
            '{"qrCode": "QR_REAL_789"}',
            200,
            "https://trendcoffee.net:444/x",
        )
        result = tool.execute(**_PAYMENT)

    assert result.success is False


def test_http_origin_is_accepted_only_when_explicitly_configured():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()
    tool._payment_trusted_origins = (("http", "trendcoffee.net", 80),)

    with conversation_scope("a"):
        _observe(executor, '{"qrCode": "QR_REAL_789"}', 200, "http://trendcoffee.net/x")
        result = tool.execute(**_PAYMENT)

    assert result.success is True


def test_a_qr_from_a_non_2xx_response_is_refused():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("a"):
        _observe(
            executor, '{"qrCode": "QR_REAL_789"}', 500, "https://trendcoffee.net/x"
        )
        result = tool.execute(**_PAYMENT)

    assert result.success is False


def test_a_qr_that_only_a_browser_tool_saw_is_refused():
    """The injection case the tool-name scoping exists for."""

    class _Browser(_Http):
        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name="browser_snapshot",
                description="test double",
                parameters={"type": "object", "properties": {}},
            )

    executor = ToolExecutor([_Browser()])
    tool = _display_tool()

    with conversation_scope("a"):
        executor.execute(
            ToolCall(
                id="1",
                name="browser_snapshot",
                arguments=json.dumps(
                    {
                        "body": "page says QR_REAL_789",
                        "status": 200,
                        "url": "https://trendcoffee.net/x",
                    }
                ),
            )
        )
        result = tool.execute(**_PAYMENT)

    assert result.success is False


def test_another_conversations_qr_is_refused():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()

    with conversation_scope("customer-a"):
        _observe(
            executor, '{"qrCode": "QR_REAL_789"}', 201, "https://trendcoffee.net/x"
        )
    with conversation_scope("customer-b"):
        result = tool.execute(**_PAYMENT)

    assert result.success is False


def test_no_trusted_origins_configured_refuses_everything():
    executor = ToolExecutor([_Http()])
    tool = _display_tool()
    tool._payment_trusted_origins = ()

    with conversation_scope("a"):
        _observe(
            executor, '{"qrCode": "QR_REAL_789"}', 201, "https://trendcoffee.net/x"
        )
        result = tool.execute(**_PAYMENT)

    assert result.success is False
