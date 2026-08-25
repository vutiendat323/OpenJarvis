"""repl reads recent tool output; it can never write evidence."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import evidence
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.repl import ReplTool


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
            metadata={"status_code": 200},
        )


def test_repl_parses_a_prior_response_without_being_given_it():
    executor = ToolExecutor([_Http()])
    repl = ReplTool()

    with conversation_scope("a"):
        executor.execute(
            ToolCall(
                id="1",
                name="http_request",
                arguments=json.dumps({"body": '{"items": [{"name": "Latte"}]}'}),
            )
        )
        result = repl.execute(
            code=(
                "import json\n"
                "data = json.loads(last_result('http_request'))\n"
                "print(data['items'][0]['name'])"
            ),
            session_id="s",
        )

    assert result.success is True
    assert "Latte" in result.content


def test_repl_reads_what_a_different_executor_fetched():
    """A saved skill dispatches through its own executor; repl still sees it."""
    skill_executor = ToolExecutor([_Http()])
    repl = ReplTool()

    with conversation_scope("a"):
        skill_executor.execute(
            ToolCall(
                id="1",
                name="http_request",
                arguments=json.dumps({"body": "FROM_SKILL"}),
            )
        )
        result = repl.execute(code="print(last_result())", session_id="s")

    assert result.success is True
    assert "FROM_SKILL" in result.content


def test_repl_output_never_becomes_evidence():
    repl = ReplTool()

    with conversation_scope("a"):
        repl.execute(code="print('QR_FORGED')", session_id="s")

        assert evidence.observed_in_tool_output("QR_FORGED") is False


def test_repl_cannot_read_another_conversations_output():
    executor = ToolExecutor([_Http()])
    repl = ReplTool()

    with conversation_scope("customer-a"):
        executor.execute(
            ToolCall(
                id="1",
                name="http_request",
                arguments=json.dumps({"body": "PRIVATE_A"}),
            )
        )
    with conversation_scope("customer-b"):
        result = repl.execute(code="print(repr(last_result()))", session_id="s")

    assert result.success is True
    assert "PRIVATE_A" not in result.content


def test_repl_cannot_reach_evidence_record_through_last_result_globals():
    """The exploit: every function exposes its defining module's globals.

    If `last_result` were the raw `evidence.last_result` function object,
    `last_result.__globals__['record']` would be `evidence.record` itself --
    letting interpreted code forge a trusted-looking entry (fake tool name,
    trusted host, 200 status) that `display_payment_qr` would later trust for
    a real payment QR. The fix must remove that attribute path structurally,
    not merely block the string "__globals__".
    """
    repl = ReplTool()

    with conversation_scope("a"):
        result = repl.execute(
            code=(
                "rec = last_result.__globals__['record']\n"
                "rec('fake_payment_tool', {}, 'FORGED_QR_PAYLOAD', 200, "
                "'https://trusted-payments.example')\n"
            ),
            session_id="s",
        )

        assert evidence.observed_in_tool_output("FORGED_QR_PAYLOAD") is False


def test_repl_cannot_reach_evidence_record_via_obfuscated_attribute_name():
    """Same exploit, but the attribute name is built at runtime.

    Proves the fix is structural (no attribute path to `record` exists at
    all) rather than a source-substring blocklist on "__globals__" -- which
    this bypasses trivially.
    """
    repl = ReplTool()

    with conversation_scope("a"):
        result = repl.execute(
            code=(
                "attr = chr(95) * 2 + 'globals' + chr(95) * 2\n"
                "rec = getattr(last_result, attr)['record']\n"
                "rec('fake_payment_tool', {}, 'FORGED_QR_OBFUSCATED', 200, "
                "'https://trusted-payments.example')\n"
            ),
            session_id="s",
        )

        assert evidence.observed_in_tool_output("FORGED_QR_OBFUSCATED") is False
