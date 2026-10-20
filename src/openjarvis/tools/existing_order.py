"""Display normalized, freshly verified facts about an owned existing order."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import ToolSpec
from openjarvis.tools.display import DisplayPaymentQrTool, _DisplayTool


def _created_at(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            date = datetime.strptime(value.split(" (")[0], "%a %b %d %Y %H:%M:%S GMT%z")
        except ValueError:
            return None
    return date.timestamp() if date.tzinfo is not None else None


@ToolRegistry.register("display_existing_order")
class DisplayExistingOrderTool(_DisplayTool):
    """Internal procedure primitive; never creates an order or payment."""

    tool_id = "display_existing_order"

    def __init__(self) -> None:
        super().__init__()
        self._payment_tool: DisplayPaymentQrTool | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Show normalized facts from an owned-order read.",
            parameters={"type": "object"},
            category="display",
        )

    def execute(self, **params: Any) -> ToolResult:
        if self._presentation is None:
            return ToolResult(
                tool_name=self.tool_id, content="order_not_owned", success=False
            )
        with self._presentation.order_read_scope(
            params.get("order_id"), params.get("read_token", "")
        ) as current:
            if not current:
                return ToolResult(
                    tool_name=self.tool_id,
                    content="order_read_superseded",
                    success=False,
                )
            return self._display_verified_order(params)

    def _display_verified_order(self, params: dict[str, Any]) -> ToolResult:
        order_id = params.get("order_id")
        known = (
            next(
                (
                    row
                    for row in self._presentation.known_orders()
                    if row["order_id"] == order_id
                ),
                None,
            )
            if self._presentation
            else None
        )
        if known is None:
            return ToolResult(
                tool_name=self.tool_id, content="order_not_owned", success=False
            )
        summary = {
            key: params[key]
            for key in (
                "order_id",
                "status",
                "payment_status",
                "total",
                "order_type",
                "branch",
                "table_name",
                "lines",
                "created_at",
                "payment_slug",
            )
            if key in params
        }
        created = _created_at(params.get("created_at"))
        window = params.get("payment_window_seconds")
        expired = isinstance(window, (int, float)) and (
            created is None or time.time() >= created + window
        )
        summary["payment_window_expired"] = expired
        summary["payable"] = (
            summary.get("status") == "pending"
            and summary.get("payment_status") == "pending"
            and bool(params.get("qr_code"))
            and not expired
        )
        if not self._presentation.update_order(order_id, summary):
            return ToolResult(
                tool_name=self.tool_id, content="order_not_owned", success=False
            )
        receipt = {
            key: value
            for key, value in summary.items()
            if key
            not in {
                "payment_status",
                "payment_slug",
                "payable",
                "payment_window_expired",
            }
        }
        if not summary["payable"] and (
            summary.get("status") != "paid" or params.get("display", True) is False
        ):
            self._presentation.invalidate_order_qr(order_id, receipt)
        if params.get("display", True) is False:
            return ToolResult(
                tool_name=self.tool_id,
                content=json.dumps(summary),
                metadata={"continue_agent": True},
            )
        if summary.get("status") == "paid":
            result = self._publish({"view": "bill", **receipt})
        elif summary["payable"]:
            if self._payment_tool is None:
                return ToolResult(
                    tool_name=self.tool_id,
                    content="payment_display_unavailable",
                    success=False,
                )
            result = self._payment_tool.execute(
                **{
                    key: value
                    for key, value in summary.items()
                    if key
                    not in {"payment_status", "payable", "payment_window_expired"}
                },
                qr_code=params["qr_code"],
                customer_message="",
                preserve_draft=True,
            )
        else:
            return ToolResult(
                tool_name=self.tool_id, content=json.dumps(summary), success=False
            )
        if result.success:
            result.content = json.dumps(summary)
            # The reply was written before the read, for the QR it asked for;
            # a paid bill is answered by the agent from the verified result.
            message = params.get("customer_message")
            if summary["payable"] and isinstance(message, str) and message.strip():
                result.metadata["customer_message"] = message.strip()
                result.metadata["completed_display"] = True
                result.metadata.pop("continue_agent", None)
            else:
                result.metadata.pop("customer_message", None)
                result.metadata["continue_agent"] = True
        return result
