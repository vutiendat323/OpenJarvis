"""The order flow forces a reasoning turn between every action.

If ordering ever completes in one or two inference turns, a tool has been
built that both changes something and reports the result -- the giant
commerce_checkout() this design exists to prevent.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.core.config import load_config
from openjarvis.merchants.fake import FakeMerchant
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.ordering import (
    BranchListTool,
    CartAddTool,
    CartViewTool,
    MenuSearchTool,
    OrderPlaceTool,
    OrderVerifyTool,
)

pytestmark = pytest.mark.integration

PRESET_PATH = Path("configs/openjarvis/examples/ordering-kiosk.toml")
PROMPT_PATH = Path("configs/openjarvis/prompts/ordering-kiosk.md")


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_the_real_request_takes_six_observed_steps():
    """"Chọn cà phê đen ít đường, đặt mang đi" -- the whole flow.

    Each step is a place the Agent must look at a result before it can choose
    the next call. There is no shortcut through them, because no tool returns
    both an effect and its consequence.
    """
    merchant = FakeMerchant()
    branches, search, add, view, place, verify = _wired(
        merchant,
        BranchListTool,
        MenuSearchTool,
        CartAddTool,
        CartViewTool,
        OrderPlaceTool,
        OrderVerifyTool,
    )

    # 1. which shop -- nothing is orderable until this is known
    branch = json.loads(branches.execute().content)["branches"][0]["slug"]

    # 2. what exists there, and at what price
    products = json.loads(
        search.execute(query="cà phê đen", branch=branch).content
    )["products"]
    variant = products[0]["variants"][0]
    assert variant["slug"] == "ca-phe-den-std"
    assert variant["price"] == 35_000

    # 3. change something -- and learn nothing about the result
    added = json.loads(
        add.execute(variant=variant["slug"], quantity=1, note="ít đường").content
    )
    assert set(added) == {"added", "line_id"}

    # 4. so the cart has to be read
    cart = json.loads(view.execute().content)
    assert cart["total"] == 35_000
    assert cart["lines"][0]["note"] == "ít đường"

    # 5. change something again -- again learning nothing
    placed = json.loads(place.execute(type="take-out", branch=branch).content)
    assert set(placed) == {"placed", "order_id"}

    # 6. so the order has to be read back from the merchant
    order = json.loads(verify.execute(order_id=placed["order_id"]).content)
    assert order["status"] == "placed"
    assert order["order_type"] == "take-out"
    assert order["total"] == cart["total"]

    # ...and the one thing that is still not confirmed says so.
    assert order["notes_are_unverified"] is True


def test_the_merchant_is_the_authority_when_the_agent_is_wrong():
    """A belief that disagrees with the merchant loses."""
    merchant = FakeMerchant()
    add, view = _wired(merchant, CartAddTool, CartViewTool)

    merchant.set_price("ca-phe-den-std", 80_000)
    add.execute(variant="ca-phe-den-std", quantity=1)

    assert json.loads(view.execute().content)["total"] == 80_000


def test_ordering_and_display_tools_never_overlap():
    """A display tool must not be able to change an order, and an ordering
    tool must not be able to draw.

    Enumerated from the modules, not from ToolRegistry: conftest clears the
    registry autouse before every test, so a registry walk would iterate
    nothing and pass without checking anything.
    """
    import inspect

    from openjarvis.tools import display, ordering
    from openjarvis.tools._stubs import BaseTool

    checked = 0
    for module in (ordering, display):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(member, BaseTool)
                or member.__module__ != module.__name__
                or inspect.isabstract(member)
            ):
                continue
            spec = member().spec
            kinds = {
                key
                for key in ("mutates", "observes", "displays")
                if spec.metadata.get(key)
            }
            assert len(kinds) == 1, f"{spec.name} declares {kinds}"
            checked += 1

    assert checked == 14, f"expected 9 ordering + 5 display tools, saw {checked}"


def test_the_real_build_wires_one_shared_merchant_into_every_ordering_tool(tmp_path):
    """Phase 1 rests on one merchant instance per built system.

    The other tests in this file, and ``tests/system/test_ordering_wiring.py``,
    exercise the injection helpers directly against a hand-built tool list.
    Neither goes through ``SystemBuilder._resolve_tools`` -- the
    ``config.merchants`` check, merchant construction, and the
    injection loop -- so a regression there (e.g. a merchant built per tool)
    could pass every other test in this file and still break Phase 1. Build
    a real system from the preset to close that gap.
    """
    config = load_config(PRESET_PATH)
    config.data_plane.db_path = str(tmp_path / "structured.db")
    # tests/conftest.py clears EngineRegistry before every test, so engine
    # discovery would find nothing regardless of what is running on this
    # box. Inject through the builder's public seam instead -- no model is
    # ever called here, we only care how tools get wired.
    engine = MagicMock()
    engine.health.return_value = True
    engine.list_models.return_value = [config.intelligence.default_model]

    # tests/conftest.py also clears ToolRegistry before every test. The
    # ordering/display modules only register their tools with the decorator
    # the first time they are imported in this process, so by the time this
    # test runs the registry may already be empty again -- and
    # SystemBuilder._resolve_tools's internal MCPServer falls back to
    # ToolRegistry for exactly these tools. Re-register from the modules
    # (same idiom as
    # test_ordering_and_display_tools_never_overlap, above) rather than
    # trusting whatever state an earlier test left behind.
    import inspect

    from openjarvis.core.registry import ToolRegistry
    from openjarvis.tools import display, ordering
    from openjarvis.tools._stubs import BaseTool

    for module in (ordering, display):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(member, BaseTool)
                or member.__module__ != module.__name__
                or inspect.isabstract(member)
            ):
                continue
            name = member().spec.name
            if not ToolRegistry.contains(name):
                ToolRegistry.register_value(name, member)

    system = SystemBuilder(config).engine_instance(engine, key="ollama").build()
    try:
        ordering_tools = [t for t in system.tools if t.spec.category == "ordering"]
        display_tools = [t for t in system.tools if t.spec.category == "display"]

        # Seven, not the module's nine: the preset enables `cart_set` and
        # leaves `cart_add`/`cart_remove` off the Agent's surface.
        assert len(ordering_tools) == 7, sorted(
            t.spec.name for t in ordering_tools
        )
        assert len({id(t._merchant) for t in ordering_tools}) == 1
        assert ordering_tools[0]._merchant._runtime is system.data_plane
        assert len(display_tools) == 3
        assert all(t._bus is not None for t in display_tools)
    finally:
        system.close()


def test_the_preset_disables_parallel_tool_dispatch():
    """Parallel dispatch runs a turn's tool calls concurrently, which lets a
    model reach ``cart_add`` + ``cart_view`` results in one turn -- the
    single-call-that-both-mutates-and-observes collapse the doctrine exists
    to prevent, reached around it instead of through it. It is also a data
    race against the process-local draft cart. The preset must
    keep ``parallel_tools`` off; this fails if that ever quietly flips back
    to the ``AgentConfig`` default of ``True``.
    """
    config = load_config(PRESET_PATH)
    assert config.agent.parallel_tools is False


def test_merchants_backend_none_leaves_ordering_tools_without_a_merchant(tmp_path):
    """``[merchants] backend = "none"`` must survive config load and reach
    the built system -- both the ``top_sections`` round-trip and the
    builder's configured-merchant-or-nothing branch are on the hook here.
    """
    import inspect

    from openjarvis.core.registry import ToolRegistry
    from openjarvis.tools import display, ordering
    from openjarvis.tools._stubs import BaseTool

    for module in (ordering, display):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(member, BaseTool)
                or member.__module__ != module.__name__
                or inspect.isabstract(member)
            ):
                continue
            name = member().spec.name
            if not ToolRegistry.contains(name):
                ToolRegistry.register_value(name, member)

    preset_text = PRESET_PATH.read_text()
    none_preset = preset_text.replace('backend = "trendcoffee"', 'backend = "none"')
    assert 'backend = "none"' in none_preset  # the replace actually matched

    config_path = tmp_path / "ordering-kiosk-none.toml"
    config_path.write_text(none_preset)

    config = load_config(config_path)
    assert config.merchants.backend == "none"

    engine = MagicMock()
    engine.health.return_value = True
    engine.list_models.return_value = [config.intelligence.default_model]

    system = SystemBuilder(config).engine_instance(engine, key="ollama").build()
    try:
        ordering_tools = [t for t in system.tools if t.spec.category == "ordering"]
        assert ordering_tools, "expected the preset's ordering tools to still load"
        assert all(t._merchant is None for t in ordering_tools)

        view = next(t for t in ordering_tools if t.spec.name == "cart_view")
        result = view.execute()
        assert result.success is False
        assert "merchant_unavailable" in result.content
    finally:
        system.close()


def test_agent_system_prompt_carries_the_notes_are_not_guarantees_rule():
    """The doctrine's most violable rule has to reach the Agent, and the only
    live path for that is the system prompt.

    `src/openjarvis/skills/data/ordering.toml` used to carry this guidance,
    but it was inert: `load_skill()` never populates `markdown_content` for a
    flat TOML, the default `skills_dir` does not discover
    `skills/data/` at all, and even a loaded skill only surfaces its text when
    the model chooses to call it -- the wrong mechanism for a rule the model
    must never violate. That file is gone; this test fails if the prompt path
    that replaced it ever stops being wired up, or if the prompt text is
    edited down and loses the rule.
    """
    from openjarvis.core.config import load_config
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    config = load_config(PRESET_PATH)
    assert config.agent.system_prompt_path == str(PROMPT_PATH), (
        "the preset must point at the prompt file carrying the ordering "
        "doctrine -- system_prompt_path drifted or was removed"
    )

    prompt = resolve_agent_system_prompt(config.agent)
    assert prompt is not None, "system_prompt_path resolved to no prompt text"
    assert "requests, not guarantees" in prompt
    assert "echoes it back" in prompt
    # The rule names both what to say and what never to say -- either one
    # disappearing is the rule getting softened, not just reworded.
    assert "tôi đã ghi ít đường cho bạn" in prompt
    assert "đã xác nhận ít đường" in prompt
