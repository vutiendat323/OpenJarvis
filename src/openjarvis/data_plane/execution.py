"""Validated direct structured execution with receipt-backed verification."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from string import Formatter
from typing import Protocol
from urllib.parse import quote, urljoin, urlsplit

import httpx

import openjarvis.data_plane.adapters  # noqa: F401
from openjarvis.core.credentials import get_tool_credential
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import SourceAdapterRegistry
from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    ExecutionReceipt,
    NormalizedBatch,
    OperationContract,
    ReceiptStatus,
    ResourceRecord,
    SnapshotRef,
    SourceCapability,
    SyncReceipt,
    TransportKind,
    TrustState,
    VerificationResult,
)
from openjarvis.security.ssrf import check_ssrf

_CREATE_SCHEMA_TABLE = """\
CREATE TABLE IF NOT EXISTS data_plane_schema (
    version INTEGER NOT NULL
)
"""
_CREATE_RECEIPTS_TABLE = """\
CREATE TABLE IF NOT EXISTS execution_receipts (
    receipt_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    capability_revision INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_ref TEXT NOT NULL DEFAULT '',
    normalized_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
)
"""
_RETRY_STATUSES = frozenset({429, 503})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class ExecutionTransport(Protocol):
    """One validated provider transport; it does not make policy decisions."""

    def request(self, **kwargs: object) -> object: ...

    def close(self) -> None: ...


class HttpExecutionTransport:
    """Persistent, per-origin HTTPX clients owned by direct execution."""

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._clients: dict[str, httpx.Client] = {}

    def request(self, **kwargs: object) -> httpx.Response:
        capability = kwargs["capability"]
        operation = kwargs["operation"]
        url = kwargs["url"]
        arguments = kwargs["arguments"]
        headers = kwargs["headers"]
        assert isinstance(capability, SourceCapability)
        assert isinstance(operation, OperationContract)
        assert isinstance(url, str)
        assert isinstance(arguments, dict)
        assert isinstance(headers, dict)
        origin = _origin(url)
        client = self._clients.setdefault(
            origin,
            httpx.Client(follow_redirects=False, timeout=self._timeout_seconds),
        )
        path_arguments = set(_format_fields(operation.path))
        excluded_arguments = path_arguments | {operation.idempotency_header}
        payload = {
            key: value
            for key, value in arguments.items()
            if key not in excluded_arguments
        }
        if capability.transport is TransportKind.GRAPHQL:
            template = operation.request_schema.get("query_template")
            if not isinstance(template, str) or not template:
                raise DataPlaneError(
                    DataPlaneErrorCode.SCHEMA_MISMATCH,
                    "GraphQL operation has no query template",
                )
            return client.post(
                url,
                json={"query": template, "variables": payload},
                headers=headers,
            )
        if operation.method.upper() in {"GET", "HEAD"}:
            return client.request(
                operation.method, url, params=payload, headers=headers
            )
        return client.request(operation.method, url, json=payload, headers=headers)

    def csrf_token(self, url: str, name: str) -> str | None:
        client = self._clients.get(_origin(url))
        if client is None:
            return None
        try:
            return client.cookies.get(name)
        except httpx.CookieConflict:
            return None

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


class McpExecutionTransport:
    """Reuse existing MCP tool adapters without introducing another agent path."""

    def __init__(self, adapters: Mapping[str, object]) -> None:
        self._adapters = dict(adapters)

    def request(self, **kwargs: object) -> object:
        operation = kwargs["operation"]
        arguments = kwargs["arguments"]
        assert isinstance(operation, OperationContract)
        assert isinstance(arguments, dict)
        adapter = self._adapters.get(operation.name)
        if adapter is None:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_MISSING,
                f"No MCP adapter for {operation.name}",
            )
        result = adapter.execute(**arguments)  # type: ignore[union-attr]
        if not getattr(result, "success", False):
            raise DataPlaneError(
                DataPlaneErrorCode.PROVIDER_UNAVAILABLE,
                "MCP execution failed",
            )
        content = getattr(result, "content", "")
        try:
            return json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH,
                "MCP result is not JSON",
            ) from exc

    def close(self) -> None:
        return None


class DirectExecutionEngine:
    """Execute validated contracts and promote unsafe results only via verify."""

    def __init__(
        self,
        capabilities: SQLiteCapabilityStore,
        snapshots: StructuredSnapshotStore,
        *,
        adapters: Mapping[str, object] | None = None,
        transports: Mapping[TransportKind, ExecutionTransport] | None = None,
        mcp_adapters: Mapping[str, object] | None = None,
        bus: EventBus | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._capabilities = capabilities
        self._snapshots = snapshots
        self._adapters = dict(adapters or {})
        self._bus = bus
        self._sleep = sleep
        self._http = HttpExecutionTransport()
        self._transports: dict[TransportKind, ExecutionTransport] = {
            TransportKind.REST: self._http,
            TransportKind.GRAPHQL: self._http,
            TransportKind.PROVIDER: self._http,
            TransportKind.MCP: McpExecutionTransport(mcp_adapters or {}),
        }
        self._transports.update(transports or {})
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(capabilities.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def sync(self, source_id: str, resources: list[str]) -> SyncReceipt:
        started_at = _now()
        capability = self._capability(source_id)
        self._publish(
            EventType.SOURCE_SYNC_STARTED,
            source_id=source_id,
            capability_revision=capability.revision,
            count=len(resources),
        )
        operations = [
            self._read_operation(capability, resource) for resource in resources
        ]
        try:
            batches = tuple(
                self._read_batch(capability, operation, {}) for operation in operations
            )
            commits = self._snapshots.upsert_many(batches)
            refs = tuple(commit.ref for commit in commits)
            count = sum(commit.resource_count for commit in commits)
        except DataPlaneError as exc:
            self._publish(
                EventType.SOURCE_SYNC_FAILED,
                source_id=source_id,
                capability_revision=capability.revision,
                status=ReceiptStatus.FAILED.value,
            )
            return _sync_receipt(
                source_id, resources, ReceiptStatus.FAILED, started_at, exc.code.value
            )
        except ValueError:
            self._publish(
                EventType.SOURCE_SYNC_FAILED,
                source_id=source_id,
                capability_revision=capability.revision,
                status=ReceiptStatus.FAILED.value,
            )
            return _sync_receipt(
                source_id,
                resources,
                ReceiptStatus.FAILED,
                started_at,
                DataPlaneErrorCode.SCHEMA_MISMATCH.value,
            )
        receipt = _sync_receipt(
            source_id,
            resources,
            ReceiptStatus.SUCCEEDED,
            started_at,
            snapshot_refs=refs,
            resource_count=count,
        )
        self._publish(
            EventType.SOURCE_SYNC_COMMITTED,
            source_id=source_id,
            capability_revision=capability.revision,
            count=count,
            status=receipt.status.value,
        )
        return receipt

    def execute(
        self, source_id: str, operation_name: str, arguments: dict[str, object]
    ) -> ExecutionReceipt:
        capability = self._capability(source_id)
        operation = self._operation(capability, operation_name)
        if operation.safe:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Use sync for validated read operations",
            )
        if operation.trust is not TrustState.WRITE_VALIDATED:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Mutation capability is not write validated",
            )
        request_hash = _request_hash(capability, operation_name, arguments)
        self._publish(
            EventType.SOURCE_EXECUTE_STARTED,
            source_id=source_id,
            operation=operation_name,
            capability_revision=capability.revision,
        )
        verification_claim: dict[str, str] = {}
        try:
            adapter = self._normalization_adapter(capability)
            provider_arguments = self._provider_request(adapter, operation, arguments)
            self._validate_request(operation, provider_arguments)
            verification_claim = self._verification_claim(
                adapter, operation, provider_arguments
            )
            payload = self._dispatch(capability, operation, provider_arguments)
            self._validate_response(operation, payload)
            candidate = _redact_batch(self._normalize(capability, operation, payload))
            provider_ref = _provider_ref(candidate)
            receipt = self._new_receipt(
                capability,
                operation_name,
                request_hash,
                ReceiptStatus.SUCCEEDED,
                provider_ref=provider_ref,
                normalized=candidate.to_dict(),
            )
        except httpx.TimeoutException:
            receipt = self._new_receipt(
                capability,
                operation_name,
                request_hash,
                ReceiptStatus.UNKNOWN,
                error_code=DataPlaneErrorCode.MUTATION_AMBIGUOUS.value,
            )
        except httpx.RequestError:
            receipt = self._new_receipt(
                capability,
                operation_name,
                request_hash,
                ReceiptStatus.UNKNOWN,
                error_code=DataPlaneErrorCode.MUTATION_AMBIGUOUS.value,
            )
        except DataPlaneError as exc:
            receipt = self._new_receipt(
                capability,
                operation_name,
                request_hash,
                ReceiptStatus.FAILED,
                error_code=exc.code.value,
            )
        self._save_receipt(receipt, verification_claim)
        self._publish(
            EventType.SOURCE_EXECUTE_RECEIPT_CREATED,
            source_id=source_id,
            operation=operation_name,
            capability_revision=capability.revision,
            status=receipt.status.value,
        )
        return _public_receipt(receipt)

    def verify(self, receipt_id: str) -> VerificationResult:
        receipt = self._load_receipt(receipt_id, private=True)
        if receipt is None:
            raise KeyError(receipt_id)
        capability = self._capability(receipt.source_id)
        operation = self._operation(capability, receipt.operation)
        if not operation.verify_operation:
            raise DataPlaneError(
                DataPlaneErrorCode.VERIFICATION_FAILED,
                "Execution has no safe verification operation",
            )
        verify_operation = self._operation(capability, operation.verify_operation)
        if not verify_operation.safe:
            raise DataPlaneError(
                DataPlaneErrorCode.VERIFICATION_FAILED,
                "Verification operation is not safe",
            )
        if capability.revision != receipt.capability_revision:
            return self._complete_verification(
                receipt,
                capability,
                verify_operation.name,
                observed=False,
                status=ReceiptStatus.FAILED,
                error_code=DataPlaneErrorCode.VERIFICATION_FAILED.value,
            )
        if (
            receipt.status is not ReceiptStatus.SUCCEEDED
            or not receipt.provider_ref
            or not receipt.normalized
        ):
            return self._complete_verification(
                receipt,
                capability,
                verify_operation.name,
                observed=False,
                status=ReceiptStatus.FAILED,
                error_code=DataPlaneErrorCode.VERIFICATION_FAILED.value,
            )
        try:
            candidate = _candidate_batch(receipt)
            adapter = self._adapter_for(capability)
            claim = _candidate_claim(receipt)
            if not self._claim_adapter_available(adapter):
                raise DataPlaneError(
                    DataPlaneErrorCode.VERIFICATION_FAILED,
                    "Provider has no verification correlation",
                )
            observed = self._read_batch(
                capability,
                verify_operation,
                _path_arguments(verify_operation.path, receipt.provider_ref),
            )
            if not _candidate_matches_observation(
                candidate, observed, receipt.provider_ref
            ):
                raise DataPlaneError(
                    DataPlaneErrorCode.VERIFICATION_FAILED,
                    "Provider observation does not match receipt reference",
                )
            if not self._verification_claim_matches(
                adapter, receipt.operation, claim, candidate, observed
            ):
                raise DataPlaneError(
                    DataPlaneErrorCode.VERIFICATION_FAILED,
                    "Provider observation does not match receipt claim",
                )
            refs = tuple(
                commit.ref
                for commit in self._snapshots.upsert_many((candidate, observed))
            )
            result = VerificationResult(
                receipt_id=receipt_id,
                operation=verify_operation.name,
                observed=True,
                status=ReceiptStatus.SUCCEEDED,
                snapshot_refs=refs,
            )
        except DataPlaneError as exc:
            result = VerificationResult(
                receipt_id=receipt_id,
                operation=verify_operation.name,
                observed=False,
                status=ReceiptStatus.FAILED,
                error_code=exc.code.value,
            )
        except ValueError:
            result = VerificationResult(
                receipt_id=receipt_id,
                operation=verify_operation.name,
                observed=False,
                status=ReceiptStatus.FAILED,
                error_code=DataPlaneErrorCode.VERIFICATION_FAILED.value,
            )
        return self._complete_verification(
            receipt,
            capability,
            result.operation,
            observed=result.observed,
            status=result.status,
            snapshot_refs=result.snapshot_refs,
            error_code=result.error_code,
        )

    def get_receipt(self, receipt_id: str) -> ExecutionReceipt | None:
        receipt = self._load_receipt(receipt_id)
        return _public_receipt(receipt) if receipt is not None else None

    def _load_receipt(
        self, receipt_id: str, *, private: bool = False
    ) -> ExecutionReceipt | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM execution_receipts WHERE receipt_id = ?", (receipt_id,)
            ).fetchone()
        if row is None:
            return None
        return ExecutionReceipt(
            receipt_id=row["receipt_id"],
            source_id=row["source_id"],
            operation=row["operation"],
            capability_revision=row["capability_revision"],
            request_hash=row["request_hash"],
            status=ReceiptStatus(row["status"]),
            provider_ref=row["provider_ref"],
            normalized=dict(json.loads(row["normalized_json"])) if private else {},
            created_at=row["created_at"],
        )

    def _complete_verification(
        self,
        receipt: ExecutionReceipt,
        capability: SourceCapability,
        operation: str,
        *,
        observed: bool,
        status: ReceiptStatus,
        snapshot_refs: tuple[SnapshotRef, ...] = (),
        error_code: str = "",
    ) -> VerificationResult:
        result = VerificationResult(
            receipt_id=receipt.receipt_id,
            operation=operation,
            observed=observed,
            status=status,
            snapshot_refs=snapshot_refs,
            error_code=error_code,
        )
        self._publish(
            EventType.SOURCE_VERIFY_COMPLETED,
            source_id=receipt.source_id,
            operation=operation,
            capability_revision=capability.revision,
            status=status.value,
        )
        return result

    @staticmethod
    def _new_receipt(
        capability: SourceCapability,
        operation: str,
        request_hash: str,
        status: ReceiptStatus,
        *,
        provider_ref: str = "",
        normalized: dict[str, object] | None = None,
        error_code: str = "",
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            receipt_id=str(uuid.uuid4()),
            source_id=capability.source_id,
            operation=operation,
            capability_revision=capability.revision,
            request_hash=request_hash,
            status=status,
            provider_ref=provider_ref,
            normalized=normalized or {},
            created_at=_now(),
            error_code=error_code,
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
        seen: set[int] = set()
        for transport in self._transports.values():
            if id(transport) not in seen:
                seen.add(id(transport))
                close = getattr(transport, "close", None)
                if close is not None:
                    close()

    def _migrate(self) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(_CREATE_SCHEMA_TABLE)
                rows = self._conn.execute(
                    "SELECT version FROM data_plane_schema"
                ).fetchall()
                if not rows:
                    self._conn.execute(
                        "INSERT INTO data_plane_schema (version) VALUES (3)"
                    )
                elif len(rows) == 1 and rows[0]["version"] in (1, 2):
                    self._conn.execute("UPDATE data_plane_schema SET version = 3")
                elif len(rows) != 1 or rows[0]["version"] != 3:
                    raise RuntimeError("unsupported data-plane schema version")
                self._conn.execute(_CREATE_RECEIPTS_TABLE)
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def _capability(self, source_id: str) -> SourceCapability:
        capability = self._capabilities.get(source_id)
        if capability is None:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_MISSING, "Capability missing"
            )
        if _is_expired(capability.expires_at):
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_STALE, "Capability is stale"
            )
        self._validate_capability_origin(capability)
        return capability

    def _operation(self, capability: SourceCapability, name: str) -> OperationContract:
        operation = capability.operations.get(name)
        if operation is None:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_MISSING, "Operation missing"
            )
        if operation.trust in {
            TrustState.CANDIDATE,
            TrustState.QUARANTINED,
            TrustState.REVOKED,
        }:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Operation capability is quarantined",
            )
        return operation

    def _read_operation(
        self, capability: SourceCapability, resource_type: str
    ) -> OperationContract:
        for operation in capability.operations.values():
            if operation.resource_type == resource_type and operation.safe:
                validated = self._operation(capability, operation.name)
                if validated.method.upper() not in {"GET", "HEAD"}:
                    raise DataPlaneError(
                        DataPlaneErrorCode.SCHEMA_MISMATCH,
                        "Safe sync requires a GET or HEAD contract method",
                    )
                return validated
        raise DataPlaneError(
            DataPlaneErrorCode.CAPABILITY_MISSING, "Read operation missing"
        )

    def _adapter_for(self, capability: SourceCapability) -> object | None:
        adapter = self._adapters.get(capability.provider)
        if adapter is not None:
            return adapter
        try:
            return SourceAdapterRegistry.create(capability.provider)
        except KeyError:
            return None

    def _normalization_adapter(self, capability: SourceCapability) -> object | None:
        adapter = self._adapter_for(capability)
        if adapter is None and capability.provider != "generic":
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_MISSING,
                "Provider adapter is unavailable",
            )
        return adapter

    @staticmethod
    def _provider_request(
        adapter: object | None,
        operation: OperationContract,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        build_request = getattr(adapter, "build_request", None)
        if not callable(build_request):
            return arguments
        try:
            request = build_request(operation.name, arguments)
        except (TypeError, ValueError) as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH,
                "Provider request is invalid",
            ) from exc
        if not isinstance(request, dict):
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH,
                "Provider request is invalid",
            )
        return request

    @staticmethod
    def _verification_claim(
        adapter: object | None,
        operation: OperationContract,
        request: dict[str, object],
    ) -> dict[str, str]:
        create_claim = getattr(adapter, "create_verification_claim", None)
        verify_claim = getattr(adapter, "verify_verification_claim", None)
        if not callable(create_claim) or not callable(verify_claim):
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Provider has no verification correlation",
            )
        try:
            claim = create_claim(operation.name, request)
        except (TypeError, ValueError) as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Provider verification claim is invalid",
            ) from exc
        if not _is_private_claim(claim):
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Provider verification claim is invalid",
            )
        return claim

    @staticmethod
    def _claim_adapter_available(adapter: object | None) -> bool:
        return callable(getattr(adapter, "verify_verification_claim", None))

    @staticmethod
    def _verification_claim_matches(
        adapter: object | None,
        operation_name: str,
        claim: dict[str, str],
        candidate: NormalizedBatch,
        observed: NormalizedBatch,
    ) -> bool:
        verify_claim = getattr(adapter, "verify_verification_claim", None)
        if not callable(verify_claim):
            return False
        try:
            return bool(verify_claim(operation_name, claim, candidate, observed))
        except (TypeError, ValueError):
            return False

    def _read_batch(
        self,
        capability: SourceCapability,
        operation: OperationContract,
        arguments: dict[str, object],
    ) -> NormalizedBatch:
        self._normalization_adapter(capability)
        pages: list[NormalizedBatch] = []
        page = 1
        while True:
            page_arguments = dict(arguments)
            fields = set(_format_fields(operation.path))
            if "page" in fields:
                page_arguments["page"] = page
            if "size" in fields:
                page_arguments.setdefault("size", 100)
            payload = self._dispatch(capability, operation, page_arguments)
            self._validate_response(operation, payload)
            pages.append(self._normalize(capability, operation, payload))
            if not _has_next(payload):
                break
            page += 1
        first = pages[0]
        return replace(
            first,
            records=tuple(record for batch in pages for record in batch.records),
            capability_revision=capability.revision,
        )

    def _dispatch(
        self,
        capability: SourceCapability,
        operation: OperationContract,
        arguments: dict[str, object],
    ) -> object:
        try:
            path_template = operation.path.split("?", 1)[0]
            fields = set(_format_fields(path_template))
            encoded_arguments = dict(arguments)
            encoded_arguments.update(
                {field: _encode_path_segment(arguments[field]) for field in fields}
            )
            path = operation.path.format(**encoded_arguments)
        except (KeyError, ValueError) as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH, "Path arguments are invalid"
            ) from exc
        url = _join_capability_url(capability, path)
        transport = self._transports.get(capability.transport)
        if transport is None:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_MISSING, "Transport unavailable"
            )
        headers = self._headers(capability, operation, url, arguments, transport)
        attempts = 0
        current_url = url
        while True:
            try:
                response = transport.request(
                    capability=capability,
                    operation=operation,
                    url=current_url,
                    arguments=arguments,
                    headers=headers,
                )
            except httpx.RequestError:
                if self._retry_allowed(operation, arguments) and attempts < 1:
                    attempts += 1
                    continue
                if operation.safe:
                    raise DataPlaneError(
                        DataPlaneErrorCode.PROVIDER_UNAVAILABLE,
                        "Safe provider request failed",
                    )
                raise
            if not isinstance(response, httpx.Response):
                return response
            if response.status_code in _REDIRECT_STATUSES:
                if not operation.safe:
                    raise DataPlaneError(
                        DataPlaneErrorCode.PROVIDER_UNAVAILABLE,
                        "Unsafe operation redirect rejected",
                    )
                if attempts >= 5:
                    raise DataPlaneError(
                        DataPlaneErrorCode.PROVIDER_UNAVAILABLE, "Too many redirects"
                    )
                location = response.headers.get("location")
                if not location:
                    raise DataPlaneError(
                        DataPlaneErrorCode.PROVIDER_UNAVAILABLE,
                        "Redirect has no location",
                    )
                current_url = urljoin(current_url, location)
                self._validate_request_url(capability, current_url)
                attempts += 1
                continue
            if (
                response.status_code in _RETRY_STATUSES
                and self._retry_allowed(operation, arguments)
                and attempts < 1
            ):
                attempts += 1
                self._sleep(_retry_after(response))
                continue
            if response.status_code >= 400:
                code = (
                    DataPlaneErrorCode.RATE_LIMITED
                    if response.status_code == 429
                    else DataPlaneErrorCode.PROVIDER_UNAVAILABLE
                )
                raise DataPlaneError(code, "Provider request failed")
            try:
                return response.json()
            except json.JSONDecodeError as exc:
                raise DataPlaneError(
                    DataPlaneErrorCode.SCHEMA_MISMATCH, "Provider response is not JSON"
                ) from exc

    def _headers(
        self,
        capability: SourceCapability,
        operation: OperationContract,
        url: str,
        arguments: dict[str, object],
        transport: ExecutionTransport,
    ) -> dict[str, str]:
        headers = {"User-Agent": "OpenJarvis/direct-execution"}
        if capability.credential_ref:
            try:
                section, key = capability.credential_ref.split(":", 1)
            except ValueError as exc:
                raise DataPlaneError(
                    DataPlaneErrorCode.AUTHENTICATION_REQUIRED,
                    "Credential reference is invalid",
                ) from exc
            credential = get_tool_credential(section, key)
            if not credential:
                raise DataPlaneError(
                    DataPlaneErrorCode.AUTHENTICATION_REQUIRED,
                    "Credential is unavailable",
                )
            if capability.auth_mode == "bearer":
                headers["Authorization"] = f"Bearer {credential}"
            elif capability.auth_mode == "header":
                headers[key] = credential
        if operation.idempotency_header:
            value = arguments.get(operation.idempotency_header)
            if isinstance(value, str) and value:
                headers[operation.idempotency_header] = value
        if operation.csrf_cookie:
            lookup = getattr(transport, "csrf_token", None)
            token = lookup(url, operation.csrf_cookie) if callable(lookup) else None
            if not token or not operation.csrf_header:
                raise DataPlaneError(
                    DataPlaneErrorCode.AUTHENTICATION_EXPIRED,
                    "CSRF token is unavailable",
                )
            headers[operation.csrf_header] = token
        return headers

    @staticmethod
    def _retry_allowed(
        operation: OperationContract, arguments: dict[str, object]
    ) -> bool:
        if operation.method.upper() in {"GET", "HEAD"}:
            return True
        key = arguments.get(operation.idempotency_header)
        return bool(operation.idempotency_header and isinstance(key, str) and key)

    def _normalize(
        self,
        capability: SourceCapability,
        operation: OperationContract,
        payload: object,
    ) -> NormalizedBatch:
        adapter = self._normalization_adapter(capability)
        if adapter is None:
            return _generic_batch(capability, operation, payload)
        try:
            batch = adapter.normalize(operation.resource_type, payload)  # type: ignore[union-attr]
        except (TypeError, ValueError, KeyError) as exc:
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH, "Response normalization failed"
            ) from exc
        if (
            batch.source_id != capability.source_id
            or batch.resource_type != operation.resource_type
        ):
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH, "Normalizer returned wrong resource"
            )
        return replace(batch, capability_revision=capability.revision)

    def _validate_response(self, operation: OperationContract, payload: object) -> None:
        _validate_json_schema(operation.response_schema, payload, "Response")

    def _validate_request(
        self, operation: OperationContract, arguments: dict[str, object]
    ) -> None:
        _validate_json_schema(operation.request_schema, arguments, "Request")

    def _validate_capability_origin(self, capability: SourceCapability) -> None:
        self._validate_request_url(capability, capability.base_url)

    def _validate_request_url(self, capability: SourceCapability, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED, "Unsafe execution URL"
            )
        if _origin(url) != _origin(capability.origin):
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED, "Execution origin changed"
            )
        if check_ssrf(url):
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Execution URL failed SSRF validation",
            )

    def _save_receipt(
        self, receipt: ExecutionReceipt, verification_claim: dict[str, str]
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO execution_receipts (
                    receipt_id, source_id, operation, capability_revision, request_hash,
                    status, provider_ref, normalized_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.source_id,
                    receipt.operation,
                    receipt.capability_revision,
                    receipt.request_hash,
                    receipt.status.value,
                    receipt.provider_ref,
                    json.dumps(
                        {
                            "request_hash": receipt.request_hash,
                            "candidate": receipt.normalized,
                            "verification_claim": verification_claim,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    receipt.created_at,
                ),
            )

    def _publish(self, event_type: EventType, **data: object) -> None:
        if self._bus is not None:
            self._bus.publish(event_type, dict(data))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _format_fields(path: str) -> tuple[str, ...]:
    return tuple(name for _, name, _, _ in Formatter().parse(path) if name)


def _join_capability_url(capability: SourceCapability, path: str) -> str:
    if not path.startswith("/") or ".." in path.split("/"):
        raise DataPlaneError(
            DataPlaneErrorCode.SCHEMA_MISMATCH, "Operation path is unsafe"
        )
    if capability.transport is TransportKind.GRAPHQL:
        if path != urlsplit(capability.base_url).path:
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH,
                "GraphQL operation path does not match its endpoint",
            )
        url = capability.base_url
    else:
        url = f"{capability.base_url.rstrip('/')}{path}"
    parsed = urlsplit(url)
    if parsed.query or parsed.fragment:
        # Query templates are allowed, fragments never are.
        if parsed.fragment:
            raise DataPlaneError(
                DataPlaneErrorCode.SCHEMA_MISMATCH, "Operation path has fragment"
            )
    return url


def _request_hash(
    capability: SourceCapability, operation: str, arguments: dict[str, object]
) -> str:
    value = json.dumps(
        {
            "revision": capability.revision,
            "operation": operation,
            "arguments": arguments,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _sync_receipt(
    source_id: str,
    resources: list[str],
    status: ReceiptStatus,
    started_at: str,
    error_code: str = "",
    *,
    snapshot_refs: tuple[SnapshotRef, ...] = (),
    resource_count: int = 0,
) -> SyncReceipt:
    return SyncReceipt(
        receipt_id=str(uuid.uuid4()),
        source_id=source_id,
        resources=tuple(resources),
        status=status,
        snapshot_refs=snapshot_refs,
        resource_count=resource_count,
        started_at=started_at,
        completed_at=_now(),
        error_code=error_code,
    )


def _has_next(payload: object) -> bool:
    if isinstance(payload, dict):
        if payload.get("hasNext") is True:
            return True
        result = payload.get("result")
        return isinstance(result, dict) and result.get("hasNext") is True
    return False


def _provider_ref(batch: NormalizedBatch) -> str:
    if not batch.records:
        return ""
    payload = batch.records[0].payload
    order_ref = payload.get("order")
    return (
        str(order_ref)
        if isinstance(order_ref, str) and order_ref
        else batch.records[0].resource_id
    )


def _public_receipt(receipt: ExecutionReceipt) -> ExecutionReceipt:
    return replace(receipt, normalized={})


def _candidate_batch(receipt: ExecutionReceipt) -> NormalizedBatch:
    stored_hash = receipt.normalized.get("request_hash")
    candidate = receipt.normalized.get("candidate")
    if stored_hash != receipt.request_hash or not isinstance(candidate, dict):
        raise DataPlaneError(
            DataPlaneErrorCode.VERIFICATION_FAILED,
            "Receipt candidate provenance is invalid",
        )
    try:
        return NormalizedBatch.from_dict(candidate)
    except (KeyError, TypeError, ValueError) as exc:
        raise DataPlaneError(
            DataPlaneErrorCode.VERIFICATION_FAILED,
            "Receipt candidate is invalid",
        ) from exc


def _candidate_claim(receipt: ExecutionReceipt) -> dict[str, str]:
    claim = receipt.normalized.get("verification_claim")
    if not _is_private_claim(claim):
        raise DataPlaneError(
            DataPlaneErrorCode.VERIFICATION_FAILED,
            "Receipt verification claim is invalid",
        )
    return claim


def _is_private_claim(value: object) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str) or not item:
            return False
        if key.endswith("_hash"):
            if not item.startswith("sha256:"):
                return False
        elif not key.endswith("_ref"):
            return False
    return True


def _candidate_matches_observation(
    candidate: NormalizedBatch,
    observed: NormalizedBatch,
    provider_ref: str,
) -> bool:
    if not provider_ref or not _observes_ref(observed, provider_ref):
        return False
    if candidate.resource_type == "payment":
        return any(
            record.payload.get("order") == provider_ref for record in candidate.records
        )
    return any(record.resource_id == provider_ref for record in candidate.records)


def _generic_batch(
    capability: SourceCapability,
    operation: OperationContract,
    payload: object,
) -> NormalizedBatch:
    value = payload
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        data = value["data"]
        query_name = operation.name.removeprefix("graphql.")
        value = data.get(query_name, data)
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        value = value["items"]
    values = value if isinstance(value, list) else [value]
    records: list[ResourceRecord] = []
    for item in values:
        record = dict(item) if isinstance(item, dict) else {"value": item}
        record_id = record.get("id", record.get("slug"))
        if not isinstance(record_id, str) or not record_id:
            record_id = hashlib.sha256(
                json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        records.append(ResourceRecord(resource_id=record_id, payload=record))
    return NormalizedBatch(
        source_id=capability.source_id,
        resource_type=operation.resource_type,
        records=tuple(records),
        synced_at=_now(),
        capability_revision=capability.revision,
        provenance=("generic_structured_transport",),
    )


def _validate_json_schema(schema: dict[str, object], value: object, label: str) -> None:
    if not schema:
        return
    if not _matches_schema(schema, value):
        raise DataPlaneError(
            DataPlaneErrorCode.SCHEMA_MISMATCH,
            f"{label} does not match its contract schema",
        )


def _matches_schema(schema: dict[str, object], value: object) -> bool:
    schema_type = schema.get("type")
    if isinstance(schema_type, str) and not _matches_type(schema_type, value):
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list) and any(key not in value for key in required):
            return False
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            if schema.get("additionalProperties") is False and any(
                key not in properties for key in value
            ):
                return False
            for key, child in properties.items():
                if (
                    key in value
                    and isinstance(child, dict)
                    and not _matches_schema(child, value[key])
                ):
                    return False
    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict) and any(
            not _matches_schema(items, item) for item in value
        ):
            return False
    return True


def _matches_type(schema_type: str, value: object) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }.get(schema_type, True)


def _redact_batch(batch: NormalizedBatch) -> NormalizedBatch:
    return replace(
        batch,
        records=tuple(
            replace(record, payload=_redact_data(record.payload))
            for record in batch.records
        ),
    )


def _redact_data(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _redact_data(nested)
            for key, nested in value.items()
            if not any(
                part in str(key).casefold()
                for part in ("authorization", "cookie", "token", "secret")
            )
        }
    if isinstance(value, list):
        return [_redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_data(item) for item in value)
    return value


def _path_arguments(path: str, provider_ref: str) -> dict[str, object]:
    fields = _format_fields(path)
    if not fields:
        return {}
    return {field: provider_ref for field in fields}


def _encode_path_segment(value: object) -> str:
    if not isinstance(value, str):
        raise DataPlaneError(
            DataPlaneErrorCode.SCHEMA_MISMATCH,
            "Path argument is not a raw identifier",
        )
    segment = value
    if (
        not segment
        or segment in {".", ".."}
        or any(character in segment for character in ("/", "\\", "?", "#", "%"))
        or any(ord(character) < 32 or ord(character) == 127 for character in segment)
    ):
        raise DataPlaneError(
            DataPlaneErrorCode.SCHEMA_MISMATCH,
            "Path argument is not a single segment",
        )
    return quote(segment, safe="")


def _observes_ref(batch: NormalizedBatch, provider_ref: str) -> bool:
    return any(record.resource_id == provider_ref for record in batch.records)


def _retry_after(response: httpx.Response) -> float:
    value = response.headers.get("retry-after")
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


def _is_expired(expires_at: str) -> bool:
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= datetime.now(timezone.utc)


__all__ = [
    "DirectExecutionEngine",
    "ExecutionTransport",
    "HttpExecutionTransport",
    "McpExecutionTransport",
]
