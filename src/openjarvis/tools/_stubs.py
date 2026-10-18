"""ABC for tool implementations and the ToolExecutor dispatch engine.

Follows the same registry pattern as ``engine/_stubs.py`` and ``memory/_stubs.py``.
Each tool is registered via ``@ToolRegistry.register("name")`` and implements
``BaseTool`` with a ``spec`` property and ``execute()`` method.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import json
import time
from abc import ABC, abstractmethod
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from openjarvis.core.conversation import current_conversation_id
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.runs import RUN_TRACKER, StableOutcome, current_voice_run
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import evidence as _evidence

# How long a write from one Agent run waits for another run's write to finish
# before it is answered "pending" instead of dispatched (spec 2026-10-10 §5.2).
WRITE_WAIT_SECONDS = 5.0

# How long a checkout waits for the other runs of its session to stop changing
# the cart before it is answered "not settled" and creates no order (spec
# 2026-10-17 P8 / §7.7).
STABLE_CART_WAIT_SECONDS = 15.0

CART_NOT_SETTLED = (
    "cart_not_settled: other requests are still changing the cart after 15 s. "
    "No order was created. Tell the customer the cart is still being updated "
    "and ask whether to wait."
)
CART_CHANGED = (
    "cart_changed_during_checkout: the cart changed after this request began. "
    "No order was created. Handle the change, then ask whether to pay."
)
CHECKOUT_SUPERSEDED = (
    "checkout_superseded: another checkout of this conversation ran while this "
    "one waited, so this one created no order. Do not retry it on your own; "
    "tell the customer what the other checkout did."
)

# True while a tool's own ``execute`` runs. A tool already dispatched runs all
# its steps (spec 2026-10-10 D2): a skill's later steps call ``ToolExecutor``
# nested, and an interrupt must not abort them half way.
_IN_TOOL: ContextVar[bool] = ContextVar("openjarvis_tool_executing", default=False)


def tool_is_executing() -> bool:
    """Return True while the current context is inside a tool's own ``execute``."""
    return _IN_TOOL.get()


# ---------------------------------------------------------------------------
# ToolSpec — metadata describing a tool's interface
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolSpec:
    """Declarative description of a tool's interface and characteristics."""

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    category: str = ""
    cost_estimate: float = 0.0
    latency_estimate: float = 0.0
    requires_confirmation: bool = False
    timeout_seconds: float = 30.0
    required_capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BaseTool ABC
# ---------------------------------------------------------------------------


class BaseTool(ABC):
    """Base class for all tool implementations.

    Subclasses must be registered via
    ``@ToolRegistry.register("name")`` to become discoverable.
    """

    tool_id: str
    is_local: bool = True

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool specification."""

    @abstractmethod
    def execute(self, **params: Any) -> ToolResult:
        """Execute the tool with the given parameters."""

    def to_openai_function(self) -> Dict[str, Any]:
        """Convert to OpenAI function-calling format."""
        from openjarvis.tools.description_loader import (
            get_tool_description_override,
        )

        s = self.spec
        desc = get_tool_description_override(s.name) or s.description
        return {
            "type": "function",
            "function": {
                "name": s.name,
                "description": desc,
                "parameters": s.parameters,
            },
        }


# ---------------------------------------------------------------------------
# ToolExecutor — dispatch engine for tool calls
# ---------------------------------------------------------------------------


class ToolExecutor:
    """Dispatch tool calls to registered tools with event bus integration.

    Parameters
    ----------
    tools:
        List of tool instances to make available.
    bus:
        Optional event bus for publishing ``TOOL_CALL_START``/``TOOL_CALL_END``.
    """

    def __init__(
        self,
        tools: List[BaseTool],
        bus: Optional[EventBus] = None,
        *,
        interactive: bool = False,
        confirm_callback: Optional[Callable[[str], bool]] = None,
        default_timeout: float = 30.0,
        capability_policy: Optional[Any] = None,
        agent_id: str = "",
        boundary_guard: Optional[Any] = None,
    ) -> None:
        self._tools: Dict[str, BaseTool] = {t.spec.name: t for t in tools}
        self._bus = bus
        self._interactive = interactive
        self._confirm_callback = confirm_callback
        self._default_timeout = default_timeout
        self._capability_policy = capability_policy
        self._agent_id = agent_id
        self._boundary_guard = boundary_guard

    def execute(self, tool_call: ToolCall) -> ToolResult:
        if not _IN_TOOL.get():
            tool = self._tools.get(tool_call.name)
            cid, run = current_conversation_id(), current_voice_run()
            if (
                cid
                and run
                and tool is not None
                and tool.spec.metadata.get("waits_for_stable_cart") is True
            ):
                from openjarvis.agents._stubs import check_agent_cancelled

                check_agent_cancelled()
                return self._execute_when_cart_is_stable(tool_call, tool, cid, run)
        return self._execute_gated(tool_call)

    def _execute_when_cart_is_stable(
        self, tool_call: ToolCall, tool: BaseTool, cid: str, run: str
    ) -> ToolResult:
        """Hold a checkout until other runs stop changing the cart (spec P8).

        Nothing is dispatched unless the cart is stable; once it is, the cart
        stays frozen against other runs' writes until the checkout has ended.
        """
        started = time.monotonic()
        deadline = started + STABLE_CART_WAIT_SECONDS
        revision_now = getattr(tool, "current_cart_revision", None)
        revision_before = revision_now() if callable(revision_now) else None
        outcome = RUN_TRACKER.wait_stable(cid, run, STABLE_CART_WAIT_SECONDS)
        # Only the runs in flight when the wait began may move the revision
        # the model bound (ruling 7); a later run's write ended the wait above.
        waited_for = RUN_TRACKER.waited_for(cid, run)
        if outcome is StableOutcome.CHANGED:
            return ToolResult(
                tool_name=tool_call.name,
                success=False,
                content=CART_CHANGED,
                metadata={"dispatched": False, "cart_changed": True},
            )
        if outcome is not StableOutcome.STABLE:
            return self._cart_not_settled(tool_call)
        try:
            # The freeze is exclusive: another checkout may hold it. Wait for
            # its thaw within what is left of the stable-cart budget.
            while not RUN_TRACKER.freeze(cid, run):
                if not RUN_TRACKER.wait_unfrozen(
                    cid, run, max(0.0, deadline - time.monotonic())
                ):
                    return self._cart_not_settled(tool_call)
            # Held now, so no other checkout can move the epoch. One that ran
            # since this run began may already have ordered these very lines;
            # a replacement-lines checkout has no revision claim to stop a
            # second order (ruling 17).
            if RUN_TRACKER.superseded(cid, run):
                return ToolResult(
                    tool_name=tool_call.name,
                    success=False,
                    content=CHECKOUT_SUPERSEDED,
                    metadata={"dispatched": False, "checkout_superseded": True},
                )
            tool_call = self._follow_cart_revision(
                tool, tool_call, revision_before, waited_for
            )
            return self._execute_gated(tool_call)
        finally:
            RUN_TRACKER.unfreeze(cid, run)

    @staticmethod
    def _cart_not_settled(tool_call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_name=tool_call.name,
            success=False,
            content=CART_NOT_SETTLED,
            metadata={"dispatched": False, "cart_not_settled": True},
        )

    @staticmethod
    def _follow_cart_revision(
        tool: BaseTool, tool_call: ToolCall, before: Any, waited_for: frozenset[str]
    ) -> ToolCall:
        """Carry the checkout's revision across changes made while it waited.

        The model bound ``cart_revision`` to the cart it saw when checkout
        began. Runs already in flight then (not other waiting checkouts), and
        finished before the cart settled, are meant to be in the order (spec
        7.7). The revision moves forward only when it was current when the
        wait began and every bump since was made by one of those runs. A touch
        edit, a run that began later or an unknown author leaves the stale
        revision for the revision claim to refuse, so no order is created.
        """
        changes_since = getattr(tool, "cart_revision_changes", None)
        if type(before) is not int or not callable(changes_since):
            return tool_call
        changes = changes_since(before)
        if changes is None:
            return tool_call
        now, authors = changes
        if (
            now == before
            or not authors
            or not all(author and author in waited_for for author in authors)
        ):
            return tool_call
        try:
            arguments = json.loads(tool_call.arguments or "{}")
        except json.JSONDecodeError:
            return tool_call
        if not isinstance(arguments, dict) or arguments.get("cart_revision") != before:
            return tool_call
        arguments["cart_revision"] = now
        return ToolCall(tool_call.id, tool_call.name, json.dumps(arguments))

    def _execute_gated(self, tool_call: ToolCall) -> ToolResult:
        from openjarvis.agents._stubs import (
            _RUN_WORKER_LEASE,
            check_agent_cancelled,
            dispatch_is_committed,
        )

        in_tool = _IN_TOOL.get()
        if not in_tool:
            check_agent_cancelled()
        lease = _RUN_WORKER_LEASE.get()
        tool = self._tools.get(tool_call.name)
        # Reads cannot disturb another run's write, so they never queue.
        read_only = tool is not None and tool.spec.metadata.get("read_only") is True
        cid = current_conversation_id()
        run = current_voice_run()
        write = lease is not None and not read_only
        tracked = bool(cid and run) and write
        # A nested step's outer call already passed the freeze check and holds
        # the gate: waiting here would block the freezer behind it (ruling 4).
        freezable = tracked and not in_tool
        waits_for_stable = (
            tool is not None and tool.spec.metadata.get("waits_for_stable_cart") is True
        )
        timeout = 0.0
        if write:
            timeout = (tool.spec.timeout_seconds if tool else 0.0) or (
                self._default_timeout
            )
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if freezable:
                # Another run froze the cart for its checkout: a write of this
                # run waits for the thaw (spec 2026-10-18 §5).
                if not RUN_TRACKER.wait_unfrozen(cid, run, remaining):
                    return self._pending_operation(tool_call)
            unregister = None
            if lease is None or read_only:
                scope = nullcontext(True)
            elif in_tool or dispatch_is_committed():
                # A dispatched tool's own step finishes even after barge-in (D2),
                # and so does a call the model completed (spec 2026-10-17 §5.4).
                scope = lease.tool_gate.enter(
                    lease,
                    wait_seconds=WRITE_WAIT_SECONDS,
                    same_conversation_wait_seconds=remaining,
                )
            else:
                # A queued write of a run the customer cut leaves the queue at once.
                unregister = lease.register_cancellation(lease.tool_gate.wake)
                scope = lease.tool_gate.enter(
                    lease,
                    wait_seconds=WRITE_WAIT_SECONDS,
                    cancelled=lambda: lease.is_cancelled,
                    same_conversation_wait_seconds=remaining,
                )
            try:
                with scope as acquired:
                    if unregister is not None:
                        unregister()
                    if not acquired:
                        return self._pending_operation(tool_call)
                    # Another run may have taken the freeze while this write
                    # sat in the gate; check again now that it is held.
                    if not freezable or RUN_TRACKER.wait_unfrozen(cid, run, 0.0):
                        if not in_tool:
                            check_agent_cancelled()
                            if lease is not None and tool is not None and not read_only:
                                unsettled = self._earlier_action_unsettled(tool_call)
                                if unsettled is not None:
                                    return unsettled
                        if tracked and not in_tool and not waits_for_stable:
                            # Only a write that is about to run counts as a
                            # change. A nested step belongs to an outer call
                            # already noted, and a checkout is not a change
                            # another waiting checkout must abort for: the
                            # revision claim refuses the second order.
                            RUN_TRACKER.note_write(cid, run)
                        return self._execute(tool_call)
            finally:
                if unregister is not None:
                    unregister()
            # Left the gate so FIFO waiters (the freezer's own writes) proceed;
            # wait for the thaw with the time that remains, then queue again.

    @staticmethod
    def _pending_operation(tool_call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_name=tool_call.name,
            success=False,
            content=(
                "A previous turn's tool operation is still running. "
                "No new tool was dispatched. Its outcome is pending, "
                "not failed or cancelled. Explain this to the user; "
                "do not retry or claim completion."
            ),
            metadata={"pending_operation": True, "dispatched": False},
        )

    @staticmethod
    def _earlier_action_unsettled(tool_call: ToolCall) -> ToolResult | None:
        """Hold a write that would act past an earlier failure the model never saw.

        Spec 2026-10-10 D4: when a write this one waited behind failed or ended
        unknown, nothing more happens (payment included) until the customer
        was told and answered.
        """
        from openjarvis.agents.action_ledger import ACTION_LEDGER
        from openjarvis.core.conversation import current_turn_nonce

        conversation_id = current_conversation_id()
        if not conversation_id or not ACTION_LEDGER.unseen_failures(
            conversation_id, current_turn_nonce()
        ):
            return None
        return ToolResult(
            tool_name=tool_call.name,
            success=False,
            content=(
                "An earlier request's action just finished as failed or with an "
                "unknown outcome. Nothing was dispatched. Read background_actions, "
                "tell the customer what happened and ask before continuing; do "
                "not pay or repeat that action without their answer."
            ),
            metadata={"dispatched": False, "earlier_action_unsettled": True},
        )

    def _execute(self, tool_call: ToolCall) -> ToolResult:
        """Parse arguments, dispatch to tool, measure latency, emit events."""
        invocation_id = uuid4().hex
        tool = self._tools.get(tool_call.name)
        if tool is None:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Unknown tool: {tool_call.name}",
                success=False,
                metadata={"dispatched": False},
            )

        from openjarvis.core.conversation import (
            SPEAKER_UNCONFIRMED_MESSAGE,
            speaker_blocks_tool,
        )

        # A turn whose speaker the Voice gate could not confirm must not
        # change cart, order or payment state; the Agent asks instead.
        if speaker_blocks_tool(tool_call.name):
            return ToolResult(
                tool_name=tool_call.name,
                content=SPEAKER_UNCONFIRMED_MESSAGE,
                success=False,
                metadata={"speaker_unconfirmed": True, "dispatched": False},
            )

        # Parse arguments
        try:
            params = json.loads(tool_call.arguments) if tool_call.arguments else {}
        except json.JSONDecodeError as exc:
            return ToolResult(
                tool_name=tool_call.name,
                content=f"Invalid arguments JSON: {exc}",
                success=False,
                metadata={"dispatched": False},
            )
        if not isinstance(params, dict):
            return ToolResult(
                tool_name=tool_call.name,
                content=(
                    "Invalid arguments: expected a JSON object, "
                    f"got {type(params).__name__}."
                ),
                success=False,
                metadata={"dispatched": False},
            )

        # Boundary guard: scan external tool arguments
        if self._boundary_guard is not None and not getattr(tool, "is_local", True):
            try:
                tool_call = self._boundary_guard.check_outbound(tool_call)
                # Re-parse arguments after potential redaction
                params = json.loads(tool_call.arguments) if tool_call.arguments else {}
                if not isinstance(params, dict):
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            "Invalid arguments: expected a JSON object, "
                            f"got {type(params).__name__}."
                        ),
                        success=False,
                        metadata={"dispatched": False},
                    )
            except Exception as exc:
                return ToolResult(
                    tool_name=tool_call.name,
                    content=f"Security block: {exc}",
                    success=False,
                    metadata={"dispatched": False},
                )

        # RBAC capability check
        if self._capability_policy and tool.spec.required_capabilities:
            for cap in tool.spec.required_capabilities:
                if not self._capability_policy.check(
                    self._agent_id,
                    cap,
                    tool_call.name,
                ):
                    if self._bus:
                        self._bus.publish(
                            EventType.CAPABILITY_DENIED,
                            {
                                "agent_id": self._agent_id,
                                "capability": cap,
                                "tool": tool_call.name,
                            },
                        )
                    return ToolResult(
                        tool_name=tool_call.name,
                        content=(
                            f"Capability '{cap}' denied for"
                            f" agent '{self._agent_id}'"
                            f" on tool '{tool_call.name}'."
                        ),
                        success=False,
                        metadata={"dispatched": False},
                    )

        # Taint checking (sink policy)
        taint_set = params.get("_taint") if isinstance(params, dict) else None
        if taint_set is not None:
            try:
                from openjarvis.security.taint import TaintSet, check_taint

                if isinstance(taint_set, TaintSet):
                    violation = check_taint(tool_call.name, taint_set)
                    if violation:
                        if self._bus:
                            self._bus.publish(
                                EventType.TAINT_VIOLATION,
                                {
                                    "tool": tool_call.name,
                                    "violation": violation,
                                },
                            )
                        return ToolResult(
                            tool_name=tool_call.name,
                            content=f"Taint violation: {violation}",
                            success=False,
                            metadata={"dispatched": False},
                        )
            except ImportError:
                pass
            # Remove internal taint key before passing to tool
            if isinstance(params, dict):
                params.pop("_taint", None)

        # Confirmation check for sensitive tools
        if tool.spec.requires_confirmation:
            if not self._interactive or self._confirm_callback is None:
                return ToolResult(
                    tool_name=tool_call.name,
                    content=(
                        f"Tool '{tool_call.name}' requires"
                        " confirmation but no confirmation"
                        " callback is available."
                    ),
                    success=False,
                    metadata={"dispatched": False},
                )
            prompt = f"Allow execution of tool '{tool_call.name}' with args {params}?"
            if not self._confirm_callback(prompt):
                return ToolResult(
                    tool_name=tool_call.name,
                    content=f"Tool '{tool_call.name}' execution denied by user.",
                    success=False,
                    metadata={"dispatched": False},
                )

        # Emit start event. ``agent`` carries the managed-agent UUID so the
        # AgentExecutor's trace subscriber (which filters by agent_id) can
        # actually match this event — without it, every tool call is silently
        # dropped from traces.
        observation_params = params
        observation_filter = getattr(tool, "observation_arguments", None)
        if callable(observation_filter):
            observation_params = observation_filter(params)
        if self._bus:
            self._bus.publish(
                EventType.TOOL_CALL_START,
                {
                    "tool": tool_call.name,
                    "arguments": observation_params,
                    "agent": self._agent_id,
                    "invocation_id": invocation_id,
                    "tool_call_id": tool_call.id,
                },
            )

        # Execute with timeout
        timeout = tool.spec.timeout_seconds or self._default_timeout
        t0 = time.time()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                # A raw pool thread starts with an empty context, which would
                # hide the caller's Agent worker lease from the tool (and with
                # it, cooperative cancellation of long MCP calls).
                context = contextvars.copy_context()

                started = [False]

                def invoke():
                    from openjarvis.agents._stubs import check_agent_cancelled

                    if not _IN_TOOL.get():
                        check_agent_cancelled()
                    # Dispatch boundary: from here on the tool may have acted.
                    started[0] = True
                    token = _IN_TOOL.set(True)
                    try:
                        return tool.execute(**params)
                    finally:
                        _IN_TOOL.reset(token)

                future = pool.submit(context.run, invoke)
                result = future.result(timeout=timeout)
        except asyncio.CancelledError:
            if not started[0]:
                # Pre-dispatch cancellation: the caller drops it as not run.
                raise
            result = ToolResult(
                tool_name=tool_call.name,
                content=(
                    f"Tool '{tool_call.name}' was interrupted after it started;"
                    " its outcome is unknown. Observe whether it happened"
                    " before retrying -- do not re-execute."
                ),
                success=False,
                metadata={"outcome_unknown": True},
            )
        except concurrent.futures.TimeoutError:
            if self._bus:
                self._bus.publish(
                    EventType.TOOL_TIMEOUT,
                    {"tool": tool_call.name, "timeout": timeout},
                )
            content = f"Tool '{tool_call.name}' timed out after {timeout:.0f}s."
            method = ""
            if isinstance(params, dict):
                method = str(params.get("method", "")).upper()
            state_changing = method in {"POST", "PUT", "PATCH", "DELETE"}
            # Model-facing wording keeps the explicit-mutation rule.
            may_have_mutated = bool(tool.spec.metadata.get("mutates") or state_changing)
            if may_have_mutated:
                # A mutation that outran its timeout may still have been
                # dispatched. That is `unknown`, not a retryable failure.
                content += (
                    " The mutation may already have been dispatched;"
                    " its outcome is unknown. Observe whether it happened"
                    " before retrying -- do not re-execute."
                )
            # Spec 2026-10-10 §5.1/§6: every tool not marked read_only is a
            # write, so a write that times out mid-call has an unknown outcome.
            outcome_unknown = (
                state_changing or tool.spec.metadata.get("read_only") is not True
            )
            result = ToolResult(
                tool_name=tool_call.name,
                content=content,
                success=False,
                metadata={"outcome_unknown": True} if outcome_unknown else {},
            )
        except Exception as exc:
            result = ToolResult(
                tool_name=tool_call.name,
                content=f"Tool execution error: {exc}",
                success=False,
            )
        latency = time.time() - t0
        result.latency_seconds = latency
        result.metadata["arguments"] = observation_params

        if result.success and isinstance(result.content, str):
            status = result.metadata.get("status_code")
            final_url = result.metadata.get("final_url")
            requested_url = params.get("url") if isinstance(params, dict) else None
            url = final_url if isinstance(final_url, str) else requested_url
            _evidence.record(
                tool_call.name,
                result.content,
                status if isinstance(status, int) else None,
                url if isinstance(url, str) else None,
            )

        # Auto-detect taints in results
        if result.success:
            try:
                from openjarvis.security.taint import auto_detect_taint

                detected = auto_detect_taint(result.content)
                if detected and detected.labels:
                    result.metadata["_taint"] = detected
            except ImportError:
                pass

        # Emit end event
        if self._bus:
            result_text = str(result.content) if result.content else ""
            # Pass through ToolResult.metadata so downstream consumers
            # (TraceCollector → TraceStep.metadata → SkillOptimizer) can
            # see skill-tagged invocations.  Filter to JSON-serializable
            # values only — internal objects like TaintSet (added by the
            # taint auto-detect above) must not leak to event subscribers
            # since the trace store will JSON-serialize them later.
            event_metadata = self._json_safe_metadata(result.metadata)
            self._bus.publish(
                EventType.TOOL_CALL_END,
                {
                    "tool": tool_call.name,
                    "success": result.success,
                    "invocation_id": invocation_id,
                    "tool_call_id": tool_call.id,
                    "latency": latency,
                    "result": result_text,
                    "metadata": event_metadata,
                    "agent": self._agent_id,
                },
            )

        return result

    @staticmethod
    def _json_safe_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Return a copy of *metadata* containing only JSON-serializable values.

        ``ToolExecutor`` annotates ``ToolResult.metadata`` with internal
        objects (currently ``_taint: TaintSet``).  Those are useful for
        in-process security checks but cannot be serialized when the
        ``TraceCollector`` writes ``TraceStep.metadata`` to JSON in the
        SQLite trace store.  This helper drops any keys whose value is
        not JSON-safe — silently, since the missing data is not
        load-bearing for downstream consumers.
        """
        if not metadata:
            return {}

        import json

        safe: Dict[str, Any] = {}
        for key, value in metadata.items():
            if not isinstance(key, str):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                # Skip non-serializable values (e.g. TaintSet)
                continue
            safe[key] = value
        return safe

    def available_tools(self) -> List[ToolSpec]:
        """Return specs for all available tools."""
        return [t.spec for t in self._tools.values()]

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Resolve an internal collaborator without exposing another model schema."""
        return self._tools.get(name)

    def get_openai_tools(self) -> List[Dict[str, Any]]:
        """Return tools in OpenAI function-calling format."""
        return [t.to_openai_function() for t in self._tools.values()]


def build_tool_descriptions(
    tools: List[BaseTool],
    *,
    include_category: bool = True,
    include_cost: bool = False,
) -> str:
    """Build rich text descriptions from a list of tools.

    This is the single source of truth for all text-based agents that need
    to describe available tools in their system prompts.

    Parameters
    ----------
    tools:
        List of tool instances.
    include_category:
        Whether to include the ``Category:`` line.
    include_cost:
        Whether to include ``Cost estimate:`` and ``Latency estimate:`` lines.

    Returns
    -------
    str
        Formatted multi-tool description, or ``"No tools available."`` if
        *tools* is empty.
    """
    if not tools:
        return "No tools available."

    from openjarvis.tools.description_loader import (
        get_tool_description_override,
    )

    sections: list[str] = []
    for t in tools:
        s = t.spec
        desc = get_tool_description_override(s.name) or s.description
        lines = [f"### {s.name}", desc]

        if include_category and s.category:
            lines.append(f"Category: {s.category}")

        if include_cost:
            if s.cost_estimate:
                lines.append(f"Cost estimate: ${s.cost_estimate:.4f}")
            if s.latency_estimate:
                lines.append(f"Latency estimate: {s.latency_estimate:.1f}s")

        # Parameter descriptions
        props = s.parameters.get("properties", {})
        required = set(s.parameters.get("required", []))
        if props:
            lines.append("Parameters:")
            for pname, pinfo in props.items():
                ptype = pinfo.get("type", "any")
                req_mark = ", required" if pname in required else ""
                desc = pinfo.get("description", "")
                if desc:
                    lines.append(f"  - {pname} ({ptype}{req_mark}): {desc}")
                else:
                    lines.append(f"  - {pname} ({ptype}{req_mark})")

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


__all__ = ["BaseTool", "ToolExecutor", "ToolSpec", "build_tool_descriptions"]
