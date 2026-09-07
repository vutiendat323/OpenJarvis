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
from openjarvis.merchants.port import (
    MerchantPort,
    OrderApprovalRejected,
    OrderApprovalRequired,
)
from openjarvis.tools._stubs import BaseTool, ToolSpec

OBSERVES = {"observes": True}
MUTATES = {"mutates": True}

# The codes `MerchantPort.place_order` raises as `ValueError`.
ORDER_PLACE_ERROR_CODES = frozenset(
    {
        "invalid_order_type",
        "unknown_branch",
        "cart_empty",
        "cart_line_invalid",
        "cart_line_unavailable",
        "cart_line_price_changed",
    }
)


class _MerchantTool(BaseTool):
    """Shared plumbing: the injected merchant and the failure when it is absent."""

    def __init__(self) -> None:
        # Set by SystemBuilder._inject_tool_deps. None outside a built system.
        self._merchant: Optional[MerchantPort] = None
        self._display_menu: Optional[BaseTool] = None

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

    def _snapshot_failure(self, resource_type: str) -> Optional[ToolResult]:
        """Keep an empty/stale read from masquerading as a successful read."""
        status = getattr(self._merchant, "snapshot_status", None)
        if not callable(status):
            return None
        reason = status(resource_type)
        return self._fail(self.spec.name, reason) if reason else None


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
        snapshot_failure = self._snapshot_failure("branch")
        if snapshot_failure is not None:
            return snapshot_failure
        branches = self._merchant.list_branches()
        return self._ok("branch_list", {"branches": [asdict(b) for b in branches]})


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
                "whole menu. If the result says menu_not_synced or menu_stale, "
                "sync the menu once before retrying. Read-only."
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
        snapshot_failure = self._snapshot_failure("branch")
        if snapshot_failure is not None:
            return snapshot_failure
        if branch not in {b.slug for b in self._merchant.list_branches()}:
            return self._fail("menu_search", "unknown_branch")
        snapshot_failure = self._snapshot_failure("menu_item")
        if snapshot_failure is not None:
            return snapshot_failure
        products = self._merchant.search_menu(str(params.get("query", "")), branch)
        if self._display_menu is not None:
            self._display_menu.execute(
                items=[
                    {
                        "id": product.slug,
                        "name": product.name,
                        "price": product.variants[0].price if product.variants else 0,
                        "available": product.available,
                        "note": product.category,
                    }
                    for product in products
                ]
            )
        return self._ok("menu_search", {"products": [asdict(p) for p in products]})


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
        snapshot_failure = self._snapshot_failure("branch")
        if snapshot_failure is not None:
            return snapshot_failure
        snapshot_failure = self._snapshot_failure("menu_item")
        if snapshot_failure is not None:
            return snapshot_failure
        product = self._merchant.get_product(str(params.get("product", "")), branch)
        if product is None:
            return self._fail("menu_item", "unknown_product")
        return self._ok("menu_item", asdict(product))


@ToolRegistry.register("cart_set")
class CartSetTool(_MerchantTool):
    """Declare the whole cart. Repeating the call changes nothing."""

    tool_id = "cart_set"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_set",
            description=(
                "Declare everything the customer is ordering, as one list. "
                "This REPLACES the cart -- it does not add to it, so send "
                "every item every time, and calling it twice with the same "
                "list leaves the same cart. To remove something, send the "
                "list without it; to empty the cart, send an empty list. "
                "`variant` is a variant slug from menu_search or menu_item "
                "-- a size with its own price -- not a product slug. "
                "Anything the merchant does not price as a variant (sugar "
                "level, ice, 'no straw') goes in `note`, which is free text "
                "a person at the shop reads. "
                "Returns only whether it was set; it does NOT tell you what "
                "the cart now contains. Call cart_view afterwards and check "
                "it against what the customer asked for."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The complete order, replacing the cart.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "variant": {
                                    "type": "string",
                                    "description": "Variant slug (a specific size).",
                                },
                                "quantity": {
                                    "type": "integer",
                                    "description": "How many.",
                                },
                                "note": {
                                    "type": "string",
                                    "description": (
                                        "Free text for the shop, e.g. 'ít đường'."
                                    ),
                                },
                            },
                            "required": ["variant"],
                        },
                    }
                },
                "required": ["items"],
            },
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        rows = params.get("items")
        if not isinstance(rows, list):
            return self._fail("cart_set", "items_required")
        items = []
        for row in rows:
            if not isinstance(row, dict):
                return self._fail("cart_set", "variant_unavailable")
            try:
                quantity = int(row.get("quantity", 1) or 1)
            except (TypeError, ValueError):
                return self._fail("cart_set", "variant_unavailable")
            items.append(
                (str(row.get("variant", "")), quantity, str(row.get("note", "") or ""))
            )
        try:
            self._merchant.set_cart(items)
        except (ValueError, TypeError):
            return self._fail("cart_set", "variant_unavailable")
        return self._ok("cart_set", {"set": True})


@ToolRegistry.register("cart_add")
class CartAddTool(_MerchantTool):
    """Add one item. Returns an acknowledgement, never the cart."""

    tool_id = "cart_add"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_add",
            description=(
                "Add one variant to the cart. `variant` is a variant slug "
                "from menu_search or menu_item -- a size with its own price "
                "-- not a product slug. Anything the merchant does not price "
                "as a variant (sugar level, ice, 'no straw') goes in `note`, "
                "which is free text a person at the shop reads. "
                "Returns only whether it was added and the new line id; it "
                "does NOT tell you what the cart now contains. Call "
                "cart_view afterwards and check it against what the "
                "customer asked for."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "variant": {
                        "type": "string",
                        "description": "Variant slug (a specific size).",
                    },
                    "quantity": {"type": "integer", "description": "How many."},
                    "note": {
                        "type": "string",
                        "description": (
                            "Free text for the shop, e.g. 'ít đường'. Nobody "
                            "validates it and nothing can verify it was "
                            "honoured -- a person reads it."
                        ),
                    },
                },
                "required": ["variant"],
            },
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        try:
            line_id = self._merchant.add_to_cart(
                str(params.get("variant", "")),
                int(params.get("quantity", 1) or 1),
                str(params.get("note", "") or ""),
            )
        except (ValueError, TypeError):
            return self._fail("cart_add", "variant_unavailable")
        return self._ok("cart_add", {"added": True, "line_id": line_id})


@ToolRegistry.register("cart_remove")
class CartRemoveTool(_MerchantTool):
    """Remove one line. Returns an acknowledgement, never the cart."""

    tool_id = "cart_remove"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_remove",
            description=(
                "Remove one line from the cart by its line id. Returns only "
                "whether it was removed. Call cart_view afterwards to see "
                "what the cart now contains."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "line_id": {
                        "type": "string",
                        "description": "Line id returned by cart_add or cart_view.",
                    }
                },
                "required": ["line_id"],
            },
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        if not self._merchant.remove_from_cart(str(params.get("line_id", ""))):
            return self._fail("cart_remove", "unknown_line")
        return self._ok("cart_remove", {"removed": True})


@ToolRegistry.register("cart_view")
class CartViewTool(_MerchantTool):
    """Read the real cart. This is the only thing that says what is in it."""

    tool_id = "cart_view"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_view",
            description=(
                "Read the current cart: every line with its size, note, "
                "quantity and price, plus the total. This is the only way "
                "to know what the cart contains. Read-only."
            ),
            parameters={"type": "object", "properties": {}},
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        cart = self._merchant.read_cart()
        return self._ok(
            "cart_view",
            {
                "lines": [asdict(line) for line in cart.lines],
                "total": cart.total,
            },
        )


@ToolRegistry.register("order_place")
class OrderPlaceTool(_MerchantTool):
    """Place the order. Does not pay, and does not say what was placed."""

    tool_id = "order_place"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="order_place",
            description=(
                "Place the current cart as an order at one branch. `type` is "
                "'at-table', 'take-out' or 'delivery' -- ask the customer, "
                "never guess: handing a takeaway customer a dine-in order is "
                "a real mistake. Does NOT take payment. "
                "Returns only the new order id; it does not tell you what "
                "the order contains. Call order_verify with that id to read "
                "back what the merchant recorded, and compare it with what "
                "the customer asked for before going further."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["at-table", "take-out", "delivery"],
                        "description": "How the customer is taking the order.",
                    },
                    "branch": {
                        "type": "string",
                        "description": "Branch slug from branch_list.",
                    },
                    "approval_id": {
                        "type": "string",
                        "description": "Approval id returned by a pending order_place.",
                    },
                },
                "required": ["type", "branch"],
            },
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.merchants.port import ORDER_TYPES

        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        order_type = str(params.get("type", "")).strip()
        if not order_type:
            return self._fail("order_place", "order_type_required")
        if order_type not in ORDER_TYPES:
            return self._fail("order_place", "invalid_order_type")
        branch = str(params.get("branch", "")).strip()
        if not branch:
            return self._fail("order_place", "branch_required")
        if not self._merchant.read_cart().lines:
            return self._fail("order_place", "cart_empty")
        try:
            order_id = self._merchant.place_order(
                order_type, branch, str(params.get("approval_id", "")).strip()
            )
        except OrderApprovalRequired as exc:
            return ToolResult(
                tool_name="order_place",
                success=False,
                content=json.dumps({"error": "approval_required"}),
                metadata={
                    "pending_approval": True,
                    "approval_id": exc.approval_id,
                    "request_hash": exc.request_hash,
                },
            )
        except OrderApprovalRejected as exc:
            return self._fail("order_place", exc.code)
        except ValueError as exc:
            # `place_order` raises a distinct code per failure; flattening them
            # all to `unknown_branch` hid `cart_line_price_changed`, which the
            # customer has to be told about. Unrecognised prose (an adapter
            # message, say) is not passed through to the Agent as a code.
            code = str(exc)
            return self._fail(
                "order_place",
                code if code in ORDER_PLACE_ERROR_CODES else "order_place_failed",
            )
        return self._ok("order_place", {"placed": True, "order_id": order_id})


@ToolRegistry.register("order_verify")
class OrderVerifyTool(_MerchantTool):
    """Read the order back from the merchant."""

    tool_id = "order_verify"

    def __init__(self) -> None:
        super().__init__()
        self._display_bill: Optional[BaseTool] = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="order_verify",
            description=(
                "Read an order back from the merchant by id: its lines, "
                "total, type, branch and status, as the merchant records "
                "them. This is the merchant's answer, not yours -- check it "
                "against what the customer asked for. "
                "Note fields are echoed, not confirmed: a note reads back "
                "unchanged whether or not anyone acts on it, so tell the "
                "customer their request was recorded, never that it is "
                "guaranteed. Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order id returned by order_place.",
                    }
                },
                "required": ["order_id"],
            },
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        order = self._merchant.read_order(str(params.get("order_id", "")))
        if order is None:
            return self._fail("order_verify", "unknown_order")
        bill = {
            "order_id": order.order_id,
            "status": order.status,
            "order_type": order.order_type,
            "branch": order.branch_slug,
            "lines": [asdict(line) for line in order.lines],
            "total": order.total,
        }
        if self._display_bill is not None:
            self._display_bill.execute(**bill)
        return self._ok(
            "order_verify",
            {
                **bill,
                # Stated in the payload, not only in the description: the
                # Agent reads results far more reliably than it re-reads a
                # tool spec, and overclaiming here means telling a customer
                # their drink is confirmed less sweet when nothing checked.
                "notes_are_unverified": True,
            },
        )
