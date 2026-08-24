"""Presentation-session composition through the native SystemBuilder."""

from __future__ import annotations

import importlib
import json
from unittest.mock import MagicMock, patch

from openjarvis.core.config import JarvisConfig
from openjarvis.system.builder import SystemBuilder


class _FakePlaywrightClient:
    _server_name = "playwright"

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_builder_injects_its_presentation_manager_into_display_tools() -> None:
    """Would fail if display tools and routes could use different sessions."""
    import openjarvis.tools.display as display

    importlib.reload(display)
    config = JarvisConfig()
    config.telemetry.enabled = False
    config.traces.enabled = False
    config.skills.enabled = False
    config.agent_manager.enabled = False
    config.tools.enabled = ["display_menu"]
    config.tools.mcp.enabled = True
    config.tools.mcp.servers = json.dumps(
        [{"name": "playwright", "url": "http://localhost:8080/mcp"}]
    )
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True
    client = _FakePlaywrightClient()
    builder = SystemBuilder(config).engine_instance(engine).speech(False)

    def _discover(_server_config):
        builder._mcp_clients.append(client)
        return []

    with (
        patch.object(builder, "_discover_external_mcp", side_effect=_discover),
        patch.object(builder, "_resolve_memory", return_value=None),
    ):
        system = builder.build()

    try:
        display_menu = next(
            tool for tool in system.tools if tool.spec.name == "display_menu"
        )

        assert display_menu._presentation is system.presentation_session_manager
        assert system.presentation_session_manager._client is client
    finally:
        system.close()

    assert client.closed is True


def test_builder_injects_presentation_manager_into_hidden_display_bill() -> None:
    """Order verification must publish through the session manager even when
    display_bill itself is not Agent-visible."""
    import openjarvis.tools.display as display
    import openjarvis.tools.ordering as ordering

    importlib.reload(display)
    importlib.reload(ordering)
    config = JarvisConfig()
    config.merchants.backend = "fake"
    config.telemetry.enabled = False
    config.traces.enabled = False
    config.skills.enabled = False
    config.agent_manager.enabled = False
    config.tools.enabled = ["order_verify"]
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = (
        SystemBuilder(config).engine_instance(engine).speech(False).build()
    )
    try:
        assert [tool.spec.name for tool in system.tools] == ["order_verify"]
        order_verify = system.tools[0]
        display_bill = order_verify._display_bill

        assert display_bill.spec.name == "display_bill"
        assert display_bill not in system.tools
        assert display_bill._presentation is system.presentation_session_manager
    finally:
        system.close()
