"""[workflow].enabled decides whether skills schedule steps through the engine."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core.config import JarvisConfig
from openjarvis.system.builder import SystemBuilder


@pytest.mark.parametrize("enabled", [False, True])
def test_builder_hands_the_configured_workflow_engine_to_skills(enabled: bool):
    config = JarvisConfig()
    config.telemetry.enabled = False
    config.traces.enabled = False
    config.agent_manager.enabled = False
    config.tools.enabled = ["http_request"]
    config.skills.enabled = True
    config.skills.active = "trendcoffee-tables"
    config.workflow.enabled = enabled
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True
    builder = SystemBuilder(config).engine_instance(engine).speech(False)

    with patch.object(builder, "_resolve_memory", return_value=None):
        system = builder.build()

    try:
        tables = system.tool_executor.get_tool("skill_trendcoffee-tables")
        assert (system.workflow_engine is not None) is enabled
        assert tables._executor._workflow_engine is system.workflow_engine
    finally:
        system.close()
