"""Atomic one-time claims for Data Plane execution approvals."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from openjarvis.tools.approval_store import (
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_EXECUTING,
    TIER_HIGH,
    ApprovalStore,
)


def _approved_action(store: ApprovalStore, request_hash: str = "request-hash"):
    action = store.queue_action(
        action_type="source_execute",
        description="Execute one provider mutation",
        payload={
            "source_id": "trend-coffee",
            "operation": "order.place",
            "preview": {"argument_keys": ["items"]},
            "capability_revision": 7,
            "request_hash": request_hash,
        },
        permission_key="source_execute:trend-coffee:order.place",
        tier=TIER_HIGH,
    )
    store.update_status(action.id, STATUS_APPROVED)
    return action


def test_only_one_concurrent_claim_can_enter_execution(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    action = _approved_action(store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: store.claim_execution(
                    action.id,
                    action_type="source_execute",
                    request_hash="request-hash",
                ),
                range(2),
            )
        )

    assert sorted(result.status for result in results) == ["claimed", "rejected"]
    assert store.get_action(action.id).status == STATUS_EXECUTING
    store.close()


def test_hash_mismatch_does_not_burn_an_approved_action(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    action = _approved_action(store)

    result = store.claim_execution(
        action.id,
        action_type="source_execute",
        request_hash="different-hash",
    )

    assert result.status == "rejected"
    assert result.reason == "approval_request_hash_mismatch"
    assert store.get_action(action.id).status == STATUS_APPROVED
    store.close()


def test_terminal_execution_cannot_be_reapproved_for_replay(tmp_path):
    store = ApprovalStore(str(tmp_path / "approvals.db"))
    action = _approved_action(store)
    claim = store.claim_execution(
        action.id,
        action_type="source_execute",
        request_hash="request-hash",
    )
    assert claim.status == "claimed"
    assert store.finish_execution(
        action.id, request_hash="request-hash", status=STATUS_EXECUTED
    )

    changed = store.update_status(action.id, STATUS_APPROVED)

    assert changed is False
    assert store.get_action(action.id).status == STATUS_EXECUTED
    store.close()
