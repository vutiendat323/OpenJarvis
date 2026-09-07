"""The built kiosk links a successful menu read to the display tool."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from openjarvis.core.config import JarvisConfig
from openjarvis.system.builder import SystemBuilder


def test_builder_injects_display_menu_into_menu_search(tmp_path):
    import openjarvis.tools.display as display
    import openjarvis.tools.ordering as ordering

    importlib.reload(display)
    importlib.reload(ordering)
    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.merchants.backend = "trendcoffee"
    config.tools.enabled = ["menu_search", "display_menu"]
    config.skills.enabled = False
    config.telemetry.enabled = False
    config.traces.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = SystemBuilder(config).engine_instance(engine).build()
    try:
        menu_search = next(
            tool for tool in system.tools if tool.spec.name == "menu_search"
        )
        display_menu = next(
            tool for tool in system.tools if tool.spec.name == "display_menu"
        )

        assert menu_search._display_menu is display_menu
    finally:
        system.close()
