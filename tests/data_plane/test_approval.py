"""Who approves a provider mutation, and how the grant stays exact."""

from __future__ import annotations

import pytest

from openjarvis.tools.approval_store import ApprovalStore


def test_conversational_gate_mints_a_grant_without_an_operator(tmp_path):
    """A kiosk customer is the approver, and there is no second person to ask.

    The operator queue assumes someone is watching a dashboard. In a voice
    kiosk nobody is, so `prepare()` alone stranded every order: the Agent
    stopped with an empty answer and the customer heard silence.
    """
    from openjarvis.data_plane.approval import ExecutionApprovalGate

    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    gate = ExecutionApprovalGate(store, conversational=True)
    arguments = {"order_type": "take-out"}

    grant = gate.prepare_and_authorize("trend-coffee", "order.place", arguments, 1)

    assert gate.conversational is True
    # The hash binding survives: the grant still refuses a different request.
    grant.consume("trend-coffee", "order.place", arguments, 1)


def test_conversational_grant_still_refuses_a_request_it_did_not_authorize(tmp_path):
    from openjarvis.data_plane.approval import ApprovalError, ExecutionApprovalGate

    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))
    gate = ExecutionApprovalGate(store, conversational=True)

    grant = gate.prepare_and_authorize(
        "trend-coffee", "order.place", {"order_type": "take-out"}, 1
    )

    with pytest.raises(ApprovalError):
        grant.consume("trend-coffee", "order.place", {"order_type": "delivery"}, 1)


def test_gate_defaults_to_requiring_an_operator(tmp_path):
    from openjarvis.data_plane.approval import ExecutionApprovalGate

    store = ApprovalStore(db_path=str(tmp_path / "approvals.db"))

    assert ExecutionApprovalGate(store).conversational is False
