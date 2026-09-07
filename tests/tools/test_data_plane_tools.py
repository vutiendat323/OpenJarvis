"""Semantic Data Plane tool contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.types import (
    ConsistencyMode,
    DiscoveryResult,
    OperationContract,
    ReceiptStatus,
    SourceCapability,
    StructuredResult,
    TransportKind,
    TrustState,
)
from openjarvis.tools.data_plane import (
    SourceDiscoverTool,
    SourceExecuteTool,
    SourceSyncTool,
    SourceVerifyTool,
    StructuredQueryTool,
)


def test_data_plane_tool_capabilities_and_kinds() -> None:
    """A metadata regression must not let ToolExecutor misclassify a tool."""
    assert SourceDiscoverTool().spec.required_capabilities == ["source:discover"]
    assert SourceSyncTool().spec.metadata == {"observes": True}
    assert StructuredQueryTool().spec.metadata == {"observes": True}
    assert SourceExecuteTool().spec.metadata == {"mutates": True}
    assert SourceVerifyTool().spec.metadata == {"observes": True}


class _DirectWithoutExecution:
    def __init__(self) -> None:
        self.sync_requests: list[tuple[str, list[str]]] = []

    def sync(self, source_id: str, resources: list[str]) -> SimpleNamespace:
        self.sync_requests.append((source_id, resources))
        return SimpleNamespace(
            status=ReceiptStatus.SUCCEEDED,
            to_dict=lambda: {"status": "succeeded"},
        )

    def syncable_resources(self, source_id: str) -> list[str]:
        return ["branch"]

    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "Task 7 source_execute must not call DirectExecutionEngine"
        )


class _FailedDirect(_DirectWithoutExecution):
    def sync(self, source_id: str, resources: list[str]) -> SimpleNamespace:
        self.sync_requests.append((source_id, resources))
        return SimpleNamespace(
            status=ReceiptStatus.FAILED,
            to_dict=lambda: {
                "status": "failed",
                "error_code": "provider_unavailable",
            },
        )


class _MissingSourceDirect(_DirectWithoutExecution):
    def sync(self, source_id: str, resources: list[str]) -> SimpleNamespace:
        raise DataPlaneError(
            DataPlaneErrorCode.CAPABILITY_MISSING,
            "unknown source",
        )


def _runtime_for(capability: SourceCapability) -> SimpleNamespace:
    direct = _DirectWithoutExecution()
    return SimpleNamespace(
        discovery=SimpleNamespace(get_capability=lambda source_id: capability),
        direct=direct,
        snapshots=SimpleNamespace(
            query=lambda query: StructuredResult(
                items=(), version=0, synced_at="", stale=False
            )
        ),
    )


def test_source_execute_rejects_unsafe_contract_without_direct_dispatch() -> None:
    """Removing Task 7's write gate would call the double and fail this test."""
    capability = SourceCapability(
        source_id="trend-coffee",
        provider="trendcoffee",
        origin="https://trendcoffee.net",
        base_url="https://trendcoffee.net/api/latest",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={
            "order.place": OperationContract(
                "order.place",
                "POST",
                "/orders/public",
                "order",
                TrustState.QUARANTINED,
                False,
            )
        },
        fingerprint="sha256:fixture",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="",
        expires_at="",
        revision=1,
    )
    tool = SourceExecuteTool()
    runtime = _runtime_for(capability)
    tool._runtime = runtime

    result = tool.execute(
        source_id="trend-coffee", operation="order.place", arguments={}
    )

    assert result.success is False
    assert json.loads(result.content) == {"error_code": "capability_quarantined"}
    assert runtime.direct.sync_requests == []


def test_source_sync_explains_canonical_ids_when_source_is_unknown() -> None:
    capability = SourceCapability(
        source_id="trend-coffee",
        provider="trendcoffee",
        origin="https://trendcoffee.net",
        base_url="https://trendcoffee.net/api/latest",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={},
        fingerprint="sha256:fixture",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="",
        expires_at="",
        revision=1,
    )
    runtime = _runtime_for(capability)
    runtime.direct = _MissingSourceDirect()
    runtime.capabilities = SimpleNamespace(list_source_ids=lambda: ["trend-coffee"])
    tool = SourceSyncTool()
    tool._runtime = runtime

    result = tool.execute(
        source_id="trendcoffee.net", resources=["menu_item"]
    )

    assert result.success is False
    assert json.loads(result.content) == {
        "error_code": "capability_missing",
        "valid_source_ids": ["trend-coffee"],
    }


def test_structured_query_only_synchronizes_live_or_stale_requests() -> None:
    """The cached query path must remain local and free of a transport call."""
    capability = SourceCapability(
        source_id="fixture",
        provider="generic",
        origin="https://fixture.test",
        base_url="https://fixture.test/api",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={},
        fingerprint="sha256:fixture",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="",
        expires_at="",
        revision=1,
    )
    tool = StructuredQueryTool()
    runtime = _runtime_for(capability)
    tool._runtime = runtime

    cached = tool.execute(source_id="fixture", resource_type="branch")
    live = tool.execute(
        source_id="fixture",
        resource_type="branch",
        consistency=ConsistencyMode.LIVE.value,
    )

    assert cached.success is True
    assert live.success is True
    assert runtime.direct.sync_requests == [("fixture", ["branch"])]


def test_source_discover_syncs_new_reads_but_not_a_cache_hit() -> None:
    """The cache branch must not restart static/browser discovery or a sync."""
    capability = SourceCapability(
        source_id="fixture",
        provider="generic",
        origin="https://fixture.test",
        base_url="https://fixture.test/api",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={
            "branch.list": OperationContract(
                "branch.list",
                "GET",
                "/branch",
                "branch",
                TrustState.READ_VALIDATED,
                True,
            )
        },
        fingerprint="sha256:fixture",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="",
        expires_at="",
        revision=1,
    )
    runtime = _runtime_for(capability)
    tool = SourceDiscoverTool()
    tool._runtime = runtime
    runtime.discovery.discover = lambda *_args: DiscoveryResult(
        source_id="fixture",
        state=TrustState.READ_VALIDATED,
        capability=capability,
    )

    discovered = tool.execute(source_ref="https://fixture.test")
    runtime.discovery.discover = lambda *_args: DiscoveryResult(
        source_id="fixture",
        state=TrustState.READ_VALIDATED,
        capability=capability,
        cache_hit=True,
    )
    cached = tool.execute(source_ref="https://fixture.test")

    assert discovered.success is True
    assert cached.success is True
    assert runtime.direct.sync_requests == [("fixture", ["branch"])]


def test_sync_receipt_failure_is_not_reported_as_fresh_success() -> None:
    """Dropping receipt status checks would falsely report a completed sync."""
    capability = SourceCapability(
        source_id="fixture",
        provider="generic",
        origin="https://fixture.test",
        base_url="https://fixture.test/api",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={
            "branch.list": OperationContract(
                "branch.list",
                "GET",
                "/branch",
                "branch",
                TrustState.READ_VALIDATED,
                True,
            )
        },
        fingerprint="sha256:fixture",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="",
        expires_at="",
        revision=1,
    )
    runtime = _runtime_for(capability)
    runtime.direct = _FailedDirect()
    runtime.discovery.discover = lambda *_args: DiscoveryResult(
        source_id="fixture",
        state=TrustState.READ_VALIDATED,
        capability=capability,
    )
    discover = SourceDiscoverTool()
    sync = SourceSyncTool()
    query = StructuredQueryTool()
    for tool in (discover, sync, query):
        tool._runtime = runtime

    discovered = discover.execute(source_ref="https://fixture.test")
    synchronized = sync.execute(source_id="fixture", resources=["branch"])
    live = query.execute(
        source_id="fixture", resource_type="branch", consistency="live"
    )
    runtime.snapshots.query = lambda query: StructuredResult(
        items=(), version=1, synced_at="2026-08-20T00:00:00+00:00", stale=True
    )
    refreshed = query.execute(
        source_id="fixture",
        resource_type="branch",
        consistency="refresh_if_stale",
    )

    assert discovered.success is False
    assert synchronized.success is False
    assert live.success is False
    assert refreshed.success is False
    assert all(
        json.loads(result.content)["sync"]["status"] == "failed"
        for result in (discovered, synchronized, live, refreshed)
    )
