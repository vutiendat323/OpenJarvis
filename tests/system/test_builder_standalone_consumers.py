"""Committed builder wiring must match the committed producer signatures."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjarvis.core.config import JarvisConfig
from openjarvis.core.events import EventBus
from openjarvis.system.builder import SystemBuilder


def test_builder_uses_standalone_trendcoffee_constructor(monkeypatch) -> None:
    from openjarvis.mcp.server import MCPServer
    from openjarvis.merchants import trendcoffee

    seen: list[tuple[object, str]] = []

    def standalone_constructor(runtime, *, source_id):
        seen.append((runtime, source_id))
        return object()

    monkeypatch.setattr(trendcoffee, "TrendCoffeeMerchant", standalone_constructor)
    monkeypatch.setattr(MCPServer, "get_tools", lambda self: [])
    config = JarvisConfig()
    config.merchants.backend = "trendcoffee"
    config.merchants.source_id = "trend-coffee"
    runtime = object()

    SystemBuilder(config)._resolve_tools(
        config,
        engine=None,
        model="",
        memory_backend=None,
        data_plane=runtime,
    )

    assert seen == [(runtime, "trend-coffee")]


def test_builder_uses_standalone_approval_gate_constructor(
    monkeypatch, tmp_path
) -> None:
    from openjarvis.core.registry import SourceAdapterRegistry
    from openjarvis.data_plane import (
        approval,
        capability_store,
        discovery,
        execution,
        snapshot_store,
    )
    from openjarvis.tools import proactive_tools

    owners: list[SimpleNamespace] = []

    def owner(**fields):
        value = SimpleNamespace(close=lambda: None, **fields)
        owners.append(value)
        return value

    capabilities = owner()
    snapshots = owner()
    discovery_owner = owner()
    direct = owner()
    approval_store = object()
    approval_gate = owner()
    seen: list[tuple[object, bool]] = []

    def standalone_constructor(store, *, owns_store=False):
        seen.append((store, owns_store))
        return approval_gate

    monkeypatch.setattr(SourceAdapterRegistry, "contains", lambda key: True)
    monkeypatch.setattr(
        capability_store, "SQLiteCapabilityStore", lambda path: capabilities
    )
    monkeypatch.setattr(
        snapshot_store, "StructuredSnapshotStore", lambda path: snapshots
    )
    monkeypatch.setattr(
        discovery, "DiscoveryEngine", lambda *args, **kwargs: discovery_owner
    )
    monkeypatch.setattr(
        execution, "DirectExecutionEngine", lambda *args, **kwargs: direct
    )
    monkeypatch.setattr(approval, "ExecutionApprovalGate", standalone_constructor)
    monkeypatch.setattr(proactive_tools, "get_store", lambda: approval_store)
    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "data-plane.db")
    builder = SystemBuilder(config)

    runtime = builder._build_data_plane(config, EventBus())
    runtime.close()

    assert seen == [(approval_store, False)]


def test_builder_wires_internal_manager_to_skill_manage_without_direct_skills(
    monkeypatch, tmp_path
) -> None:
    import openjarvis.skills.manager as manager_module
    import openjarvis.tools  # noqa: F401 -- populate the MCP tool registry
    import openjarvis.tools.skill_manage as skill_manage

    importlib.reload(skill_manage)

    class Manager:
        def __init__(self, bus, *, capability_policy=None) -> None:
            self.discovered_paths: list[list] = []
            self.executor = None

        def discover(self, *, paths) -> None:
            self.discovered_paths.append(paths)

        def set_tool_executor(self, executor) -> None:
            self.executor = executor

        def get_skill_tools(self, *, tool_executor):
            raise AssertionError("direct SkillTool wrappers must stay disabled")

        def get_few_shot_examples(self) -> list[str]:
            return []

    monkeypatch.setattr(manager_module, "SkillManager", Manager)
    config = JarvisConfig()
    config.skills.enabled = False
    config.skills.skills_dir = str(tmp_path / "learned-skills")
    config.tools.enabled = ["skill_manage"]
    config.telemetry.enabled = False
    config.traces.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = SystemBuilder(config).engine_instance(engine).speech(False).build()
    try:
        managed_tool = next(
            tool for tool in system.tools if tool.spec.name == "skill_manage"
        )

        assert isinstance(system.skill_manager, Manager)
        assert system.skill_manager.discovered_paths == [[tmp_path / "learned-skills"]]
        assert system.skill_manager.executor is system.tool_executor
        assert managed_tool._skill_manager is system.skill_manager
        assert managed_tool._memory_backend is system.memory_backend
        assert managed_tool._skills_dir == tmp_path / "learned-skills"
        assert [tool.spec.name for tool in system.tools] == ["skill_manage"]
    finally:
        system.close()
