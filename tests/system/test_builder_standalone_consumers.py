"""Committed builder wiring must match the committed producer signatures."""

from __future__ import annotations

from types import SimpleNamespace

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
