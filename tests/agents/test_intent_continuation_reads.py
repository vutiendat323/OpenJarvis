"""A read after a continuing target keeps the turn open for its next step.

Live 2026-10-10 (10:52 deepseek-flash, 11:56 gpt-6-luna, 13:00 deepseek-flash):
after "add to cart" succeeded with the turn left open, the agent read the
table list, and that read reset ``continuing``; the table selection that
followed was refused as "requested outcome already completed". At 13:00 the
customer was then told the table had been changed although it had not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjarvis.agents.intent import IntentState
from openjarvis.core.events import EventBus
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.loader import load_skill
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.tools._stubs import ToolExecutor
from tests.agents.test_intent_normalization import ExistingTool


def _call(name: str) -> ToolCall:
    return ToolCall(id=name, name=name, arguments=json.dumps({"_intent": "ADD"}))


def _ok(name: str, **metadata) -> ToolResult:
    return ToolResult(tool_name=name, content="ok", success=True, metadata=metadata)


def _state() -> IntentState:
    return IntentState(
        [
            ExistingTool("add", metadata={"intent_contract": {"name": "ADD"}}),
            ExistingTool("tables", metadata={"read_only": True}),
            ExistingTool("set_table"),
        ]
    )


def _run(state: IntentState, name: str, result: ToolResult) -> None:
    [call] = state.prepare([_call(name)])
    state.observe(call, result)


def test_read_only_result_keeps_a_continuing_target_open():
    state = _state()
    _run(state, "add", _ok("add", continue_agent=True))
    assert state.reached and state.continuing

    _run(state, "tables", _ok("tables"))

    assert state.continuing
    assert [c.name for c in state.prepare([_call("set_table")])] == ["set_table"]


def test_a_step_that_does_not_continue_still_ends_the_turn():
    state = _state()
    _run(state, "add", _ok("add", continue_agent=True))
    _run(state, "set_table", _ok("set_table"))

    assert state.continuing is False
    with pytest.raises(ValueError, match="requested outcome already completed"):
        state.prepare([_call("tables")])


def test_a_read_cannot_reopen_a_turn_whose_target_ended_it():
    state = _state()
    _run(state, "add", _ok("add"))
    assert state.continuing is False

    with pytest.raises(ValueError, match="requested outcome already completed"):
        state.prepare([_call("tables")])


def test_the_table_list_skill_is_declared_read_only():
    skill = Path("skills/trendcoffee-tables.toml")
    tool = SkillTool(load_skill(skill), SkillExecutor(ToolExecutor([], EventBus())))

    assert tool.spec.metadata.get("read_only") is True
