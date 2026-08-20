"""Data Plane composition through the SystemBuilder."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from openjarvis.core.config import JarvisConfig
from openjarvis.core.events import EventBus
from openjarvis.core.registry import SourceAdapterRegistry
from openjarvis.system import SystemBuilder
from openjarvis.system.bundles import DataPlaneRuntime
from openjarvis.system.core import JarvisSystem


class _CloseProbe:
    def __init__(
        self, name: str, close_order: list[str], *, raises: bool = False
    ) -> None:
        self.name = name
        self.close_order = close_order
        self.raises = raises

    def close(self) -> None:
        self.close_order.append(self.name)
        if self.raises:
            raise RuntimeError(f"{self.name} close failure")


def test_builder_creates_one_shared_data_plane(tmp_path) -> None:
    """Category injection must hand every selected semantic tool one runtime."""
    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.tools.enabled = ["source_discover", "source_sync", "structured_query"]
    config.skills.enabled = False
    config.telemetry.enabled = False
    config.traces.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = SystemBuilder(config).engine_instance(engine).build()
    try:
        assert system.data_plane is not None
        assert (
            system.data_plane.discovery.capability_store
            is system.data_plane.capabilities
        )
        assert all(tool._runtime is system.data_plane for tool in system.tools)
    finally:
        system.close()


def test_builder_restores_required_adapters_after_registry_clear(tmp_path) -> None:
    """A registry-reset test must not leave a built runtime with no adapters."""
    SourceAdapterRegistry.clear()
    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.skills.enabled = False
    config.telemetry.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = SystemBuilder(config).engine_instance(engine).build()
    try:
        assert {"generic", "trendcoffee"} <= set(SourceAdapterRegistry.keys())
    finally:
        system.close()


def test_data_plane_runtime_closes_its_owners_once() -> None:
    """One runtime close must not duplicate any Data Plane-owned close call."""
    runtime = DataPlaneRuntime(
        capabilities=MagicMock(),
        snapshots=MagicMock(),
        discovery=MagicMock(),
        direct=MagicMock(),
    )
    runtime.close()
    runtime.close()

    runtime.direct.close.assert_called_once()
    runtime.snapshots.close.assert_called_once()
    runtime.capabilities.close.assert_called_once()
    runtime.discovery.close.assert_called_once()


def test_data_plane_runtime_close_releases_every_owner_after_close_errors() -> None:
    """A failing close must not prevent remaining HTTP and SQLite cleanup."""
    close_order: list[str] = []
    runtime = DataPlaneRuntime(
        capabilities=_CloseProbe("capabilities", close_order, raises=True),
        snapshots=_CloseProbe("snapshots", close_order),
        discovery=_CloseProbe("discovery", close_order, raises=True),
        direct=_CloseProbe("direct", close_order, raises=True),
    )

    with pytest.raises(RuntimeError, match="discovery close failure"):
        runtime.close()

    assert close_order == ["discovery", "direct", "snapshots", "capabilities"]
    runtime.close()
    assert close_order == ["discovery", "direct", "snapshots", "capabilities"]


def test_system_delegates_data_plane_shutdown_to_its_runtime() -> None:
    """System ownership must not reintroduce parallel close calls."""
    runtime = MagicMock()
    system = JarvisSystem(
        config=JarvisConfig(),
        bus=EventBus(),
        engine=MagicMock(),
        engine_key="test",
        model="test",
        data_plane=runtime,
    )

    system.close()

    runtime.close.assert_called_once()


def test_builder_failure_closes_the_unowned_data_plane(tmp_path, monkeypatch) -> None:
    """A failure after composition must not leak the runtime's HTTP/SQLite owners."""
    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.skills.enabled = False
    config.telemetry.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True
    builder = SystemBuilder(config).engine_instance(engine)
    captured = {}
    original = builder._build_data_plane

    def capture_runtime(*args):
        runtime = original(*args)
        captured["runtime"] = runtime
        return runtime

    def fail_after_composition(*args, **kwargs):
        raise RuntimeError("controlled tool resolution failure")

    monkeypatch.setattr(builder, "_build_data_plane", capture_runtime)
    monkeypatch.setattr(builder, "_resolve_tools", fail_after_composition)

    with pytest.raises(RuntimeError, match="controlled tool resolution failure"):
        builder.build()

    assert captured["runtime"]._closed is True


@pytest.mark.parametrize(
    ("failing_constructor", "expected_close_order"),
    [
        ("snapshots", ["capabilities"]),
        ("discovery", ["snapshots", "capabilities"]),
        ("direct", ["discovery", "snapshots", "capabilities"]),
    ],
)
def test_builder_releases_constructed_data_plane_owners_after_mid_build_failure(
    tmp_path, monkeypatch, failing_constructor, expected_close_order
) -> None:
    """Each failed constructor releases only the prior owners in reverse order."""
    from openjarvis.data_plane import (
        capability_store,
        discovery,
        execution,
        snapshot_store,
    )

    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    builder = SystemBuilder(config)
    close_order: list[str] = []
    capabilities = _CloseProbe("capabilities", close_order)
    snapshots = _CloseProbe("snapshots", close_order)
    discovery_owner = _CloseProbe("discovery", close_order)

    def construct_snapshots(_db_path):
        if failing_constructor == "snapshots":
            raise RuntimeError("snapshots construction failure")
        return snapshots

    def construct_discovery(*_args, **_kwargs):
        if failing_constructor == "discovery":
            raise RuntimeError("discovery construction failure")
        return discovery_owner

    def construct_direct(*_args, **_kwargs):
        if failing_constructor == "direct":
            raise RuntimeError("direct construction failure")
        return _CloseProbe("direct", close_order)

    monkeypatch.setattr(
        capability_store, "SQLiteCapabilityStore", lambda _db: capabilities
    )
    monkeypatch.setattr(snapshot_store, "StructuredSnapshotStore", construct_snapshots)
    monkeypatch.setattr(discovery, "DiscoveryEngine", construct_discovery)
    monkeypatch.setattr(execution, "DirectExecutionEngine", construct_direct)

    with pytest.raises(
        RuntimeError, match=f"{failing_constructor} construction failure"
    ):
        builder._build_data_plane(config, EventBus())

    assert close_order == expected_close_order
    assert builder._unowned_data_plane is None


def test_builder_preserves_constructor_error_when_partial_cleanup_fails(
    tmp_path, monkeypatch, caplog
) -> None:
    """A close error is logged but cannot replace the construction failure."""
    from openjarvis.data_plane import (
        capability_store,
        discovery,
        execution,
        snapshot_store,
    )

    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    builder = SystemBuilder(config)
    close_order: list[str] = []
    capabilities = _CloseProbe("capabilities", close_order, raises=True)
    snapshots = _CloseProbe("snapshots", close_order)
    discovery_owner = _CloseProbe("discovery", close_order)

    monkeypatch.setattr(
        capability_store, "SQLiteCapabilityStore", lambda _db: capabilities
    )
    monkeypatch.setattr(
        snapshot_store, "StructuredSnapshotStore", lambda _db: snapshots
    )
    monkeypatch.setattr(
        discovery, "DiscoveryEngine", lambda *_args, **_kwargs: discovery_owner
    )
    monkeypatch.setattr(
        execution,
        "DirectExecutionEngine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("direct construction failure")
        ),
    )
    caplog.set_level(logging.DEBUG, logger="openjarvis.system.builder")

    with pytest.raises(RuntimeError, match="direct construction failure"):
        builder._build_data_plane(config, EventBus())

    assert close_order == ["discovery", "snapshots", "capabilities"]
    assert "Error closing partially constructed Data Plane owner" in caplog.text
