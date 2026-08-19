"""Ordering tools.

Every tool here declares exactly one of ``mutates`` or ``observes`` in its spec
metadata, and ``tests/tools/test_ordering_doctrine.py`` enforces it. That is
what structurally prevents a single ``commerce_checkout()``: such a tool would
have to both change the order and report what the order became, and no tool is
allowed to do both. The Agent therefore has to reason between every action and
its consequence.

Internals may be as deterministic as they like. Combining a mutation with its
observation is the only thing forbidden.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.merchants.port import MerchantPort
from openjarvis.tools._stubs import BaseTool, ToolSpec

OBSERVES = {"observes": True}
MUTATES = {"mutates": True}


class _MerchantTool(BaseTool):
    """Shared plumbing: the injected merchant and the failure when it is absent."""

    def __init__(self) -> None:
        # Set by SystemBuilder._inject_tool_deps. None outside a built system.
        self._merchant: Optional[MerchantPort] = None

    def _require_merchant(self) -> Optional[ToolResult]:
        if self._merchant is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="merchant_unavailable: no merchant is configured",
            )
        return None

    @staticmethod
    def _ok(name: str, payload: dict[str, Any]) -> ToolResult:
        return ToolResult(tool_name=name, success=True, content=json.dumps(payload))

    @staticmethod
    def _fail(name: str, reason: str) -> ToolResult:
        return ToolResult(tool_name=name, success=False, content=reason)


@ToolRegistry.register("branch_list")
class BranchListTool(_MerchantTool):
    """Which shops exist. Nothing else works until one is chosen."""

    tool_id = "branch_list"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="branch_list",
            description=(
                "List the merchant's branches with their slug, name and "
                "address. Menus and orders are per branch, so call this "
                "first when you do not already know which branch the "
                "customer is at. Read-only."
            ),
            parameters={"type": "object", "properties": {}},
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        branches = self._merchant.list_branches()
        return self._ok(
            "branch_list", {"branches": [asdict(b) for b in branches]}
        )


@ToolRegistry.register("menu_search")
class MenuSearchTool(_MerchantTool):
    """Find products. This is where recommendation gets its material."""

    tool_id = "menu_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="menu_search",
            description=(
                "Search one branch's menu. Returns matching products with "
                "their availability and their variants -- each variant is a "
                "size with its own slug and its own price, and the variant "
                "slug is what cart_add takes. An empty query returns the "
                "whole menu. Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name or category to match; empty for all.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch slug from branch_list.",
                    },
                },
                "required": ["query", "branch"],
            },
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        branch = str(params.get("branch", "")).strip()
        if not branch:
            return self._fail("menu_search", "branch_required")
        if branch not in {b.slug for b in self._merchant.list_branches()}:
            return self._fail("menu_search", "unknown_branch")
        products = self._merchant.search_menu(str(params.get("query", "")), branch)
        return self._ok(
            "menu_search", {"products": [asdict(p) for p in products]}
        )


@ToolRegistry.register("menu_item")
class MenuItemTool(_MerchantTool):
    """Read one product in full, including every priced variant."""

    tool_id = "menu_item"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="menu_item",
            description=(
                "Read one product on one branch's menu: its availability and "
                "each variant with its size, price and slug. Call this when "
                "you need a variant slug before adding to the cart. There "
                "are no sugar or ice options here -- anything the merchant "
                "does not price as a variant goes in cart_add's note. "
                "Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "product": {
                        "type": "string",
                        "description": "Product slug from menu_search.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch slug from branch_list.",
                    },
                },
                "required": ["product", "branch"],
            },
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        branch = str(params.get("branch", "")).strip()
        if not branch:
            return self._fail("menu_item", "branch_required")
        product = self._merchant.get_product(
            str(params.get("product", "")), branch
        )
        if product is None:
            return self._fail("menu_item", "unknown_product")
        return self._ok("menu_item", asdict(product))
