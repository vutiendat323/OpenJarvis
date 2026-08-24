"""What the customer sees, as structured data on the event bus.

The LLM never emits markup. It supplies fields; the page renders them with
fixed templates and escapes every string. Anything else would be an XSS
surface and an unpredictable layout, on a screen a customer is looking at.

These tools are exempt from the mutation/observation doctrine: they do not
touch merchant state, they draw. They declare ``displays`` so the doctrine
test does not classify them as either.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

if TYPE_CHECKING:
    from openjarvis.kiosk.presentation import PresentationSessionManager

DISPLAYS = {"displays": True}

# Only these reach the page. A model that invents an "html" or "onclick" field
# gets it dropped here rather than at render time.
_ITEM_FIELDS = ("id", "name", "price", "available", "image_url", "note")
_LINE_FIELDS = ("name", "size", "note", "quantity", "line_total")
_BILL_FIELDS = ("order_id", "status", "order_type", "branch", "lines", "total")
_PAYMENT_QR_FIELDS = ("order_id", "payment_slug", "status", "qr_code")


class _DisplayTool(BaseTool):
    def __init__(self) -> None:
        # ``_bus`` remains the direct-construction fallback used by narrow tests.
        self._bus: Optional[EventBus] = None
        # Set by SystemBuilder after external MCP discovery completes.
        self._presentation: Optional[PresentationSessionManager] = None

    def _publish(self, payload: dict[str, Any]) -> ToolResult:
        if self._presentation is not None:
            return self._presentation.publish(payload)
        if self._bus is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="display_unavailable: no event bus is configured",
            )
        self._bus.publish(EventType.DISPLAY_UPDATE, payload)
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=json.dumps({"shown": payload["view"]}),
        )


def _picked(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {key: row[key] for key in fields if key in row}


@ToolRegistry.register("display_menu")
class DisplayMenuTool(_DisplayTool):
    """Put a shortlist of items on the customer's screen."""

    tool_id = "display_menu"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_menu",
            description=(
                "Show items on the customer's screen. Pass the few you are "
                "actually recommending, not everything menu_search returned "
                "-- choosing what to show is part of the recommendation. "
                "Each item: id, name, price, available, and optionally "
                "image_url and note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Items to show, in the order to show them.",
                        "items": {"type": "object"},
                    }
                },
                "required": ["items"],
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        rows = params.get("items") or []
        items = [_picked(row, _ITEM_FIELDS) for row in rows]
        return self._publish({"view": "menu", "items": [i for i in items if i]})


@ToolRegistry.register("display_cart")
class DisplayCartTool(_DisplayTool):
    """Put the current cart on the customer's screen."""

    tool_id = "display_cart"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_cart",
            description=(
                "Show the cart on the customer's screen so they can check it "
                "before ordering. Pass the lines and total you read from "
                "cart_view -- do not invent them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "description": "Cart lines from cart_view.",
                        "items": {"type": "object"},
                    },
                    "total": {"type": "integer", "description": "Cart total."},
                },
                "required": ["lines", "total"],
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        rows = params.get("lines") or []
        lines = [_picked(row, _LINE_FIELDS) for row in rows]
        try:
            total = int(params.get("total", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        return self._publish(
            {"view": "cart", "lines": [line for line in lines if line], "total": total}
        )


@ToolRegistry.register("display_bill")
class DisplayBillTool(_DisplayTool):
    """Put a merchant-read order on the customer's screen."""

    tool_id = "display_bill"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_bill",
            description=(
                "Show an order bill exactly as order_verify read it from the "
                "merchant. Pass order_id, status, order_type, branch, lines "
                "and total from that verified readback; do not invent them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "status": {"type": "string"},
                    "order_type": {"type": "string"},
                    "branch": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "object"}},
                    "total": {"type": "integer"},
                },
                "required": list(_BILL_FIELDS),
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        bill = _picked(params, _BILL_FIELDS)
        rows = bill.get("lines") or []
        lines = [_picked(row, _LINE_FIELDS) for row in rows]
        try:
            total = int(bill.get("total", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        bill["lines"] = [line for line in lines if line]
        bill["total"] = total
        return self._publish({"view": "bill", **bill})


@ToolRegistry.register("display_payment_qr")
class DisplayPaymentQrTool(_DisplayTool):
    """Put a verified merchant payment QR value on the customer's screen."""

    tool_id = "display_payment_qr"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_payment_qr",
            description=(
                "Show the QR value from a verified payment snapshot. Call "
                "this explicitly only after source_verify and a subsequent "
                "structured_query(resource_type='payment'); never use an "
                "unverified source_execute receipt."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "payment_slug": {"type": "string"},
                    "status": {"type": "string"},
                    "qr_code": {"type": "string"},
                },
                "required": list(_PAYMENT_QR_FIELDS),
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        qr_code = params.get("qr_code")
        if not isinstance(qr_code, str) or not qr_code.strip():
            return ToolResult(
                tool_name="display_payment_qr",
                content="qr_code_required",
                success=False,
            )
        payment = _picked(params, _PAYMENT_QR_FIELDS)
        return self._publish({"view": "payment_qr", **payment})


@ToolRegistry.register("display_clear")
class DisplayClearTool(_DisplayTool):
    """Return the screen to its idle state."""

    tool_id = "display_clear"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_clear",
            description="Clear the customer's screen back to its idle state.",
            parameters={"type": "object", "properties": {}},
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        return self._publish({"view": "none"})
