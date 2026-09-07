"""LearningOrchestrator turn learning — TRACE_COMPLETE to a runnable skill."""

from __future__ import annotations

import json

import pytest

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import StepType, Trace, TraceStep
from openjarvis.learning.learning_orchestrator import LearningOrchestrator
from openjarvis.skills.manager import SkillManager
from openjarvis.tools.skill_manage import SkillManageTool


class _Memory:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def store(self, content: str, *, source: str, metadata: dict) -> str:
        self.records.append({"content": content, "metadata": metadata})
        return "memory-1"


class _Store:
    """Minimal trace store surface the orchestrator constructor touches."""

    def list_traces(self, **kwargs):
        return []


def _verified_transaction(query: str = "Place a latte order") -> Trace:
    """A completed order whose verification reuses the produced order id."""
    return Trace(
        query=query,
        result="Your order is confirmed.",
        steps=[
            TraceStep(
                step_type=StepType.TOOL_CALL,
                timestamp=1.0,
                input={
                    "tool": "http_request",
                    "arguments": {
                        "method": "POST",
                        "url": "https://shop.example/orders",
                        "body": {"drink": "latte"},
                    },
                },
                output={
                    "success": True,
                    "result": json.dumps({"order": {"id": "ord-123"}}),
                },
            ),
            TraceStep(
                step_type=StepType.TOOL_CALL,
                timestamp=2.0,
                input={
                    "tool": "http_request",
                    "arguments": {
                        "method": "GET",
                        "url": "https://shop.example/orders/ord-123",
                    },
                },
                output={"success": True, "result": '{"status": "confirmed"}'},
            ),
        ],
    )


def _failed_transaction() -> Trace:
    trace = _verified_transaction("Place a failing order")
    trace.steps[-1].output = {"success": False, "result": "boom"}
    return trace


def _displayed_read() -> Trace:
    return Trace(
        query="Show the food menu",
        result="Here is the food menu.",
        steps=[
            TraceStep(
                step_type=StepType.TOOL_CALL,
                timestamp=1.0,
                input={
                    "tool": "http_request",
                    "arguments": {
                        "method": "GET",
                        "url": "https://shop.example/menu",
                    },
                },
                output={
                    "success": True,
                    "result": '{"items": [{"name": "Fresh noodles"}]}',
                },
            ),
            TraceStep(
                step_type=StepType.TOOL_CALL,
                timestamp=2.0,
                input={
                    "tool": "display_menu",
                    "arguments": {"items": [{"name": "Fresh noodles"}]},
                },
                output={"success": True, "result": "shown"},
            ),
        ],
    )


@pytest.fixture
def wired(tmp_path):
    """An orchestrator wired the way SystemBuilder wires it."""
    bus = EventBus()
    manager = SkillManager(bus=bus)
    memory = _Memory()
    tool = SkillManageTool(
        skills_dir=tmp_path / "skills",
        skill_manager=manager,
        memory_backend=memory,
    )
    orchestrator = LearningOrchestrator(
        trace_store=_Store(),
        config_dir=tmp_path / "configs",
        bus=bus,
        skill_manage_tool=tool,
        skill_manager=manager,
    )
    try:
        yield bus, manager, memory, orchestrator
    finally:
        orchestrator.close()


def test_completed_trace_becomes_a_discoverable_skill_and_memory(wired):
    """One verified turn must be replayable without a process restart."""
    bus, manager, memory, orchestrator = wired

    bus.publish(EventType.TRACE_COMPLETE, {"trace": _verified_transaction()})
    orchestrator.close()

    assert manager.skill_names(), "no skill was learned"
    learned = manager.resolve(manager.skill_names()[0])
    assert [step.tool_name for step in learned.steps] == [
        "http_request",
        "http_request",
    ]
    assert len(memory.records) == 1
    assert memory.records[0]["metadata"]["requires_fresh_confirmation"] is True
    assert memory.records[0]["metadata"]["intent"] == "place a latte order"


def test_displayed_read_becomes_a_safe_live_read_skill(wired):
    bus, manager, memory, orchestrator = wired

    bus.publish(EventType.TRACE_COMPLETE, {"trace": _displayed_read()})
    orchestrator.close()

    learned = manager.resolve(manager.skill_names()[0])
    assert learned.name.startswith("learned-read-")
    assert [step.tool_name for step in learned.steps] == [
        "http_request",
        "display_menu",
    ]
    assert memory.records[0]["metadata"]["requires_fresh_confirmation"] is False


def test_publishing_does_not_learn_on_the_publishing_thread(wired):
    """Learning must never sit on the request path that published the event."""
    bus, manager, _memory, orchestrator = wired

    bus.publish(EventType.TRACE_COMPLETE, {"trace": _verified_transaction()})

    assert manager.skill_names() == []


def test_an_unverified_trace_is_never_learned(wired):
    bus, manager, memory, orchestrator = wired

    bus.publish(EventType.TRACE_COMPLETE, {"trace": _failed_transaction()})
    orchestrator.close()

    assert manager.skill_names() == []
    assert memory.records == []


def test_the_same_intent_is_learned_once(wired):
    """A repeated turn must not rewrite or re-remember the same skill."""
    bus, manager, memory, orchestrator = wired

    bus.publish(EventType.TRACE_COMPLETE, {"trace": _verified_transaction()})
    bus.publish(EventType.TRACE_COMPLETE, {"trace": _verified_transaction()})
    orchestrator.close()

    assert len(manager.skill_names()) == 1
    assert len(memory.records) == 1


def test_close_is_idempotent_and_bounded(wired):
    _bus, _manager, _memory, orchestrator = wired

    orchestrator.close()
    orchestrator.close()


def test_jarvis_system_drains_turn_learning_before_closing_stores():
    """A learned skill written after the store closed would be lost."""
    from openjarvis.core.config import JarvisConfig
    from openjarvis.system.core import JarvisSystem

    order: list[str] = []

    class _Recorder:
        def __init__(self, label: str) -> None:
            self._label = label

        def close(self) -> None:
            order.append(self._label)

    system = JarvisSystem(
        config=JarvisConfig(),
        bus=EventBus(),
        engine=_Recorder("engine"),
        engine_key="test",
        model="test-model",
        trace_store=_Recorder("trace_store"),
        memory_backend=_Recorder("memory_backend"),
        _learning_orchestrator=_Recorder("orchestrator"),
    )
    system.close()

    assert order.index("orchestrator") < order.index("trace_store")
    assert order.index("orchestrator") < order.index("memory_backend")


def test_builder_only_wires_turn_learning_when_opted_in(tmp_path):
    from openjarvis.core.config import JarvisConfig
    from openjarvis.system.builder import SystemBuilder

    config = JarvisConfig()
    bus = EventBus()
    manager = SkillManager(bus=bus)
    tool = SkillManageTool(skills_dir=tmp_path / "skills", skill_manager=manager)
    store = _Store()

    assert (
        SystemBuilder._setup_learning_orchestrator(
            config, bus=bus, trace_store=store, skill_manage_tool=tool
        )
        is None
    )

    config.learning.enabled = True
    config.learning.auto_update = True
    orchestrator = SystemBuilder._setup_learning_orchestrator(
        config,
        bus=bus,
        trace_store=store,
        skill_manage_tool=tool,
        skill_manager=manager,
    )
    try:
        assert orchestrator is not None
        assert orchestrator._trace_store is store
        assert orchestrator._skill_manager is manager
        bus.publish(EventType.TRACE_COMPLETE, {"trace": _verified_transaction()})
        orchestrator.close()
        assert manager.skill_names(), "builder-wired orchestrator learned nothing"
    finally:
        orchestrator.close()


def test_skill_optimizer_reuses_the_live_manager(tmp_path):
    """An empty private manager would optimize nothing that is actually served."""
    seen: dict = {}

    class _Optimizer:
        def __init__(self, **kwargs) -> None:
            pass

        def optimize(self, store, manager):
            seen["manager"] = manager
            return {}

    manager = SkillManager(bus=EventBus())
    orchestrator = LearningOrchestrator(
        trace_store=_Store(),
        config_dir=tmp_path / "configs",
        skill_manager=manager,
    )

    import openjarvis.learning.agents.skill_optimizer as optimizer_module

    original = optimizer_module.SkillOptimizer
    optimizer_module.SkillOptimizer = _Optimizer
    try:
        orchestrator._maybe_optimize_skills(auto_optimize=True)
    finally:
        optimizer_module.SkillOptimizer = original

    assert seen["manager"] is manager
