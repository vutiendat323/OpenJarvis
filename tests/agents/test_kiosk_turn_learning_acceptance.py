"""Agent-level acceptance: a completed order becomes one outer skill call.

Wires the kiosk composition the way ``SystemBuilder`` does — one native
``ToolExecutor`` shared by the agent and the live ``SkillManager`` — and drives
it with a scripted engine. No network and no real shop are involved.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import pytest

from openjarvis.agents._stubs import AgentContext, AgentRunCompleted
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.agents.runtime import NativeAgentRuntime
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult
from openjarvis.learning.learning_orchestrator import LearningOrchestrator
from openjarvis.skills.manager import SkillManager
from openjarvis.tools._stubs import BaseTool, ToolSpec
from openjarvis.tools.skill_manage import SkillManageTool
from tests.agents.fake_engine import FakeEngine

ORDER_QUERY = "Order one latte at trend coffee"
LEARNED_NAME = (
    "learned-transaction-" + sha256(ORDER_QUERY.lower().encode()).hexdigest()[:12]
)
MENU_QUERY = "Show the food menu"


class _FakeHttp(BaseTool):
    """Stands in for the provider: POST mints an id, GET verifies that id."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_request",
            description="Call the shop API.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls.append(params)
        if str(params.get("method", "GET")).upper() == "POST":
            body = json.dumps({"result": {"order": {"slug": "ord-777"}}})
        else:
            body = json.dumps({"result": {"status": "confirmed"}})
        return ToolResult(tool_name="http_request", success=True, content=body)


class _FakeDisplay(BaseTool):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_bill",
            description="Show the bill.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls.append(params)
        return ToolResult(tool_name="display_bill", success=True, content="shown")


class _FakeMenuDisplay(BaseTool):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_menu",
            description="Show the live menu.",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        self.calls.append(params)
        return ToolResult(tool_name="display_menu", success=True, content="shown")


def _tool_call(call_id: str, name: str, arguments: dict) -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(arguments)}


def _ordering_script() -> list[dict]:
    """Turn one: the agent transacts through http_request and shows the bill."""
    return [
        {
            "tool_calls": [
                _tool_call(
                    "c1",
                    "http_request",
                    {
                        "method": "POST",
                        "url": "https://shop.example/orders/public",
                        "body": {"drink": "latte"},
                    },
                )
            ]
        },
        {
            "tool_calls": [
                _tool_call(
                    "c2",
                    "http_request",
                    {
                        "method": "GET",
                        "url": "https://shop.example/orders/ord-777",
                    },
                )
            ]
        },
        {"tool_calls": [_tool_call("c3", "display_bill", {"order_slug": "ord-777"})]},
        {"content": "Your latte is confirmed."},
    ]


def _replay_script() -> list[dict]:
    """Turn two: one model-visible call, and it is the learned skill."""
    return [
        {
            "tool_calls": [
                _tool_call(
                    "c4", "skill_manage", {"action": "run", "name": LEARNED_NAME}
                )
            ]
        },
        {"content": "Your latte is confirmed again."},
    ]


class _Memory:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def store(self, content: str, *, source: str, metadata: dict) -> str:
        self.records.append({"content": content, "metadata": metadata})
        return "memory-1"


class _Store:
    def list_traces(self, **kwargs):
        return []


class _Kiosk:
    """The kiosk composition under test, assembled once per scenario."""

    def __init__(self, tmp_path) -> None:
        self.bus = EventBus()
        self.http = _FakeHttp()
        self.display = _FakeDisplay()
        self.manager = SkillManager(bus=self.bus)
        self.memory = _Memory()
        self.skill_manage = SkillManageTool(
            skills_dir=tmp_path / "skills",
            skill_manager=self.manager,
            memory_backend=self.memory,
        )
        self.orchestrator = LearningOrchestrator(
            trace_store=_Store(),
            config_dir=tmp_path / "configs",
            bus=self.bus,
            skill_manage_tool=self.skill_manage,
            skill_manager=self.manager,
        )

    async def turn(self, query: str, script: list[dict]) -> list:
        agent = OrchestratorAgent(
            FakeEngine(script),
            "test-model",
            tools=[self.http, self.display, self.skill_manage],
            bus=self.bus,
            parallel_tools=False,
        )
        # The builder binds the agent's own native executor to the manager, so
        # a learned skill's inner steps run through the very same tools.
        self.manager.set_tool_executor(agent._executor)
        runtime = NativeAgentRuntime(agent, bus=self.bus)
        return [
            event
            async for event in runtime.bind(model="test-model").run_stream(
                query, AgentContext()
            )
        ]

    def close(self) -> None:
        self.orchestrator.close()


@pytest.fixture
def kiosk(tmp_path):
    composition = _Kiosk(tmp_path)
    try:
        yield composition
    finally:
        composition.close()


@pytest.mark.asyncio
async def test_a_learned_order_is_not_replayed_without_a_checkout_guard(kiosk):
    """A learned mutation is remembered but never re-sent on its own."""
    await kiosk.turn(ORDER_QUERY, _ordering_script())
    kiosk.orchestrator.close()

    assert kiosk.manager.skill_names() == [LEARNED_NAME]
    assert kiosk.memory.records[0]["metadata"]["requires_fresh_confirmation"] is True

    http_before = len(kiosk.http.calls)
    events = await kiosk.turn(ORDER_QUERY, _replay_script())

    completed = next(e for e in events if isinstance(e, AgentRunCompleted))
    [result] = completed.result.tool_results
    assert result.tool_name == "skill_manage"
    assert result.success is False
    assert "unguarded transaction" in result.content
    assert kiosk.http.calls[http_before:] == []


@pytest.mark.asyncio
async def test_a_failed_tool_call_is_never_learned(kiosk):
    class _FailingHttp(_FakeHttp):
        def execute(self, **params: Any) -> ToolResult:
            self.calls.append(params)
            return ToolResult(
                tool_name="http_request", success=False, content="upstream 500"
            )

    kiosk.http = _FailingHttp()
    await kiosk.turn(ORDER_QUERY, _ordering_script())
    kiosk.orchestrator.close()

    assert kiosk.manager.skill_names() == []
    assert kiosk.memory.records == []


@pytest.mark.asyncio
async def test_an_unverified_mutation_is_never_learned(kiosk):
    """A POST nobody read back is not a transaction anyone should replay."""
    script = [
        _ordering_script()[0],
        {"content": "Order sent."},
    ]
    await kiosk.turn(ORDER_QUERY, script)
    kiosk.orchestrator.close()

    assert kiosk.manager.skill_names() == []


@pytest.mark.asyncio
async def test_an_empty_answer_is_never_learned(kiosk):
    script = _ordering_script()[:-1] + [{"content": "   "}]
    await kiosk.turn(ORDER_QUERY, script)
    kiosk.orchestrator.close()

    assert kiosk.manager.skill_names() == []


@pytest.mark.asyncio
async def test_a_secret_bearing_trace_is_never_learned(kiosk):
    """A learned file must never carry an expanded Authorization header."""
    script = _ordering_script()
    script[0]["tool_calls"][0]["arguments"] = json.dumps(
        {
            "method": "POST",
            "url": "https://shop.example/orders/public",
            "headers": {"Authorization": "Bearer sk-live-secret"},
            "body": {"drink": "latte"},
        }
    )
    await kiosk.turn(ORDER_QUERY, script)
    kiosk.orchestrator.close()

    assert kiosk.manager.skill_names() == []
    assert kiosk.memory.records == []


@pytest.mark.asyncio
async def test_an_abandoned_turn_is_never_learned(kiosk):
    """Cancelling mid-stream must leave nothing behind to replay."""
    agent = OrchestratorAgent(
        FakeEngine(_ordering_script()),
        "test-model",
        tools=[kiosk.http, kiosk.display, kiosk.skill_manage],
        bus=kiosk.bus,
        parallel_tools=False,
    )
    kiosk.manager.set_tool_executor(agent._executor)
    runtime = NativeAgentRuntime(agent, bus=kiosk.bus)

    stream = runtime.bind(model="test-model").run_stream(ORDER_QUERY, AgentContext())
    await stream.__anext__()
    await stream.aclose()
    kiosk.orchestrator.close()

    assert kiosk.manager.skill_names() == []


@pytest.mark.asyncio
async def test_a_learned_menu_read_refetches_then_displays_fresh_data(tmp_path):
    """Warm replay skips discovery but never replays a frozen menu payload."""
    from tests.learning.test_skill_discovery import _completed_menu_trace

    class _FreshMenuHttp(_FakeHttp):
        def execute(self, **params: Any) -> ToolResult:
            self.calls.append(params)
            body = {
                "items": [{"id": "noodles-1", "name": "Fresh noodles v2", "price": 1}],
                "hasNext": False,
            }
            return ToolResult(
                tool_name="http_request",
                success=True,
                content=json.dumps(body),
                metadata={
                    "status_code": 200,
                    "content_type": "application/json",
                    "final_url": params.get("url"),
                    "truncated": False,
                },
            )

    bus = EventBus()
    http = _FreshMenuHttp()
    display = _FakeMenuDisplay()
    manager = SkillManager(bus=bus)
    memory = _Memory()
    skill_manage = SkillManageTool(
        skills_dir=tmp_path / "skills",
        skill_manager=manager,
        memory_backend=memory,
    )
    learner = LearningOrchestrator(
        trace_store=_Store(),
        config_dir=tmp_path / "configs",
        bus=bus,
        skill_manage_tool=skill_manage,
        skill_manager=manager,
    )
    try:
        # Reads are learned only from browser-grounded evidence.
        bus.publish(EventType.TRACE_COMPLETE, {"trace": _completed_menu_trace()})
        learner.close()
        [learned] = manager.skill_names()
        assert learned.startswith("learned-read-")
        assert memory.records[0]["metadata"]["requires_fresh_confirmation"] is False

        agent = OrchestratorAgent(
            FakeEngine(
                [
                    {
                        "content": "Here is the refreshed food menu.",
                        "tool_calls": [
                            _tool_call(
                                "m3",
                                "skill_manage",
                                {
                                    "action": "run",
                                    "name": learned,
                                    "context": {"q": "Fresh noodles"},
                                },
                            )
                        ],
                    }
                ]
            ),
            "test-model",
            tools=[http, display, skill_manage],
            bus=bus,
            parallel_tools=False,
        )
        manager.set_tool_executor(agent._executor)
        runtime = NativeAgentRuntime(agent, bus=bus)
        events = [
            event
            async for event in runtime.bind(model="test-model").run_stream(
                MENU_QUERY, AgentContext()
            )
        ]
    finally:
        learner.close()

    completed = next(event for event in events if isinstance(event, AgentRunCompleted))
    [result] = completed.result.tool_results
    assert result.tool_name == "skill_manage", result.content
    assert result.success is True, result.content
    [call] = http.calls
    assert "q=Fresh%20noodles" in call["url"]
    assert display.calls[-1]["items"][0]["name"] == "Fresh noodles v2"
