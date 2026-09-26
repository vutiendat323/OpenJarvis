"""An unconfirmed speaker may not change cart, order or payment state."""

from __future__ import annotations

import asyncio

import pytest

from openjarvis.core.conversation import (
    SPEAKER_UNCONFIRMED_MESSAGE,
    allowed_tool_steps,
    speaker_blocks_tool,
    uncertain_speaker_scope,
)
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec


class _Tool(BaseTool):
    def __init__(self, name: str) -> None:
        self.tool_id = name
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.tool_id, description="test tool")

    def execute(self, **params) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_name=self.tool_id, content="ok", success=True)


def _call(name: str) -> ToolCall:
    return ToolCall(id="call-1", name=name, arguments="{}")


def test_accepted_speaker_is_the_default():
    assert speaker_blocks_tool("display_cart") is False


def test_uncertain_speaker_blocks_tools_outside_the_allow_list():
    cart = _Tool("display_cart")
    menu = _Tool("display_menu")
    executor = ToolExecutor([cart, menu])

    with uncertain_speaker_scope(["display_menu"]):
        refused = executor.execute(_call("display_cart"))
        allowed = executor.execute(_call("display_menu"))

    assert refused.success is False
    assert refused.content == SPEAKER_UNCONFIRMED_MESSAGE
    assert refused.content.startswith("speaker_unconfirmed:")
    assert refused.metadata == {"speaker_unconfirmed": True, "dispatched": False}
    assert cart.calls == 0
    assert allowed.success is True
    assert menu.calls == 1


def test_an_allowed_skill_runs_its_fixed_steps_and_a_blocked_one_does_not():
    """Live 2026-09-26: an unconfirmed "open the menu" was refused because
    the allowed menu skill's own http_request step was blocked."""
    http = _Tool("http_request")
    executor = ToolExecutor([http])

    with uncertain_speaker_scope(["skill_trendcoffee-menu"]):
        with allowed_tool_steps("skill_trendcoffee-menu"):
            inside_allowed = executor.execute(_call("http_request"))
        with allowed_tool_steps("skill_trendcoffee-checkout"):
            inside_blocked = executor.execute(_call("http_request"))
        after = executor.execute(_call("http_request"))

    assert inside_allowed.success is True
    assert inside_blocked.content == SPEAKER_UNCONFIRMED_MESSAGE
    assert after.content == SPEAKER_UNCONFIRMED_MESSAGE
    assert http.calls == 1


def test_scope_ends_with_the_turn():
    cart = _Tool("display_cart")
    executor = ToolExecutor([cart])

    with uncertain_speaker_scope([]):
        pass

    assert executor.execute(_call("display_cart")).success is True


@pytest.mark.anyio
async def test_worker_threads_inherit_the_uncertain_scope():
    with uncertain_speaker_scope(["display_menu"]):
        blocked = await asyncio.to_thread(speaker_blocks_tool, "display_payment_qr")

    assert blocked is True
