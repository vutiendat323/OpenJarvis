"""Known-source synchronization never falls back to a browser."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.execution import DirectExecutionEngine, ExecutionTransport
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    NormalizedBatch,
    OperationContract,
    ReceiptStatus,
    ResourceRecord,
    SourceCapability,
    TransportKind,
    TrustState,
)


class _Adapter:
    def normalize(self, resource_type: str, payload: object) -> NormalizedBatch:
        return NormalizedBatch(
            source_id="fixture",
            resource_type=resource_type,
            records=(ResourceRecord(resource_id="item", payload={"id": "item"}),),
            synced_at="2026-08-20T00:00:00+00:00",
        )


class _Transport(ExecutionTransport):
    def request(self, **kwargs: object) -> object:
        return {"items": [{"id": "item"}]}


@pytest.fixture
def runtime(tmp_path):
    capabilities = SQLiteCapabilityStore(tmp_path / "structured.db")
    capabilities.save(
        SourceCapability(
            source_id="fixture",
            provider="fixture",
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
            validated_at="2026-08-20T00:00:00+00:00",
            expires_at="2030-08-20T00:00:00+00:00",
            revision=1,
        )
    )
    snapshots = StructuredSnapshotStore(tmp_path / "structured.db")
    direct = DirectExecutionEngine(
        capabilities,
        snapshots,
        adapters={"fixture": _Adapter()},
        transports={TransportKind.REST: _Transport()},
    )
    result = SimpleNamespace(
        capabilities=capabilities,
        snapshots=snapshots,
        direct=direct,
        browser=SimpleNamespace(observe=Mock()),
    )
    yield result
    direct.close()
    snapshots.close()
    capabilities.close()


def test_warm_sync_never_invokes_browser(runtime):
    receipt = runtime.direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.SUCCEEDED
    runtime.browser.observe.assert_not_called()
