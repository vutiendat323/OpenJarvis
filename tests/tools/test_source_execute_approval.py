"""The semantic mutation tool pauses and resumes through exact approval."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from openjarvis.data_plane.approval import ExecutionApprovalGate
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    ExecutionReceipt,
    NormalizedBatch,
    OperationContract,
    ReceiptStatus,
    ResourceRecord,
    SourceCapability,
    TransportKind,
    TrustState,
)
from openjarvis.tools.approval_store import STATUS_APPROVED, ApprovalStore
from openjarvis.tools.data_plane import SourceExecuteTool


def _capability() -> SourceCapability:
    return SourceCapability(
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
                TrustState.WRITE_VALIDATED,
                False,
            ),
            "payment.initiate": OperationContract(
                "payment.initiate",
                "POST",
                "/payment/initiate/public",
                "payment",
                TrustState.WRITE_VALIDATED,
                False,
            ),
        },
        fingerprint="sha256:" + "a" * 64,
        schema_hash="sha256:" + "b" * 64,
        evidence=(),
        validated_at="2026-08-20T00:00:00+00:00",
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revision=3,
    )


class _Direct:
    def __init__(self):
        self.calls = []

    def execute(self, source_id, operation, arguments, *, grant=None):
        self.calls.append((source_id, operation, arguments, grant))
        return ExecutionReceipt(
            receipt_id="receipt-1",
            source_id=source_id,
            operation=operation,
            capability_revision=3,
            request_hash=grant.request_hash,
            status=ReceiptStatus.SUCCEEDED,
            created_at="2026-08-20T00:00:00+00:00",
        )


def _tool(tmp_path, *, verified_orders=()):
    """Wire the tool over a real snapshot store, so the payment precondition is
    exercised against the same query semantics production uses."""
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    gate = ExecutionApprovalGate(store)
    direct = _Direct()
    capability = _capability()
    snapshots = StructuredSnapshotStore(tmp_path / "structured.db")
    if verified_orders:
        snapshots.upsert(
            NormalizedBatch(
                source_id="trend-coffee",
                resource_type="order",
                records=tuple(
                    ResourceRecord(slug, {"slug": slug, "status": "confirmed"})
                    for slug in verified_orders
                ),
                synced_at="2026-08-20T00:00:00+00:00",
            )
        )
    runtime = SimpleNamespace(
        discovery=SimpleNamespace(get_capability=lambda source_id: capability),
        direct=direct,
        approval_gate=gate,
        snapshots=snapshots,
    )
    tool = SourceExecuteTool()
    tool._runtime = runtime
    return tool, store, direct, snapshots


def test_source_execute_queues_then_consumes_exact_approval(tmp_path):
    tool, store, direct, snapshots = _tool(tmp_path)
    arguments = {"order_type": "take-out", "branch_slug": "b1", "items": []}

    pending = tool.execute(
        source_id="trend-coffee", operation="order.place", arguments=arguments
    )
    approval_id = pending.metadata["approval_id"]
    store.update_status(approval_id, STATUS_APPROVED)
    done = tool.execute(
        source_id="trend-coffee",
        operation="order.place",
        arguments=arguments,
        approval_id=approval_id,
    )

    assert pending.success is False
    assert pending.metadata["pending_approval"] is True
    assert done.success is True
    assert json.loads(done.content)["receipt"]["receipt_id"] == "receipt-1"
    assert len(direct.calls) == 1
    store.close()
    snapshots.close()


def test_changed_argument_rejects_approval_without_direct_execution(tmp_path):
    tool, store, direct, snapshots = _tool(tmp_path)
    original = {"order_type": "take-out", "branch_slug": "b1", "items": []}
    pending = tool.execute(
        source_id="trend-coffee", operation="order.place", arguments=original
    )
    approval_id = pending.metadata["approval_id"]
    store.update_status(approval_id, STATUS_APPROVED)

    rejected = tool.execute(
        source_id="trend-coffee",
        operation="order.place",
        arguments={**original, "branch_slug": "other"},
        approval_id=approval_id,
    )

    assert rejected.success is False
    assert json.loads(rejected.content) == {
        "error_code": "approval_request_hash_mismatch"
    }
    assert direct.calls == []
    assert store.get_action(approval_id).status == STATUS_APPROVED
    store.close()
    snapshots.close()


def test_payment_requires_verified_order_before_approval_is_prepared(tmp_path):
    tool, store, direct, snapshots = _tool(tmp_path)

    rejected = tool.execute(
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments={"order": "order-1", "paymentMethod": "bank-transfer"},
    )

    assert rejected.success is False
    assert json.loads(rejected.content) == {"error_code": "order_not_verified"}
    assert store.list_pending() == []
    assert direct.calls == []
    store.close()
    snapshots.close()


def test_payment_for_another_order_than_the_verified_one_is_rejected(tmp_path):
    tool, store, direct, snapshots = _tool(tmp_path, verified_orders=("order-1",))

    rejected = tool.execute(
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments={"order": "order-2", "paymentMethod": "bank-transfer"},
    )

    assert rejected.success is False
    assert json.loads(rejected.content) == {"error_code": "order_not_verified"}
    assert store.list_pending() == []
    assert direct.calls == []
    store.close()
    snapshots.close()


def test_verified_order_payment_still_requires_its_own_exact_approval(tmp_path):
    tool, store, direct, snapshots = _tool(tmp_path, verified_orders=("order-1",))
    arguments = {"order": "order-1", "paymentMethod": "bank-transfer"}

    pending = tool.execute(
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments=arguments,
    )
    store.update_status(pending.metadata["approval_id"], STATUS_APPROVED)
    done = tool.execute(
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments=arguments,
        approval_id=pending.metadata["approval_id"],
    )

    assert pending.metadata["pending_approval"] is True
    assert done.success is True
    assert len(direct.calls) == 1
    store.close()
    snapshots.close()


def test_payment_rejects_non_allowlisted_shape_before_approval(tmp_path):
    tool, store, direct, snapshots = _tool(tmp_path, verified_orders=("order-1",))

    rejected = tool.execute(
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments={
            "order": "order-1",
            "paymentMethod": "bank-transfer",
            "amount": 35_000,
        },
    )

    assert rejected.success is False
    assert json.loads(rejected.content) == {"error_code": "payment_arguments_invalid"}
    assert store.list_pending() == []
    assert direct.calls == []
    store.close()
    snapshots.close()
