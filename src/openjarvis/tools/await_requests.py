"""Wait for the customer's earlier requests that are still being handled.

A correction ("remove that") can arrive while the request it refers to is
still running in a parallel Agent run. This tool lets the run wait for those
requests before it changes what they add. It is a read: it changes nothing,
so it never queues behind another run's write.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.conversation import current_conversation_id
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.runs import RUN_TRACKER, current_voice_run
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

AWAIT_EARLIER_SECONDS = 15.0

FINISHED = "earlier_requests_finished"
NONE_IN_FLIGHT = "no_earlier_requests"
STILL_RUNNING = (
    "earlier_requests_still_running: they did not finish within 15 s. "
    "Tell the customer and ask before changing them."
)


@ToolRegistry.register("await_earlier_requests")
class AwaitEarlierRequestsTool(BaseTool):
    """Block until the customer's earlier in-flight requests have finished."""

    tool_id = "await_earlier_requests"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="await_earlier_requests",
            description=(
                "Wait until the customer's earlier requests that are still in "
                "progress have finished (at most 15 s). Call it before changing "
                "or removing what such a request adds, then read "
                "background_actions."
            ),
            parameters={"type": "object", "properties": {}},
            category="orchestration",
            timeout_seconds=20.0,
            metadata={"read_only": True},
        )

    def execute(self, **params: Any) -> ToolResult:
        del params
        cid, run = current_conversation_id(), current_voice_run()
        if not cid or not run:
            return ToolResult(
                tool_name="await_earlier_requests",
                content=NONE_IN_FLIGHT,
                success=True,
            )
        if RUN_TRACKER.wait_others(cid, run, AWAIT_EARLIER_SECONDS):
            return ToolResult(
                tool_name="await_earlier_requests",
                content=FINISHED,
                success=True,
            )
        return ToolResult(
            tool_name="await_earlier_requests",
            content=STILL_RUNNING,
            success=False,
        )
