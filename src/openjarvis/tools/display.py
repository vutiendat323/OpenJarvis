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
from openjarvis.tools import evidence as _evidence
from openjarvis.tools._stubs import BaseTool, ToolSpec

if TYPE_CHECKING:
    from openjarvis.kiosk.presentation import PresentationSessionManager

DISPLAYS = {"displays": True}

# Only these reach the page. A model that invents an "html" or "onclick" field
# gets it dropped here rather than at render time.
_ITEM_FIELDS = ("id", "name", "price", "available", "image_url", "note")
_LINE_FIELDS = ("name", "size", "note", "quantity", "line_total")
_BILL_FIELDS = ("order_id", "status", "order_type", "branch", "lines", "total")
_PAYMENT_QR_FIELDS = ("order_id", "payment_slug", "status", "qr_code", "total")
_REQUIRED_PAYMENT_QR_FIELDS = ("order_id", "payment_slug", "status", "qr_code")


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


def _menu_items_from_latest_http() -> list[dict[str, Any]]:
    content = _evidence.last_result("http_request")
    start = content.find("{")
    if start < 0:
        return []
    try:
        payload = json.loads(content[start:])
    except (TypeError, json.JSONDecodeError):
        return []

    result = payload.get("result") if isinstance(payload, dict) else None
    pages = result.get("items") if isinstance(result, dict) else None
    if not isinstance(pages, list):
        return []

    items: list[dict[str, Any]] = []
    for page in pages:
        menu_items = page.get("menuItems") if isinstance(page, dict) else None
        if not isinstance(menu_items, list):
            continue
        for menu_item in menu_items:
            product = (
                menu_item.get("product") if isinstance(menu_item, dict) else None
            )
            if not isinstance(product, dict) or not product.get("name"):
                continue
            variants = product.get("variants")
            variant = variants[0] if isinstance(variants, list) and variants else {}
            if not isinstance(variant, dict):
                variant = {}
            item = {
                "id": variant.get("slug") or product.get("slug"),
                "name": product["name"],
                "price": variant.get("price"),
                "available": product.get("isActive"),
            }
            description = product.get("description")
            if isinstance(description, str) and description.strip():
                item["note"] = description
            items.append(
                {key: value for key, value in item.items() if value is not None}
            )
    return items


@ToolRegistry.register("display_menu")
class DisplayMenuTool(_DisplayTool):
    """Put freshly retrieved menu items on the customer's screen."""

    tool_id = "display_menu"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_menu",
            description=(
                "Show one or more freshly retrieved items on the customer's "
                "screen. For a category browse, omit items and set "
                "all_from_latest_http=true to show every product directly from "
                "the latest successful HTTP menu response. For a filtered search "
                "or recommendation, pass only the matching items. "
                "Each item: id, name, price, available, and optionally "
                "image_url and note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Items to show, in the order to show them.",
                        "minItems": 1,
                        "items": {"type": "object"},
                    },
                    "all_from_latest_http": {
                        "type": "boolean",
                        "description": (
                            "Use every product from the latest successful "
                            "http_request menu response."
                        ),
                    },
                },
                "required": [],
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        rows = params.get("items") or []
        from_http = bool(params.get("all_from_latest_http")) and not rows
        if from_http:
            rows = _menu_items_from_latest_http()
        items = [_picked(row, _ITEM_FIELDS) for row in rows]
        picked = [i for i in items if i]
        if not picked:
            # An empty screen published as success reads to the model (and
            # then to the customer, in its own voice) as "the menu is shown"
            # when nothing is. Refuse instead, so a model with no real items
            # to hand it -- no fresh read, nothing left in context -- gets a
            # signal to go fetch some rather than a false success to narrate.
            return ToolResult(
                tool_name="display_menu",
                content=("menu_evidence_missing" if from_http else "items_required"),
                success=False,
            )
        return self._publish({"view": "menu", "items": picked})


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

    def __init__(self) -> None:
        super().__init__()
        # Set by SystemBuilder from config. Empty means no QR can be displayed:
        # provenance is required, never assumed.
        self._payment_trusted_origins: tuple = ()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_payment_qr",
            description=(
                "Show the QR value from a payment response. Omit qr_code to "
                "reuse it directly from the latest trusted http_request JSON "
                "in this conversation instead of copying a large base64 value."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "payment_slug": {"type": "string"},
                    "status": {"type": "string"},
                    "qr_code": {"type": "string"},
                    "total": {"type": "integer", "description": "Payment amount in VND."},
                },
                "required": ["order_id", "payment_slug", "status"],
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        qr_code = params.get("qr_code")
        if qr_code is None:
            qr_code = _evidence.latest_json_string(
                "qrCode",
                from_tool="http_request",
                require_ok=True,
                trusted_origins=self._payment_trusted_origins,
            )
            params = {**params, "qr_code": qr_code}
        elif not isinstance(qr_code, str) or not qr_code.strip():
            return ToolResult(
                tool_name="display_payment_qr",
                content="qr_code_required",
                success=False,
            )

        total = params.get("total")
        if total is None:
            total = _evidence.latest_json_number(
                "amount",
                from_tool="http_request",
                require_ok=True,
                trusted_origins=self._payment_trusted_origins,
            ) or _evidence.latest_json_number(
                "total",
                from_tool="http_request",
                require_ok=True,
                trusted_origins=self._payment_trusted_origins,
            )
        if total is not None:
            try:
                params = {**params, "total": int(total)}
            except (TypeError, ValueError):
                pass

        payment = _picked(params, _PAYMENT_QR_FIELDS)
        complete = all(
            isinstance(payment.get(field), str) and payment[field].strip()
            for field in _REQUIRED_PAYMENT_QR_FIELDS
        )
        # Four conditions: this exact string, from http_request, with a 2xx,
        # from a trusted origin, in this conversation. observed_in_tool_output
        # treats empty trusted_origins as "no origin restriction", so an empty
        # configured list is checked here instead -- it must refuse everything.
        verified = (
            complete
            and bool(self._payment_trusted_origins)
            and _evidence.observed_in_tool_output(
                qr_code,
                from_tool="http_request",
                require_ok=True,
                trusted_origins=self._payment_trusted_origins,
            )
        )
        if not verified:
            return ToolResult(
                tool_name="display_payment_qr",
                content="payment_evidence_missing",
                success=False,
            )
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
