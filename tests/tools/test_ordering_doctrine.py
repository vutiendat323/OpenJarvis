"""Mutation and observation never share a tool.

This is the rule that structurally prevents a single commerce_checkout():
such a tool would have to both change the order and report what it became.
If this test fails, a reasoning step has been removed from the Agent's loop.
"""

from __future__ import annotations

import inspect

import pytest

from openjarvis.tools import ordering


def _ordering_tools():
    """Enumerate the tool classes from the module, not from ToolRegistry.

    `tests/conftest.py::_clean_registries` calls `ToolRegistry.clear()` autouse
    before every test, so a registry walk here would iterate nothing and every
    assertion below would pass vacuously -- a silent hole in the one test that
    guards the doctrine.

    The base class is looked up on the module object at call time
    (`getattr(ordering, "_MerchantTool")`), not imported once at module load.
    `tool_resolver.py` reloads every `openjarvis.tools.*` module whenever
    `ToolRegistry.keys()` is empty -- which the autouse `clear()` above
    guarantees before every test in a whole-suite run. A reload re-executes
    `ordering.py` into the same module dict, producing a *new* `_MerchantTool`
    class object. A stale module-level `from ... import _MerchantTool` would
    then hold the *old* class, `issubclass` would be False for every tool, and
    this function would silently return `[]`.

    That emptiness is asserted below rather than just returned, because an
    empty list here does not fail loudly on its own: `all(... for t in [])` is
    `True`, so a test built on `all()` over an empty `_ordering_tools()` would
    pass while checking nothing -- exactly the silent hole this doctrine test
    exists to prevent.
    """
    base = getattr(ordering, "_MerchantTool")
    tools = [
        member()
        for _, member in inspect.getmembers(ordering, inspect.isclass)
        if issubclass(member, base)
        and member is not base
        and member.__module__ == ordering.__name__
    ]
    assert tools, (
        "_ordering_tools() enumerated no tools -- this must fail loudly "
        "rather than let every doctrine assertion below pass vacuously"
    )
    return tools


def test_every_tool_in_the_module_is_checked():
    """A new ordering tool is covered automatically; this pins the set so a
    silently emptied list cannot make the parametrized tests vacuous."""
    assert {tool.spec.name for tool in _ordering_tools()} == {
        "branch_list",
        "menu_search",
        "menu_item",
        "cart_add",
        "cart_remove",
        "cart_view",
        "order_place",
        "order_verify",
    }


def test_every_tool_is_in_the_ordering_category():
    assert all(tool.spec.category == "ordering" for tool in _ordering_tools())


@pytest.mark.parametrize("tool", _ordering_tools(), ids=lambda t: t.spec.name)
def test_each_tool_declares_exactly_one_kind(tool):
    metadata = tool.spec.metadata
    mutates = bool(metadata.get("mutates"))
    observes = bool(metadata.get("observes"))
    assert mutates != observes, (
        f"{tool.spec.name} declares mutates={mutates} observes={observes}; "
        "a tool must be exactly one of the two"
    )


@pytest.mark.parametrize("tool", _ordering_tools(), ids=lambda t: t.spec.name)
def test_a_mutating_tool_takes_no_observation_shortcut(tool):
    """A mutating tool's description must send the Agent to an observer.

    Prose, not structure -- but the failure it guards against is a future
    edit that quietly makes cart_add return the cart 'for convenience'.
    """
    if not tool.spec.metadata.get("mutates"):
        pytest.skip("observation tool")
    assert any(
        observer in tool.spec.description
        for observer in ("cart_view", "order_verify")
    ), f"{tool.spec.name} does not tell the Agent how to see the result"
