"""Ordering tools reach a merchant through the builder's existing seam."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

from openjarvis.core.config import JarvisConfig
from openjarvis.merchants.fake import FakeMerchant
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.ordering import CartViewTool, MenuSearchTool


def test_inject_tool_deps_gives_ordering_tools_a_merchant():
    merchant = FakeMerchant()
    tools = [MenuSearchTool(), CartViewTool()]

    for tool in tools:
        SystemBuilder._inject_ordering_merchant(tool, merchant)

    assert all(tool._merchant is merchant for tool in tools)


def test_non_ordering_tools_are_untouched():
    from openjarvis.tools.calculator import CalculatorTool

    tool = CalculatorTool()
    SystemBuilder._inject_ordering_merchant(tool, FakeMerchant())

    assert not hasattr(tool, "_merchant") or tool._merchant is None


def test_default_merchant_config_has_no_production_merchant():
    config = JarvisConfig()

    assert config.merchants.backend == "none"
    assert config.merchants.source_id == "trend-coffee"


def test_builder_injects_one_shared_trendcoffee_merchant_with_data_plane(tmp_path):
    import openjarvis.tools.ordering as ordering

    # The shared test fixture clears import-time registrations before each
    # test; production's native catalog already owns this registration path.
    importlib.reload(ordering)
    config = JarvisConfig()
    config.data_plane.enabled = True
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.merchants.backend = "trendcoffee"
    config.merchants.source_id = "trend-coffee"
    config.tools.enabled = ["branch_list", "cart_view"]
    config.skills.enabled = False
    config.telemetry.enabled = False
    config.traces.enabled = False
    engine = MagicMock(spec=["health", "list_models", "close"])
    engine.health.return_value = True

    system = SystemBuilder(config).engine_instance(engine).build()
    try:
        from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant

        merchants = [tool._merchant for tool in system.tools]
        assert system.data_plane is not None
        assert len({id(merchant) for merchant in merchants}) == 1
        assert isinstance(merchants[0], TrendCoffeeMerchant)
        assert merchants[0]._runtime is system.data_plane
        assert merchants[0]._snapshot_max_age_seconds == 60
    finally:
        system.close()
