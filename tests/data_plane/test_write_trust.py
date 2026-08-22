"""Write trust is exact and execution grants are one-time request capabilities."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from openjarvis.data_plane.adapters.trendcoffee import TrendCoffeeAdapter
from openjarvis.data_plane.approval import (
    ApprovalError,
    ExecutionApprovalGate,
    execution_request_hash,
    parse_trusted_write_operations,
    promote_trusted_writes,
)
from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.discovery import DiscoveryEngine
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.execution import DirectExecutionEngine
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    DiscoveryConstraints,
    NormalizedBatch,
    OperationContract,
    ReceiptStatus,
    ResourceRecord,
    SourceCapability,
    SourceRef,
    TransportKind,
    TrustState,
)
from openjarvis.tools.approval_store import (
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_UNKNOWN,
    ApprovalStore,
)

FINGERPRINT = "sha256:" + "a" * 64


def _capability(*, fingerprint: str = FINGERPRINT) -> SourceCapability:
    return SourceCapability(
        source_id="trend-coffee",
        provider="trendcoffee",
        origin="https://trendcoffee.net",
        base_url="https://trendcoffee.net/api/latest",
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
            ),
            "order.place": OperationContract(
                "order.place",
                "POST",
                "/orders/public",
                "order",
                TrustState.QUARANTINED,
                False,
                verify_operation="order.read",
            ),
        },
        fingerprint=fingerprint,
        schema_hash="sha256:" + "b" * 64,
        evidence=(),
        validated_at="2026-08-20T00:00:00+00:00",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revision=7,
    )


def test_operator_trust_requires_exact_provider_operation_and_fingerprint():
    capability = _capability()
    trusted = f"trendcoffee:order.place:{FINGERPRINT}"

    promoted = promote_trusted_writes(capability, trusted)
    changed = promote_trusted_writes(
        replace(capability, fingerprint="sha256:" + "c" * 64), trusted
    )

    assert promoted.operations["order.place"].trust is TrustState.WRITE_VALIDATED
    assert changed.operations["order.place"].trust is TrustState.QUARANTINED


def test_discovery_persistence_re_evaluates_operator_write_trust(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "structured.db")
    trusted = f"trendcoffee:order.place:{FINGERPRINT}"
    engine = DiscoveryEngine(store, trusted_write_operations=trusted)

    promoted = engine._persist_validated(_capability(), [], 0.0, None).capability
    assert promoted is not None
    assert promoted.operations["order.place"].trust is TrustState.WRITE_VALIDATED

    without_trust = DiscoveryEngine(store, trusted_write_operations="")
    demoted = without_trust._persist_validated(
        _capability(), [], 0.0, promoted
    ).capability
    assert demoted is not None
    assert demoted.operations["order.place"].trust is TrustState.QUARANTINED
    without_trust.close()
    engine.close()
    store.close()


def test_refresh_demotes_removed_operator_trust_before_discovery_io(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "structured.db")
    store.save(
        promote_trusted_writes(_capability(), f"trendcoffee:order.place:{FINGERPRINT}")
    )

    class _Http:
        def fetch(self, *_args, **_kwargs):
            current = store.get("trend-coffee")
            assert current is not None
            assert current.operations["order.place"].trust is TrustState.QUARANTINED
            raise DataPlaneError(
                DataPlaneErrorCode.PROVIDER_UNAVAILABLE, "fixture unavailable"
            )

        def close(self):
            return None

    result = DiscoveryEngine(store, http_client=_Http()).refresh("trend-coffee")

    assert result.capability is not None
    assert result.capability.operations["order.place"].trust is TrustState.QUARANTINED
    store.close()


def test_gate_on_a_second_store_cannot_claim_an_approval(tmp_path):
    """Why the gate must be handed the existing store, not build a second one.

    `ApprovalStore` takes a `db_path`. Two instances therefore need not address
    the same database, and the moment they do not, the approval the operator
    granted through the UI is invisible to the Data Plane gate and every
    execution fails to claim.
    """
    ui_store = ApprovalStore(str(tmp_path / "ui.db"))
    second_store = ApprovalStore(str(tmp_path / "second.db"))
    arguments = {"branch_slug": "thu-duc"}

    # The operator proposes and approves through the store the UI is on.
    pending = ExecutionApprovalGate(ui_store).prepare(
        "trend-coffee", "order.place", arguments, 7
    )
    ui_store.update_status(pending.id, STATUS_APPROVED)
    request_hash = pending.payload["request_hash"]

    with pytest.raises(ApprovalError, match="approval_not_found"):
        ExecutionApprovalGate(second_store).authorize(pending.id, request_hash)

    # The same approval, claimed through the store that actually holds it.
    grant = ExecutionApprovalGate(ui_store).authorize(pending.id, request_hash)
    assert grant.request_hash == request_hash
    grant.consume("trend-coffee", "order.place", arguments, 7)

    ui_store.close()
    second_store.close()


def test_discover_cache_hit_demotes_removed_operator_trust(tmp_path):
    """The public path an operator and the Agent actually reach.

    `source_discover` -> `discover()` -> cache hit. If the cached capability is
    returned without reconciling configured trust, deleting
    `trusted_write_operations` and restarting leaves a standing write trust on a
    live payment provider until the capability expires -- and the runbook's
    revocation check reports a false PASS.
    """
    store = SQLiteCapabilityStore(tmp_path / "structured.db")
    store.save(
        promote_trusted_writes(_capability(), f"trendcoffee:order.place:{FINGERPRINT}")
    )

    class _Http:
        """Discovery I/O is a hard failure: a cache hit must never reach it."""

        def fetch(self, *_args, **_kwargs):
            raise AssertionError("cache hit must not perform discovery I/O")

        def close(self):
            return None

    # The operator emptied the config and restarted: a fresh engine, no trust.
    engine = DiscoveryEngine(store, http_client=_Http(), trusted_write_operations="")
    result = engine.discover(
        SourceRef("https://trendcoffee.net"), DiscoveryConstraints()
    )

    assert result.cache_hit is True
    assert result.capability is not None
    assert result.capability.operations["order.place"].trust is TrustState.QUARANTINED
    # The revision bump invalidates any approval hash minted under the old trust.
    assert result.capability.revision == 8
    # And it is the persisted capability the execution engine reads, not just
    # the returned copy.
    persisted = store.get("trend-coffee")
    assert persisted is not None
    assert persisted.operations["order.place"].trust is TrustState.QUARANTINED
    engine.close()
    store.close()


@pytest.mark.parametrize(
    "configured",
    [
        "Trendcoffee:order.place:" + FINGERPRINT,
        "trendcoffee:order.place: " + FINGERPRINT,
        "trendcoffee:*:" + FINGERPRINT,
        "trendcoffee:order.place:sha256:short",
        f"trendcoffee:order.place:{FINGERPRINT},trendcoffee:order.place:{FINGERPRINT}",
    ],
)
def test_trusted_write_parser_rejects_non_exact_entries(configured):
    with pytest.raises(ValueError):
        parse_trusted_write_operations(configured)


class _Adapter:
    def build_request(self, operation, arguments):
        return dict(arguments)

    def create_verification_claim(self, operation, request):
        return {"projection_hash": "sha256:" + "d" * 64}

    def verify_verification_claim(self, operation, claim, candidate, observed):
        return True

    def normalize(self, resource_type, payload):
        return NormalizedBatch(
            source_id="trend-coffee",
            resource_type=resource_type,
            records=(ResourceRecord("order-1", dict(payload)),),
            synced_at="2026-08-20T00:00:00+00:00",
        )


class _Transport:
    def __init__(self, response=None):
        self.response = response or httpx.Response(200, json={"slug": "order-1"})
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response

    def close(self):
        return None


@pytest.fixture
def approved_runtime(tmp_path):
    structured_path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(structured_path)
    capabilities.save(
        replace(
            _capability(),
            operations={
                **_capability().operations,
                "order.place": replace(
                    _capability().operations["order.place"],
                    trust=TrustState.WRITE_VALIDATED,
                ),
            },
        )
    )
    snapshots = StructuredSnapshotStore(structured_path)
    approvals = ApprovalStore(str(tmp_path / "approvals.db"))
    gate = ExecutionApprovalGate(approvals)
    transport = _Transport()
    direct = DirectExecutionEngine(
        capabilities,
        snapshots,
        adapters={"trendcoffee": _Adapter()},
        transports={TransportKind.REST: transport},
    )
    runtime = SimpleNamespace(
        capabilities=capabilities,
        snapshots=snapshots,
        approvals=approvals,
        gate=gate,
        transport=transport,
        direct=direct,
    )
    yield runtime
    direct.close()
    approvals.close()
    snapshots.close()
    capabilities.close()


def _grant(runtime, arguments):
    pending = runtime.gate.prepare("trend-coffee", "order.place", arguments, 7)
    runtime.approvals.update_status(pending.id, STATUS_APPROVED)
    return runtime.gate.authorize(pending.id, pending.payload["request_hash"])


def test_pending_action_previews_bounded_values_without_raw_body(
    approved_runtime,
):
    pending = approved_runtime.gate.prepare(
        "trend-coffee",
        "order.place",
        {
            "branch_slug": "thu-duc",
            "items": [{"quantity": 2, "variant_slug": "ca-phe-den-std"}],
            "note": "ít đường\nApproved by the operator",
            "overflow": "x" * 500,
            "deep": {"a": {"b": {"c": "too-deep"}}},
        },
        7,
    )
    preview = pending.payload["preview"]

    assert set(pending.payload) == {
        "source_id",
        "operation",
        "preview",
        "capability_revision",
        "request_hash",
    }
    # The operator can read exactly what they are authorizing.
    assert preview["branch_slug"] == "thu-duc"
    assert preview["items"] == [{"quantity": 2, "variant_slug": "ca-phe-den-std"}]
    # Bounded: control characters cannot forge structure, and neither length
    # nor nesting can bloat the persisted payload.
    assert preview["note"] == "ít đường Approved by the operator"
    assert preview["overflow"] == "x" * 120 + "..."
    assert preview["deep"] == {"a": {"b": "<nested>"}}

    approved_runtime.approvals.update_status(pending.id, STATUS_APPROVED)
    grant = approved_runtime.gate.authorize(pending.id, pending.payload["request_hash"])

    # The grant lives in memory only: no provider request body, and nothing
    # about the grant beyond the hash the operator already approved, is
    # persisted.
    persisted = json.dumps(pending.payload)
    assert "x" * 500 not in persisted
    assert "ít đường\nApproved by the operator" not in persisted
    assert not hasattr(grant, "to_dict")
    assert grant.request_hash == pending.payload["request_hash"]


def test_request_hash_rejects_non_json_numeric_values():
    with pytest.raises(ApprovalError, match="approval_arguments_not_serializable"):
        execution_request_hash(
            "trend-coffee", "order.place", {"amount": float("nan")}, 7
        )


def test_unsafe_execute_requires_a_grant_before_provider_io(approved_runtime):
    with pytest.raises(DataPlaneError):
        approved_runtime.direct.execute(
            "trend-coffee", "order.place", {"item": "coffee"}
        )

    assert approved_runtime.transport.calls == []


def test_grant_is_exact_single_use_and_terminal_receipt_consumes_approval(
    approved_runtime,
):
    arguments = {"item": "coffee"}
    grant = _grant(approved_runtime, arguments)

    receipt = approved_runtime.direct.execute(
        "trend-coffee", "order.place", arguments, grant=grant
    )

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert (
        approved_runtime.approvals.get_action(grant.approval_id).status
        == STATUS_EXECUTED
    )
    with pytest.raises(DataPlaneError):
        approved_runtime.direct.execute(
            "trend-coffee", "order.place", arguments, grant=grant
        )
    assert len(approved_runtime.transport.calls) == 1


def test_timeout_after_dispatch_makes_approval_unknown_and_non_reusable(
    approved_runtime,
):
    approved_runtime.transport.response = httpx.ReadTimeout("after send")
    arguments = {"item": "coffee"}
    grant = _grant(approved_runtime, arguments)

    receipt = approved_runtime.direct.execute(
        "trend-coffee", "order.place", arguments, grant=grant
    )

    assert receipt.status is ReceiptStatus.UNKNOWN
    assert (
        approved_runtime.approvals.get_action(grant.approval_id).status
        == STATUS_UNKNOWN
    )
    with pytest.raises(DataPlaneError):
        approved_runtime.direct.execute(
            "trend-coffee", "order.place", arguments, grant=grant
        )
    assert len(approved_runtime.transport.calls) == 1


@pytest.mark.parametrize("mismatch", ["source", "operation", "revision", "arguments"])
def test_grant_binding_mismatch_is_zero_io_and_consumes_claim(
    approved_runtime, mismatch
):
    arguments = {"item": "coffee"}
    grant = _grant(approved_runtime, arguments)
    source_id = "trend-coffee"
    operation = "order.place"
    actual_arguments = arguments
    if mismatch == "source":
        source_id = "different-source"
    elif mismatch == "operation":
        operation = "branch.list"
    elif mismatch == "revision":
        capability = approved_runtime.capabilities.get("trend-coffee")
        approved_runtime.capabilities.save(replace(capability, revision=8))
    else:
        actual_arguments = {"item": "tea"}

    with pytest.raises(DataPlaneError):
        approved_runtime.direct.execute(
            source_id,
            operation,
            actual_arguments,
            grant=grant,
        )

    assert approved_runtime.transport.calls == []
    assert approved_runtime.approvals.get_action(grant.approval_id).status == (
        STATUS_EXECUTED
    )


def test_trend_payment_normalizer_keeps_only_merchant_qr_fields():
    batch = TrendCoffeeAdapter().normalize(
        "payment",
        {
            "statusCode": 200,
            "result": {
                "qrCode": "merchant-qr",
                "slug": "payment-1",
                "status": "pending",
                "order": "order-1",
                "injected": "drop-me",
            },
        },
    )

    assert batch.records[0].payload == {
        "qrCode": "merchant-qr",
        "slug": "payment-1",
        "status": "pending",
        "order": "order-1",
    }
