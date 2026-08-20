"""Data Plane composition through the SystemBuilder."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjarvis.core.config import JarvisConfig
from openjarvis.core.events import EventBus
from openjarvis.core.registry import SourceAdapterRegistry
from openjarvis.system import SystemBuilder
from openjarvis.system.bundles import DataPlaneRuntime
from openjarvis.system.core import JarvisSystem


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
