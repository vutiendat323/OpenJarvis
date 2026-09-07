"""Narrow Agent-facing tools for the structured Data Plane."""

from __future__ import annotations

import json
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.data_plane.approval import ApprovalError, execution_request_hash
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.types import (
    ConsistencyMode,
    DiscoveryConstraints,
    ReceiptStatus,
    SourceRef,
    StructuredQuery,
    TrustState,
)
from openjarvis.system.bundles import DataPlaneRuntime
from openjarvis.tools._stubs import BaseTool, ToolSpec

# `refresh_if_stale` is documented to the Agent as "use it when the answer must
# be current", and the Agent is never told to pass `max_age_seconds`. A zero
# default made the mode a no-op on any non-empty store.
_DEFAULT_MAX_AGE_SECONDS = 60

_OBSERVES = {"observes": True}
_MUTATES = {"mutates": True}


class _DataPlaneTool(BaseTool):
    def __init__(self) -> None:
        self._runtime: DataPlaneRuntime | None = None
        self._discovery_budget_seconds = 60
        self._browser_fallback = False

    def _unavailable(self) -> ToolResult | None:
        if self._runtime is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=json.dumps({"error_code": "data_plane_unavailable"}),
            )
        return None

    def _result(
        self,
        payload: dict[str, object],
        *,
        success: bool = True,
        metadata: dict[str, object] | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=success,
            content=json.dumps(payload, sort_keys=True, separators=(",", ":")),
            metadata=metadata or {},
        )

    def _error(self, error: DataPlaneError) -> ToolResult:
        return self._result({"error_code": error.code.value}, success=False)


@ToolRegistry.register("source_discover")
class SourceDiscoverTool(_DataPlaneTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="source_discover",
            description=(
                "Discover a structured source and synchronize new validated reads."
            ),
            parameters={
                "type": "object",
                "properties": {"source_ref": {"type": "string"}},
                "required": ["source_ref"],
            },
            category="data_plane",
            required_capabilities=["source:discover"],
            metadata=dict(_OBSERVES),
            timeout_seconds=65.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._unavailable()
        if unavailable is not None:
            return unavailable
        source_ref = str(params.get("source_ref", "")).strip()
        if not source_ref:
            return self._result({"error_code": "source_ref_required"}, success=False)
        try:
            discovered = self._runtime.discovery.discover(
                SourceRef(source_ref),
                DiscoveryConstraints(
                    budget_seconds=self._discovery_budget_seconds,
                    browser_fallback=self._browser_fallback,
                ),
            )
            payload: dict[str, object] = {"discovery": discovered.to_dict()}
            capability = discovered.capability
            if (
                not discovered.cache_hit
                and discovered.state is TrustState.READ_VALIDATED
                and capability is not None
            ):
                resources = self._runtime.direct.syncable_resources(
                    capability.source_id
                )
                if resources:
                    receipt = self._runtime.direct.sync(capability.source_id, resources)
                    payload["sync"] = receipt.to_dict()
                    if receipt.status is not ReceiptStatus.SUCCEEDED:
                        return self._result(payload, success=False)
            return self._result(payload)
        except DataPlaneError as error:
            return self._error(error)


@ToolRegistry.register("source_sync")
class SourceSyncTool(_DataPlaneTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="source_sync",
            description=(
                "Synchronize validated structured resources. Use the canonical "
                "source_id returned by source_discover; a capability_missing "
                "response includes valid source ids."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "resources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["source_id", "resources"],
            },
            category="data_plane",
            required_capabilities=["source:read"],
            metadata=dict(_OBSERVES),
            timeout_seconds=35.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._unavailable()
        if unavailable is not None:
            return unavailable
        source_id = str(params.get("source_id", "")).strip()
        resources = params.get("resources", [])
        if (
            not source_id
            or not isinstance(resources, list)
            or not all(isinstance(resource, str) and resource for resource in resources)
        ):
            return self._result(
                {"error_code": "source_sync_arguments_invalid"}, success=False
            )
        try:
            receipt = self._runtime.direct.sync(source_id, resources)
            return self._result(
                {"sync": receipt.to_dict()},
                success=receipt.status is ReceiptStatus.SUCCEEDED,
            )
        except DataPlaneError as error:
            if error.code is DataPlaneErrorCode.CAPABILITY_MISSING:
                capabilities = getattr(self._runtime, "capabilities", None)
                list_ids = getattr(capabilities, "list_source_ids", None)
                valid_source_ids = list_ids() if callable(list_ids) else []
                return self._result(
                    {
                        "error_code": error.code.value,
                        "valid_source_ids": valid_source_ids,
                    },
                    success=False,
                )
            return self._error(error)


@ToolRegistry.register("structured_query")
class StructuredQueryTool(_DataPlaneTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="structured_query",
            description="Query local structured snapshots with explicit consistency.",
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "resource_type": {"type": "string"},
                    "filters": {"type": "object"},
                    "consistency": {"type": "string"},
                    "max_age_seconds": {
                        "type": "integer",
                        "description": (
                            "How old a snapshot may be, in seconds, before "
                            "consistency='refresh_if_stale' re-syncs it. "
                            f"Defaults to {_DEFAULT_MAX_AGE_SECONDS}; 0 disables "
                            "the age check entirely."
                        ),
                    },
                    "limit": {"type": "integer"},
                },
                "required": ["source_id", "resource_type"],
            },
            category="data_plane",
            required_capabilities=["source:read"],
            metadata=dict(_OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._unavailable()
        if unavailable is not None:
            return unavailable
        try:
            query = StructuredQuery(
                source_id=str(params.get("source_id", "")).strip(),
                resource_type=str(params.get("resource_type", "")).strip(),
                filters=dict(params.get("filters", {})),
                consistency=ConsistencyMode(str(params.get("consistency", "cached"))),
                max_age_seconds=int(
                    params.get("max_age_seconds", _DEFAULT_MAX_AGE_SECONDS)
                ),
                limit=int(params.get("limit", 100)),
            )
        except (TypeError, ValueError):
            return self._result(
                {"error_code": "structured_query_arguments_invalid"}, success=False
            )
        if not query.source_id or not query.resource_type:
            return self._result(
                {"error_code": "structured_query_arguments_invalid"}, success=False
            )

        try:
            if query.consistency is ConsistencyMode.LIVE:
                receipt = self._runtime.direct.sync(
                    query.source_id, [query.resource_type]
                )
                if receipt.status is not ReceiptStatus.SUCCEEDED:
                    return self._result({"sync": receipt.to_dict()}, success=False)
            result = self._runtime.snapshots.query(query)
            if query.consistency is ConsistencyMode.REFRESH_IF_STALE and (
                result.stale or result.version == 0
            ):
                receipt = self._runtime.direct.sync(
                    query.source_id, [query.resource_type]
                )
                if receipt.status is not ReceiptStatus.SUCCEEDED:
                    return self._result({"sync": receipt.to_dict()}, success=False)
                result = self._runtime.snapshots.query(query)
            return self._result({"result": result.to_dict()})
        except DataPlaneError as error:
            return self._error(error)


@ToolRegistry.register("source_execute")
class SourceExecuteTool(_DataPlaneTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="source_execute",
            description="Execute one write-validated provider operation.",
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "operation": {"type": "string"},
                    "arguments": {"type": "object"},
                    "approval_id": {"type": "string"},
                },
                "required": ["source_id", "operation", "arguments"],
            },
            category="data_plane",
            required_capabilities=["source:write"],
            # Worst case dispatch is one 10s request, one capped Retry-After
            # sleep and one retry; the default 30s could cut a live mutation
            # off mid-flight and report it as a plain failure.
            timeout_seconds=45.0,
            metadata=dict(_MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._unavailable()
        if unavailable is not None:
            return unavailable
        source_id = str(params.get("source_id", "")).strip()
        operation_name = str(params.get("operation", "")).strip()
        arguments = params.get("arguments")
        approval_id = str(params.get("approval_id", "")).strip()
        if not source_id or not operation_name or not isinstance(arguments, dict):
            return self._result(
                {"error_code": "source_execute_arguments_invalid"}, success=False
            )
        capability = self._runtime.discovery.get_capability(source_id)
        operation = capability.operations.get(operation_name) if capability else None
        if (
            capability is None
            or operation is None
            or operation.safe
            or operation.trust is not TrustState.WRITE_VALIDATED
        ):
            return self._result(
                {"error_code": DataPlaneErrorCode.CAPABILITY_QUARANTINED.value},
                success=False,
            )
        if operation_name == "payment.initiate":
            if (
                set(arguments) != {"order", "paymentMethod"}
                or not isinstance(arguments.get("order"), str)
                or not str(arguments["order"]).strip()
                or arguments.get("paymentMethod") != "bank-transfer"
            ):
                return self._result(
                    {"error_code": "payment_arguments_invalid"}, success=False
                )
            if not self._verified_order_exists(source_id, arguments):
                return self._result({"error_code": "order_not_verified"}, success=False)
        gate = self._runtime.approval_gate
        if gate is None:
            return self._result({"error_code": "approval_unavailable"}, success=False)
        try:
            request_hash = execution_request_hash(
                source_id, operation_name, arguments, capability.revision
            )
            if not approval_id and getattr(gate, "conversational", False):
                # Nobody is watching an approval queue in a kiosk. Only the
                # wait is skipped -- the grant stays bound to this exact
                # request hash and capability revision.
                grant = gate.prepare_and_authorize(
                    source_id, operation_name, arguments, capability.revision
                )
            elif not approval_id:
                pending = gate.prepare(
                    source_id, operation_name, arguments, capability.revision
                )
                metadata = {
                    "pending_approval": True,
                    "approval_id": pending.id,
                    "request_hash": request_hash,
                }
                return self._result(
                    {"error_code": "approval_required"},
                    success=False,
                    metadata=metadata,
                )
            else:
                grant = gate.authorize(approval_id, request_hash)
            receipt = self._runtime.direct.execute(
                source_id, operation_name, arguments, grant=grant
            )
            return self._result(
                {"receipt": receipt.to_dict()},
                success=receipt.status is ReceiptStatus.SUCCEEDED,
            )
        except ApprovalError as error:
            return self._result({"error_code": error.code}, success=False)
        except DataPlaneError as error:
            return self._error(error)

    def _verified_order_exists(
        self, source_id: str, arguments: dict[str, object]
    ) -> bool:
        order_id = arguments.get("order")
        if not isinstance(order_id, str) or not order_id:
            return False
        result = self._runtime.snapshots.query(
            StructuredQuery(
                source_id=source_id,
                resource_type="order",
                filters={"slug": order_id},
                limit=0,
            )
        )
        return any(item.resource_id == order_id for item in result.items)


@ToolRegistry.register("source_verify")
class SourceVerifyTool(_DataPlaneTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="source_verify",
            description="Observe the outcome of a prior source execution receipt.",
            parameters={
                "type": "object",
                "properties": {"receipt_id": {"type": "string"}},
                "required": ["receipt_id"],
            },
            category="data_plane",
            required_capabilities=["source:read"],
            metadata=dict(_OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._unavailable()
        if unavailable is not None:
            return unavailable
        receipt_id = str(params.get("receipt_id", "")).strip()
        if not receipt_id:
            return self._result({"error_code": "receipt_id_required"}, success=False)
        try:
            return self._result(
                {"verification": self._runtime.direct.verify(receipt_id).to_dict()}
            )
        except DataPlaneError as error:
            return self._error(error)
