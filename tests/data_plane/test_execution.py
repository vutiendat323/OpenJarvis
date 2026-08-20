"""Direct structured execution stays validated, receipt-backed and observable."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
import respx

from openjarvis.core.events import EventBus, EventType
from openjarvis.data_plane.adapters import TrendCoffeeAdapter
from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.execution import DirectExecutionEngine, ExecutionTransport
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    NormalizedBatch,
    OperationContract,
    ReceiptStatus,
    ResourceRecord,
    SourceCapability,
    StructuredQuery,
    TransportKind,
    TrustState,
)


class FixtureAdapter:
    def normalize(self, resource_type: str, payload: object) -> NormalizedBatch:
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("fixture response must contain items")
        return NormalizedBatch(
            source_id="fixture",
            resource_type=resource_type,
            records=tuple(
                ResourceRecord(resource_id=item["id"], payload=dict(item))
                for item in payload["items"]
            ),
            synced_at="2026-08-20T00:00:00+00:00",
        )

    def build_request(
        self, operation: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        return dict(arguments)

    def create_verification_claim(
        self, operation: str, request: dict[str, object]
    ) -> dict[str, str]:
        if operation == "order.place":
            return {"projection_hash": _claim_hash({"item": request.get("item")})}
        if operation == "payment.initiate" and isinstance(request.get("order"), str):
            return {
                "projection_hash": _claim_hash({"order": request["order"]}),
                "order_ref": request["order"],
            }
        raise ValueError("fixture operation has no verification claim")

    def verify_verification_claim(
        self,
        operation: str,
        claim: dict[str, str],
        candidate: NormalizedBatch,
        observed: NormalizedBatch,
    ) -> bool:
        if operation == "order.place":
            candidate_ref = (
                candidate.records[0].resource_id if candidate.records else ""
            )
            observed_record = next(
                (
                    record
                    for record in observed.records
                    if record.resource_id == candidate_ref
                ),
                None,
            )
            return observed_record is not None and claim.get(
                "projection_hash"
            ) == _claim_hash({"item": observed_record.payload.get("item")})
        if operation == "payment.initiate":
            order_ref = claim.get("order_ref")
            return (
                isinstance(order_ref, str)
                and any(
                    record.payload.get("order") == order_ref
                    for record in candidate.records
                )
                and any(record.resource_id == order_ref for record in observed.records)
                and claim.get("projection_hash") == _claim_hash({"order": order_ref})
            )
        return False


def _claim_hash(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


class RecordingTransport(ExecutionTransport):
    def __init__(self, responses: list[object] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, object]] = []
        self.csrf_value: str | None = None

    def request(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected transport request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def csrf_token(self, url: str, name: str) -> str | None:
        return self.csrf_value


def _response(*items: dict[str, object], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"items": list(items)})


def _capability(
    *,
    transport: TransportKind = TransportKind.REST,
    expires_at: str | None = None,
    trust: TrustState = TrustState.READ_VALIDATED,
) -> SourceCapability:
    return SourceCapability(
        source_id="fixture",
        provider="fixture",
        origin="https://fixture.test",
        base_url="https://fixture.test/api",
        auth_mode="none",
        credential_ref="",
        transport=transport,
        operations={
            "branch.list": OperationContract(
                "branch.list", "GET", "/branch", "branch", trust, True
            ),
            "menu.list": OperationContract(
                "menu.list",
                "GET",
                "/menu?page={page}&size={size}",
                "menu_item",
                trust,
                True,
            ),
            "order.read": OperationContract(
                "order.read", "GET", "/orders/{order_id}", "order", trust, True
            ),
            "order.place": OperationContract(
                "order.place",
                "POST",
                "/orders",
                "order",
                TrustState.WRITE_VALIDATED,
                False,
                verify_operation="order.read",
            ),
        },
        fingerprint="sha256:fixture",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="2026-08-20T00:00:00+00:00",
        expires_at=expires_at
        or (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        revision=4,
    )


@pytest.fixture
def runtime(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(_capability())
    snapshots = StructuredSnapshotStore(path)
    bus = EventBus(record_history=True)
    transport = RecordingTransport()
    direct = DirectExecutionEngine(
        capabilities,
        snapshots,
        adapters={"fixture": FixtureAdapter()},
        transports={TransportKind.REST: transport},
        bus=bus,
    )
    result = SimpleNamespace(
        capabilities=capabilities,
        snapshots=snapshots,
        bus=bus,
        transport=transport,
        direct=direct,
    )
    yield result
    direct.close()
    snapshots.close()
    capabilities.close()


def test_sync_uses_validated_contract_and_commits_snapshot(runtime):
    runtime.transport.responses = [_response({"id": "ba9355f797", "name": "Q1"})]

    receipt = runtime.direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert (
        runtime.snapshots.query(
            StructuredQuery(source_id="fixture", resource_type="branch")
        )
        .items[0]
        .resource_id
        == "ba9355f797"
    )
    assert [event.event_type for event in runtime.bus.history] == [
        EventType.SOURCE_SYNC_STARTED,
        EventType.SOURCE_SYNC_COMMITTED,
    ]


def test_sync_paginates_rest_resource_until_provider_has_no_next_page(runtime):
    runtime.transport.responses = [
        _response({"id": "first"}),
        _response({"id": "second"}),
    ]
    runtime.transport.responses[0] = httpx.Response(
        200, json={"items": [{"id": "first"}], "hasNext": True}
    )

    receipt = runtime.direct.sync("fixture", ["menu_item"])

    assert receipt.resource_count == 2
    assert [call["url"] for call in runtime.transport.calls] == [
        "https://fixture.test/api/menu?page=1&size=100",
        "https://fixture.test/api/menu?page=2&size=100",
    ]


def test_post_timeout_is_ambiguous_and_not_retried(runtime):
    runtime.transport.responses = [httpx.ReadTimeout("after send")]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.UNKNOWN
    assert receipt.error_code == "mutation_ambiguous"
    assert len(runtime.transport.calls) == 1


def test_verify_does_not_promote_an_ambiguous_mutation(runtime):
    runtime.transport.responses = [httpx.ReadTimeout("after send")]
    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    result = runtime.direct.verify(receipt.receipt_id)

    assert result.status is ReceiptStatus.FAILED
    assert result.observed is False
    assert result.error_code == "verification_failed"
    assert len(runtime.transport.calls) == 1


def test_verify_is_a_separate_get_and_only_then_promotes_candidate(runtime):
    runtime.transport.responses = [
        _response({"id": "order-1", "status": "created"}),
        _response({"id": "order-1", "status": "paid", "item": "coffee"}),
    ]
    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    before = runtime.snapshots.query(
        StructuredQuery(source_id="fixture", resource_type="order")
    )
    verified = runtime.direct.verify(receipt.receipt_id)
    after = runtime.snapshots.query(
        StructuredQuery(source_id="fixture", resource_type="order")
    )

    assert before.items == ()
    assert verified.operation == "order.read"
    assert verified.observed is True
    assert after.items[0].payload["status"] == "paid"
    assert (
        runtime.transport.calls[1]["url"] == "https://fixture.test/api/orders/order-1"
    )


def test_receipt_is_durable_and_excludes_credentials(runtime):
    runtime.transport.responses = [_response({"id": "order-1"})]
    receipt = runtime.direct.execute("fixture", "order.place", {"card": "private"})
    runtime.direct.close()

    reopened = DirectExecutionEngine(
        runtime.capabilities,
        runtime.snapshots,
        adapters={"fixture": FixtureAdapter()},
    )
    loaded = reopened.get_receipt(receipt.receipt_id)

    assert loaded == receipt
    with sqlite3.connect(runtime.capabilities.db_path) as connection:
        row = connection.execute(
            "SELECT request_hash, normalized_json FROM execution_receipts "
            "WHERE receipt_id = ?",
            (receipt.receipt_id,),
        ).fetchone()
    assert row is not None
    assert "private" not in json.dumps(row)
    reopened.close()


def test_provider_response_secrets_are_redacted_before_receipt_persistence(runtime):
    runtime.transport.responses = [
        _response({"id": "order-1", "authorization": "provider-secret"})
    ]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert "provider-secret" not in json.dumps(receipt.to_dict())
    assert "authorization" not in json.dumps(receipt.to_dict())


def test_stale_or_revoked_capability_fails_closed(runtime):
    stale = _capability(expires_at="2020-01-01T00:00:00+00:00")
    runtime.capabilities.save(stale)

    with pytest.raises(Exception, match="stale"):
        runtime.direct.sync("fixture", ["branch"])

    revoked = _capability(trust=TrustState.REVOKED)
    runtime.capabilities.save(revoked)
    with pytest.raises(Exception, match="quarantined"):
        runtime.direct.sync("fixture", ["branch"])


@respx.mock
def test_http_transport_reuses_cookies_resolves_credentials_and_refreshes_csrf(
    tmp_path,
):
    path = tmp_path / "structured.db"
    capability = _capability()
    capability_data = capability.to_dict()
    capability_data.update(
        {"auth_mode": "bearer", "credential_ref": "merchant:API_KEY"}
    )
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order.update({"csrf_cookie": "csrf", "csrf_header": "X-CSRF"})
    operations["order.place"] = order
    capability_data["operations"] = operations
    capability = SourceCapability.from_dict(capability_data)
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(capability)
    snapshots = StructuredSnapshotStore(path)
    direct = DirectExecutionEngine(
        capabilities, snapshots, adapters={"fixture": FixtureAdapter()}
    )
    respx.get("https://fixture.test/api/branch").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "branch"}]},
            headers={"set-cookie": "csrf=token; Path=/"},
        )
    )
    route = respx.post("https://fixture.test/api/orders").mock(
        return_value=_response({"id": "order-1"})
    )

    with patch(
        "openjarvis.data_plane.execution.get_tool_credential", return_value="secret"
    ):
        direct.sync("fixture", ["branch"])
        direct.execute("fixture", "order.place", {"item": "coffee"})

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["x-csrf"] == "token"
    assert request.headers["cookie"] == "csrf=token"
    direct.close()
    snapshots.close()
    capabilities.close()


@respx.mock
def test_safe_get_retries_rate_limit_with_retry_after(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(_capability())
    snapshots = StructuredSnapshotStore(path)
    sleeps: list[float] = []
    direct = DirectExecutionEngine(
        capabilities,
        snapshots,
        adapters={"fixture": FixtureAdapter()},
        sleep=sleeps.append,
    )
    route = respx.get("https://fixture.test/api/branch").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0.25"}),
            _response({"id": "branch"}),
        ]
    )

    assert direct.sync("fixture", ["branch"]).status is ReceiptStatus.SUCCEEDED
    assert route.call_count == 2
    assert sleeps == [0.25]
    direct.close()
    snapshots.close()
    capabilities.close()


def test_mcp_transport_normalizes_existing_adapter_result(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(_capability(transport=TransportKind.MCP))
    snapshots = StructuredSnapshotStore(path)
    mcp = Mock()
    mcp.execute.return_value = SimpleNamespace(
        success=True,
        content=json.dumps({"items": [{"id": "from-mcp"}]}),
    )
    direct = DirectExecutionEngine(
        capabilities,
        snapshots,
        adapters={"fixture": FixtureAdapter()},
        mcp_adapters={"branch.list": mcp},
    )

    receipt = direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert mcp.execute.call_count == 1
    assert (
        snapshots.query(StructuredQuery(source_id="fixture", resource_type="branch"))
        .items[0]
        .resource_id
        == "from-mcp"
    )
    direct.close()
    snapshots.close()
    capabilities.close()


def test_execution_migrates_shared_database_to_v3_and_all_store_owners_reopen(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(_capability())
    capabilities.close()
    snapshots = StructuredSnapshotStore(path)
    snapshots.close()

    migrated_capabilities = SQLiteCapabilityStore(path)
    migrated_snapshots = StructuredSnapshotStore(path)
    direct = DirectExecutionEngine(
        migrated_capabilities,
        migrated_snapshots,
        adapters={"fixture": FixtureAdapter()},
    )
    direct.close()
    migrated_snapshots.close()
    migrated_capabilities.close()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM data_plane_schema"
        ).fetchall() == [(3,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'execution_receipts'"
        ).fetchone() == ("execution_receipts",)
    reopened_capabilities = SQLiteCapabilityStore(path)
    reopened_snapshots = StructuredSnapshotStore(path)
    assert reopened_capabilities.get("fixture") is not None
    assert (
        reopened_snapshots.query(
            StructuredQuery(source_id="fixture", resource_type="branch")
        ).items
        == ()
    )
    reopened_snapshots.close()
    reopened_capabilities.close()


@respx.mock
def test_redirect_target_is_revalidated_before_following(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(_capability())
    snapshots = StructuredSnapshotStore(path)
    direct = DirectExecutionEngine(
        capabilities, snapshots, adapters={"fixture": FixtureAdapter()}
    )
    respx.get("https://fixture.test/api/branch").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://other.test/branch"}
        )
    )

    receipt = direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "capability_quarantined"
    direct.close()
    snapshots.close()
    capabilities.close()


def test_response_schema_rejection_does_not_commit_snapshot(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    branch = dict(operations["branch.list"])
    branch["response_schema"] = {"type": "object", "required": ["required"]}
    operations["branch.list"] = branch
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.responses = [_response({"id": "branch"})]

    receipt = runtime.direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "schema_mismatch"
    assert (
        runtime.snapshots.query(
            StructuredQuery(source_id="fixture", resource_type="branch")
        ).items
        == ()
    )


def test_missing_csrf_token_returns_failed_receipt_without_dispatch(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order.update({"csrf_cookie": "csrf", "csrf_header": "X-CSRF"})
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "authentication_expired"
    assert runtime.transport.calls == []


def test_request_schema_rejection_happens_before_mutation_dispatch(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order["request_schema"] = {"type": "object", "required": ["item", "branch"]}
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "schema_mismatch"
    assert runtime.transport.calls == []


def test_idempotent_post_retries_only_when_a_key_is_supplied(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order["idempotency_header"] = "Idempotency-Key"
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.responses = [
        httpx.ConnectError("before send"),
        _response({"id": "order-1"}),
    ]

    receipt = runtime.direct.execute(
        "fixture",
        "order.place",
        {"item": "coffee", "Idempotency-Key": "request-key"},
    )

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert len(runtime.transport.calls) == 2
    assert runtime.transport.calls[0]["headers"]["Idempotency-Key"] == "request-key"


def test_unsafe_redirect_is_rejected_without_replaying_mutation(runtime):
    runtime.transport.responses = [httpx.Response(307, headers={"location": "/orders"})]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.FAILED
    assert len(runtime.transport.calls) == 1


def test_public_receipts_hide_unverified_candidate_data(runtime):
    runtime.transport.responses = [_response({"id": "order-1", "qrCode": "qr-secret"})]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})
    reopened = runtime.direct.get_receipt(receipt.receipt_id)

    assert receipt.normalized == {}
    assert reopened is not None
    assert reopened.normalized == {}
    assert "qr-secret" not in json.dumps(receipt.to_dict())


def test_verify_rejects_receipt_after_capability_revision_changes(runtime):
    runtime.transport.responses = [_response({"id": "order-1"})]
    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})
    capability = runtime.capabilities.get("fixture")
    assert capability is not None
    runtime.capabilities.save(
        SourceCapability.from_dict({**capability.to_dict(), "revision": 5})
    )

    result = runtime.direct.verify(receipt.receipt_id)

    assert result.status is ReceiptStatus.FAILED
    assert result.error_code == "verification_failed"
    assert len(runtime.transport.calls) == 1


def test_verify_rejects_tampered_candidate_request_hash_before_observation(runtime):
    runtime.transport.responses = [_response({"id": "order-1"})]
    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})
    with runtime.direct._conn:
        runtime.direct._conn.execute(
            "UPDATE execution_receipts SET normalized_json = ? WHERE receipt_id = ?",
            (
                json.dumps({"request_hash": "sha256:wrong", "candidate": {}}),
                receipt.receipt_id,
            ),
        )

    result = runtime.direct.verify(receipt.receipt_id)

    assert result.status is ReceiptStatus.FAILED
    assert result.error_code == "verification_failed"
    assert len(runtime.transport.calls) == 1


def test_connect_error_for_unsafe_request_creates_unknown_receipt(runtime):
    runtime.transport.responses = [httpx.ConnectError("connection dropped")]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.UNKNOWN
    assert receipt.error_code == "mutation_ambiguous"


def test_safe_request_error_exhaustion_returns_failed_sync_receipt(runtime):
    runtime.transport.responses = [
        httpx.ConnectError("first"),
        httpx.ConnectError("second"),
    ]

    receipt = runtime.direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "provider_unavailable"
    assert len(runtime.transport.calls) == 2


def test_sync_rejects_safe_post_contract_before_dispatch(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    branch = dict(operations["branch.list"])
    branch["method"] = "POST"
    operations["branch.list"] = branch
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))

    with pytest.raises(Exception, match="GET or HEAD"):
        runtime.direct.sync("fixture", ["branch"])

    assert runtime.transport.calls == []


def test_request_schema_subset_rejects_extra_and_wrong_nested_values(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order["request_schema"] = {
        "type": "object",
        "required": ["item"],
        "additionalProperties": False,
        "properties": {
            "item": {
                "type": "object",
                "required": ["size"],
                "properties": {"size": {"enum": ["small", "large"]}},
            }
        },
    }
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))

    receipt = runtime.direct.execute(
        "fixture", "order.place", {"item": {"size": "medium"}, "extra": True}
    )

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "schema_mismatch"
    assert runtime.transport.calls == []


def test_injected_transport_owns_csrf_cookie_lookup(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order.update({"csrf_cookie": "csrf", "csrf_header": "X-CSRF"})
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.csrf_value = "injected-token"
    runtime.transport.responses = [_response({"id": "order-1"})]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert runtime.transport.calls[0]["headers"]["X-CSRF"] == "injected-token"


def test_payment_candidate_requires_matching_observed_order_reference(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    operations["payment.initiate"] = OperationContract(
        "payment.initiate",
        "POST",
        "/payments",
        "payment",
        TrustState.WRITE_VALIDATED,
        False,
        verify_operation="order.read",
    ).to_dict()
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.responses = [
        _response({"id": "payment-1", "order": "order-1", "qrCode": "merchant-qr"}),
        _response({"id": "order-1", "status": "paid"}),
    ]

    receipt = runtime.direct.execute(
        "fixture", "payment.initiate", {"order": "order-1"}
    )
    result = runtime.direct.verify(receipt.receipt_id)

    assert receipt.normalized == {}
    assert result.status is ReceiptStatus.SUCCEEDED
    assert (
        runtime.snapshots.query(
            StructuredQuery(source_id="fixture", resource_type="payment")
        )
        .items[0]
        .payload["order"]
        == "order-1"
    )


def test_verify_rejects_provider_candidate_and_observation_for_other_request(runtime):
    runtime.transport.responses = [
        _response({"id": "order-1", "item": "tea"}),
        _response({"id": "order-1", "item": "tea", "status": "paid"}),
    ]

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})
    result = runtime.direct.verify(receipt.receipt_id)

    assert result.status is ReceiptStatus.FAILED
    assert result.error_code == "verification_failed"


class NoClaimAdapter:
    normalize = FixtureAdapter.normalize


class EmptyClaimAdapter(FixtureAdapter):
    def create_verification_claim(
        self, operation: str, request: dict[str, object]
    ) -> dict[str, str]:
        return {}


class MalformedClaimAdapter(FixtureAdapter):
    def create_verification_claim(
        self, operation: str, request: dict[str, object]
    ) -> dict[str, str]:
        return {"projection_hash": "not-a-hash"}


@pytest.mark.parametrize(
    "adapter", [NoClaimAdapter(), EmptyClaimAdapter(), MalformedClaimAdapter()]
)
def test_execute_fails_before_dispatch_without_valid_correlation_claim(
    runtime, adapter
):
    runtime.direct._adapters["fixture"] = adapter

    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "capability_quarantined"
    assert runtime.transport.calls == []
    assert [event.event_type for event in runtime.bus.history] == [
        EventType.SOURCE_EXECUTE_STARTED,
        EventType.SOURCE_EXECUTE_RECEIPT_CREATED,
    ]


def test_trend_execution_dispatches_adapter_built_provider_request(runtime):
    capability_data = _capability().to_dict()
    capability_data.update(
        {
            "source_id": "trendcoffee",
            "provider": "trendcoffee",
            "base_url": "https://fixture.test/api/latest",
            "revision": 1,
        }
    )
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.direct._adapters["trendcoffee"] = TrendCoffeeAdapter()
    runtime.transport.responses = [
        {
            "statusCode": 200,
            "result": {"slug": "order-1"},
        }
    ]

    receipt = runtime.direct.execute(
        "trendcoffee",
        "order.place",
        {
            "order_type": "take-out",
            "branch_slug": "branch-1",
            "items": [{"quantity": 1, "variant_slug": "variant-1", "note": "ít đá"}],
        },
    )

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert runtime.transport.calls[0]["arguments"] == {
        "type": "take-out",
        "timeLeftTakeOut": 0,
        "deliveryTo": "",
        "deliveryPhone": "",
        "table": "",
        "branch": "branch-1",
        "owner": "",
        "approvalBy": "",
        "orderItems": [
            {"quantity": 1, "variant": "variant-1", "promotion": None, "note": "ít đá"}
        ],
        "voucher": None,
        "description": "",
    }


@pytest.mark.parametrize(
    "path_argument",
    ["a/b", r"a\\b", "a?b", "a#b", "a\nb", ".", "..", "%2F", "%2E", "%2e%2e", "", 1],
)
def test_path_placeholder_rejects_non_segment_values_before_dispatch(
    runtime, path_argument
):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order["path"] = "/orders/{order_id}"
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.responses = [_response({"id": "order-1"})]

    receipt = runtime.direct.execute(
        "fixture", "order.place", {"item": "coffee", "order_id": path_argument}
    )

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "schema_mismatch"
    assert runtime.transport.calls == []


def test_path_placeholder_percent_encodes_valid_unicode_and_space(runtime):
    capability_data = _capability().to_dict()
    operations = dict(capability_data["operations"])
    order = dict(operations["order.place"])
    order["path"] = "/orders/{order_id}"
    operations["order.place"] = order
    capability_data["operations"] = operations
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.responses = [_response({"id": "order-1"})]

    receipt = runtime.direct.execute(
        "fixture", "order.place", {"item": "coffee", "order_id": "cà phê"}
    )

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert runtime.transport.calls[0]["url"].endswith("/orders/c%C3%A0%20ph%C3%AA")


@respx.mock
def test_generic_graphql_sync_posts_discovered_query_template(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(
        SourceCapability(
            source_id="generic",
            provider="generic",
            origin="https://generic.test",
            base_url="https://generic.test/graphql",
            auth_mode="none",
            credential_ref="",
            transport=TransportKind.GRAPHQL,
            operations={
                "graphql.menu": OperationContract(
                    "graphql.menu",
                    "GET",
                    "/graphql",
                    "structured_record",
                    TrustState.READ_VALIDATED,
                    True,
                    request_schema={
                        "query_template": "query { menu { id name } }",
                    },
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
    snapshots = StructuredSnapshotStore(path)
    direct = DirectExecutionEngine(capabilities, snapshots)
    route = respx.post("https://generic.test/graphql").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"menu": [{"id": "espresso", "name": "Espresso"}]}},
        )
    )

    receipt = direct.sync("generic", ["structured_record"])

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert json.loads(route.calls[0].request.content) == {
        "query": "query { menu { id name } }",
        "variables": {},
    }
    assert (
        snapshots.query(
            StructuredQuery(source_id="generic", resource_type="structured_record")
        )
        .items[0]
        .resource_id
        == "espresso"
    )
    direct.close()
    snapshots.close()
    capabilities.close()


@respx.mock
def test_generic_graphql_scalar_result_normalizes_to_deterministic_record(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(
        SourceCapability(
            source_id="generic",
            provider="generic",
            origin="https://generic.test",
            base_url="https://generic.test/graphql",
            auth_mode="none",
            credential_ref="",
            transport=TransportKind.GRAPHQL,
            operations={
                "graphql.status": OperationContract(
                    "graphql.status",
                    "GET",
                    "/graphql",
                    "structured_record",
                    TrustState.READ_VALIDATED,
                    True,
                    request_schema={"query_template": "query { status }"},
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
    snapshots = StructuredSnapshotStore(path)
    direct = DirectExecutionEngine(capabilities, snapshots)
    respx.post("https://generic.test/graphql").mock(
        return_value=httpx.Response(200, json={"data": {"status": "ready"}})
    )

    receipt = direct.sync("generic", ["structured_record"])
    record = snapshots.query(
        StructuredQuery(source_id="generic", resource_type="structured_record")
    ).items[0]

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert record.payload == {"value": "ready"}
    assert record.resource_id == _claim_hash({"value": "ready"}).removeprefix("sha256:")
    direct.close()
    snapshots.close()
    capabilities.close()


def test_unknown_provider_does_not_use_generic_normalization(runtime):
    capability_data = _capability().to_dict()
    capability_data["provider"] = "unknown-provider"
    runtime.capabilities.save(SourceCapability.from_dict(capability_data))
    runtime.transport.responses = [_response({"id": "record-1"})]

    receipt = runtime.direct.sync("fixture", ["branch"])

    assert receipt.status is ReceiptStatus.FAILED
    assert receipt.error_code == "capability_missing"
    assert runtime.transport.calls == []


@respx.mock
def test_generic_rest_sync_normalizes_and_commits_records(tmp_path):
    path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(path)
    capabilities.save(
        SourceCapability(
            source_id="generic",
            provider="generic",
            origin="https://generic.test",
            base_url="https://generic.test/api",
            auth_mode="none",
            credential_ref="",
            transport=TransportKind.REST,
            operations={
                "records.list": OperationContract(
                    "records.list",
                    "GET",
                    "/records",
                    "structured_record",
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
    snapshots = StructuredSnapshotStore(path)
    direct = DirectExecutionEngine(capabilities, snapshots)
    respx.get("https://generic.test/api/records").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "record-1", "name": "Record"}]},
        )
    )

    receipt = direct.sync("generic", ["structured_record"])

    assert receipt.status is ReceiptStatus.SUCCEEDED
    assert (
        snapshots.query(
            StructuredQuery(source_id="generic", resource_type="structured_record")
        )
        .items[0]
        .resource_id
        == "record-1"
    )
    direct.close()
    snapshots.close()
    capabilities.close()


def test_failed_verification_activation_leaves_candidate_out_of_snapshot(runtime):
    runtime.transport.responses = [
        _response({"id": "order-1", "status": "created"}),
        _response(
            {"id": "order-1", "status": "paid"},
            {"id": "order-1", "status": "duplicate"},
        ),
    ]
    receipt = runtime.direct.execute("fixture", "order.place", {"item": "coffee"})

    result = runtime.direct.verify(receipt.receipt_id)

    assert result.status is ReceiptStatus.FAILED
    assert (
        runtime.snapshots.query(
            StructuredQuery(source_id="fixture", resource_type="order")
        ).items
        == ()
    )
