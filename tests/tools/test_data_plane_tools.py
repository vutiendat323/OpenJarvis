"""Semantic Data Plane tool contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openjarvis.data_plane.types import (
    ConsistencyMode,
    DiscoveryResult,
    OperationContract,
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
        return SimpleNamespace(to_dict=lambda: {"status": "succeeded"})

    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "Task 7 source_execute must not call DirectExecutionEngine"
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
