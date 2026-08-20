"""Immutable contracts for the Universal Data Plane."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, TypeAlias

SourceId: TypeAlias = str
ResourceType: TypeAlias = str
OperationName: TypeAlias = str
ReceiptId: TypeAlias = str


class TrustState(str, Enum):
    CANDIDATE = "candidate"
    QUARANTINED = "quarantined"
    READ_VALIDATED = "read_validated"
    WRITE_VALIDATED = "write_validated"
    REVOKED = "revoked"


class TransportKind(str, Enum):
    REST = "rest"
    GRAPHQL = "graphql"
    PROVIDER = "provider"
    MCP = "mcp"
    EMBEDDED = "embedded"


class ConsistencyMode(str, Enum):
    CACHED = "cached"
    REFRESH_IF_STALE = "refresh_if_stale"
    LIVE = "live"


class ReceiptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


def _to_data(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_to_data(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_data(value[key]) for key in sorted(value)}
    if hasattr(value, "to_dict"):
        return value.to_dict()  # type: ignore[union-attr]
    return value


@dataclass(frozen=True, slots=True)
class SourceRef:
    value: str

    def to_dict(self) -> dict[str, object]:
        return {"value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SourceRef:
        return cls(value=str(data["value"]))


@dataclass(frozen=True, slots=True)
class DiscoveryConstraints:
    budget_seconds: float = 60.0
    browser_fallback: bool = False
    require_https: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "budget_seconds": self.budget_seconds,
            "browser_fallback": self.browser_fallback,
            "require_https": self.require_https,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> DiscoveryConstraints:
        return cls(
            budget_seconds=float(data.get("budget_seconds", 60.0)),
            browser_fallback=bool(data.get("browser_fallback", False)),
            require_https=bool(data.get("require_https", True)),
        )


@dataclass(frozen=True, slots=True)
class OperationContract:
    name: str
    method: str
    path: str
    resource_type: str
    trust: TrustState
    safe: bool
    request_schema: dict[str, object] = field(default_factory=dict)
    response_schema: dict[str, object] = field(default_factory=dict)
    verify_operation: str = ""
    idempotency_header: str = ""
    csrf_cookie: str = ""
    csrf_header: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "method": self.method,
            "path": self.path,
            "resource_type": self.resource_type,
            "trust": self.trust.value,
            "safe": self.safe,
            "request_schema": _to_data(self.request_schema),
            "response_schema": _to_data(self.response_schema),
            "verify_operation": self.verify_operation,
            "idempotency_header": self.idempotency_header,
            "csrf_cookie": self.csrf_cookie,
            "csrf_header": self.csrf_header,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> OperationContract:
        return cls(
            name=str(data["name"]),
            method=str(data["method"]),
            path=str(data["path"]),
            resource_type=str(data["resource_type"]),
            trust=TrustState(str(data["trust"])),
            safe=bool(data["safe"]),
            request_schema=dict(data.get("request_schema", {})),
            response_schema=dict(data.get("response_schema", {})),
            verify_operation=str(data.get("verify_operation", "")),
            idempotency_header=str(data.get("idempotency_header", "")),
            csrf_cookie=str(data.get("csrf_cookie", "")),
            csrf_header=str(data.get("csrf_header", "")),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    kind: str
    source_url: str
    method: str = "GET"
    status_code: int = 0
    content_type: str = ""
    payload: dict[str, object] = field(default_factory=dict)
    body_hash: str = ""
    provenance: str = ""
    untrusted: bool = True
    observed_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_url": self.source_url,
            "method": self.method,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "payload": _to_data(self.payload),
            "body_hash": self.body_hash,
            "provenance": self.provenance,
            "untrusted": self.untrusted,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> DiscoveryEvidence:
        return cls(
            kind=str(data["kind"]),
            source_url=str(data["source_url"]),
            method=str(data.get("method", "GET")),
            status_code=int(data.get("status_code", 0)),
            content_type=str(data.get("content_type", "")),
            payload=dict(data.get("payload", {})),
            body_hash=str(data.get("body_hash", "")),
            provenance=str(data.get("provenance", "")),
            untrusted=bool(data.get("untrusted", True)),
            observed_at=str(data.get("observed_at", "")),
        )


@dataclass(frozen=True, slots=True)
class SourceCapability:
    source_id: str
    provider: str
    origin: str
    base_url: str
    auth_mode: str
    credential_ref: str
    transport: TransportKind
    operations: dict[str, OperationContract]
    fingerprint: str
    schema_hash: str
    evidence: tuple[DiscoveryEvidence, ...]
    validated_at: str
    expires_at: str
    revision: int
    resource_mappings: dict[str, dict[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "provider": self.provider,
            "origin": self.origin,
            "base_url": self.base_url,
            "auth_mode": self.auth_mode,
            "credential_ref": self.credential_ref,
            "transport": self.transport.value,
            "operations": _to_data(self.operations),
            "fingerprint": self.fingerprint,
            "schema_hash": self.schema_hash,
            "evidence": _to_data(self.evidence),
            "validated_at": self.validated_at,
            "expires_at": self.expires_at,
            "revision": self.revision,
            "resource_mappings": _to_data(self.resource_mappings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SourceCapability:
        operations = dict(data["operations"])
        evidence = tuple(
            DiscoveryEvidence.from_dict(dict(item)) for item in data["evidence"]
        )
        return cls(
            source_id=str(data["source_id"]),
            provider=str(data["provider"]),
            origin=str(data["origin"]),
            base_url=str(data["base_url"]),
            auth_mode=str(data["auth_mode"]),
            credential_ref=str(data["credential_ref"]),
            transport=TransportKind(str(data["transport"])),
            operations={
                name: OperationContract.from_dict(dict(operation))
                for name, operation in operations.items()
            },
            fingerprint=str(data["fingerprint"]),
            schema_hash=str(data["schema_hash"]),
            evidence=evidence,
            validated_at=str(data["validated_at"]),
            expires_at=str(data["expires_at"]),
            revision=int(data["revision"]),
            resource_mappings={
                key: dict(value)
                for key, value in dict(data.get("resource_mappings", {})).items()
            },
        )


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    source_id: str
    state: TrustState
    capability: SourceCapability | None = None
    evidence: tuple[DiscoveryEvidence, ...] = ()
    elapsed_ms: int = 0
    browser_actions: int = 0
    cache_hit: bool = False
    error_code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "state": self.state.value,
            "capability": _to_data(self.capability),
            "evidence": _to_data(self.evidence),
            "elapsed_ms": self.elapsed_ms,
            "browser_actions": self.browser_actions,
            "cache_hit": self.cache_hit,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> DiscoveryResult:
        capability = data.get("capability")
        return cls(
            source_id=str(data["source_id"]),
            state=TrustState(str(data["state"])),
            capability=(
                SourceCapability.from_dict(dict(capability))
                if capability is not None
                else None
            ),
            evidence=tuple(
                DiscoveryEvidence.from_dict(dict(item))
                for item in data.get("evidence", [])
            ),
            elapsed_ms=int(data.get("elapsed_ms", 0)),
            browser_actions=int(data.get("browser_actions", 0)),
            cache_hit=bool(data.get("cache_hit", False)),
            error_code=str(data.get("error_code", "")),
        )


@dataclass(frozen=True, slots=True)
class ResourceRecord:
    resource_id: str
    payload: dict[str, object]
    provenance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "payload": _to_data(self.payload),
            "provenance": _to_data(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ResourceRecord:
        return cls(
            resource_id=str(data["resource_id"]),
            payload=dict(data["payload"]),
            provenance=tuple(str(item) for item in data.get("provenance", [])),
        )


@dataclass(frozen=True, slots=True)
class NormalizedBatch:
    source_id: str
    resource_type: str
    records: tuple[ResourceRecord, ...]
    synced_at: str
    capability_revision: int = 0
    provenance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "resource_type": self.resource_type,
            "records": _to_data(self.records),
            "synced_at": self.synced_at,
            "capability_revision": self.capability_revision,
            "provenance": _to_data(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> NormalizedBatch:
        return cls(
            source_id=str(data["source_id"]),
            resource_type=str(data["resource_type"]),
            records=tuple(
                ResourceRecord.from_dict(dict(item)) for item in data["records"]
            ),
            synced_at=str(data["synced_at"]),
            capability_revision=int(data.get("capability_revision", 0)),
            provenance=tuple(str(item) for item in data.get("provenance", [])),
        )


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    source_id: str
    resource_type: str
    version: int

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "resource_type": self.resource_type,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SnapshotRef:
        return cls(
            source_id=str(data["source_id"]),
            resource_type=str(data["resource_type"]),
            version=int(data["version"]),
        )


@dataclass(frozen=True, slots=True)
class ResourceRef:
    source_id: str
    resource_type: str
    resource_id: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ResourceRef:
        return cls(
            source_id=str(data["source_id"]),
            resource_type=str(data["resource_type"]),
            resource_id=str(data["resource_id"]),
        )


@dataclass(frozen=True, slots=True)
class StructuredSnapshot:
    ref: SnapshotRef
    records: tuple[ResourceRecord, ...]
    synced_at: str
    capability_revision: int
    provenance: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref.to_dict(),
            "records": _to_data(self.records),
            "synced_at": self.synced_at,
            "capability_revision": self.capability_revision,
            "provenance": _to_data(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StructuredSnapshot:
        return cls(
            ref=SnapshotRef.from_dict(dict(data["ref"])),
            records=tuple(
                ResourceRecord.from_dict(dict(item)) for item in data["records"]
            ),
            synced_at=str(data["synced_at"]),
            capability_revision=int(data["capability_revision"]),
            provenance=tuple(str(item) for item in data.get("provenance", [])),
        )


@dataclass(frozen=True, slots=True)
class SnapshotCommit:
    ref: SnapshotRef
    version: int
    resource_count: int
    synced_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ref": self.ref.to_dict(),
            "version": self.version,
            "resource_count": self.resource_count,
            "synced_at": self.synced_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SnapshotCommit:
        return cls(
            ref=SnapshotRef.from_dict(dict(data["ref"])),
            version=int(data["version"]),
            resource_count=int(data["resource_count"]),
            synced_at=str(data["synced_at"]),
        )


@dataclass(frozen=True, slots=True)
class SnapshotDiff:
    older: SnapshotRef
    newer: SnapshotRef
    added_ids: tuple[str, ...] = ()
    changed_ids: tuple[str, ...] = ()
    removed_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "older": self.older.to_dict(),
            "newer": self.newer.to_dict(),
            "added_ids": _to_data(self.added_ids),
            "changed_ids": _to_data(self.changed_ids),
            "removed_ids": _to_data(self.removed_ids),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SnapshotDiff:
        return cls(
            older=SnapshotRef.from_dict(dict(data["older"])),
            newer=SnapshotRef.from_dict(dict(data["newer"])),
            added_ids=tuple(str(item) for item in data.get("added_ids", [])),
            changed_ids=tuple(str(item) for item in data.get("changed_ids", [])),
            removed_ids=tuple(str(item) for item in data.get("removed_ids", [])),
        )


@dataclass(frozen=True, slots=True)
class StructuredQuery:
    source_id: str
    resource_type: str
    filters: dict[str, object] = field(default_factory=dict)
    consistency: ConsistencyMode = ConsistencyMode.CACHED
    max_age_seconds: int = 0
    limit: int = 100

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "resource_type": self.resource_type,
            "filters": _to_data(self.filters),
            "consistency": self.consistency.value,
            "max_age_seconds": self.max_age_seconds,
            "limit": self.limit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StructuredQuery:
        return cls(
            source_id=str(data["source_id"]),
            resource_type=str(data["resource_type"]),
            filters=dict(data.get("filters", {})),
            consistency=ConsistencyMode(str(data.get("consistency", "cached"))),
            max_age_seconds=int(data.get("max_age_seconds", 0)),
            limit=int(data.get("limit", 100)),
        )


@dataclass(frozen=True, slots=True)
class StructuredResult:
    items: tuple[ResourceRecord, ...]
    version: int
    synced_at: str
    stale: bool = False
    warning: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "items": _to_data(self.items),
            "version": self.version,
            "synced_at": self.synced_at,
            "stale": self.stale,
            "warning": self.warning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> StructuredResult:
        return cls(
            items=tuple(
                ResourceRecord.from_dict(dict(item)) for item in data["items"]
            ),
            version=int(data["version"]),
            synced_at=str(data["synced_at"]),
            stale=bool(data.get("stale", False)),
            warning=str(data.get("warning", "")),
        )


@dataclass(frozen=True, slots=True)
class SyncReceipt:
    receipt_id: str
    source_id: str
    resources: tuple[str, ...]
    status: ReceiptStatus
    snapshot_refs: tuple[SnapshotRef, ...] = ()
    browser_actions: int = 0
    resource_count: int = 0
    started_at: str = ""
    completed_at: str = ""
    error_code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "source_id": self.source_id,
            "resources": _to_data(self.resources),
            "status": self.status.value,
            "snapshot_refs": _to_data(self.snapshot_refs),
            "browser_actions": self.browser_actions,
            "resource_count": self.resource_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SyncReceipt:
        return cls(
            receipt_id=str(data["receipt_id"]),
            source_id=str(data["source_id"]),
            resources=tuple(str(item) for item in data["resources"]),
            status=ReceiptStatus(str(data["status"])),
            snapshot_refs=tuple(
                SnapshotRef.from_dict(dict(item))
                for item in data.get("snapshot_refs", [])
            ),
            browser_actions=int(data.get("browser_actions", 0)),
            resource_count=int(data.get("resource_count", 0)),
            started_at=str(data.get("started_at", "")),
            completed_at=str(data.get("completed_at", "")),
            error_code=str(data.get("error_code", "")),
        )


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    receipt_id: str
    source_id: str
    operation: str
    capability_revision: int
    request_hash: str
    status: ReceiptStatus
    provider_ref: str = ""
    normalized: dict[str, object] = field(default_factory=dict)
    created_at: str = ""
    error_code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "source_id": self.source_id,
            "operation": self.operation,
            "capability_revision": self.capability_revision,
            "request_hash": self.request_hash,
            "status": self.status.value,
            "provider_ref": self.provider_ref,
            "normalized": _to_data(self.normalized),
            "created_at": self.created_at,
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ExecutionReceipt:
        return cls(
            receipt_id=str(data["receipt_id"]),
            source_id=str(data["source_id"]),
            operation=str(data["operation"]),
            capability_revision=int(data["capability_revision"]),
            request_hash=str(data["request_hash"]),
            status=ReceiptStatus(str(data["status"])),
            provider_ref=str(data.get("provider_ref", "")),
            normalized=dict(data.get("normalized", {})),
            created_at=str(data.get("created_at", "")),
            error_code=str(data.get("error_code", "")),
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    receipt_id: str
    operation: str
    observed: bool
    status: ReceiptStatus
    snapshot_refs: tuple[SnapshotRef, ...] = ()
    error_code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id,
            "operation": self.operation,
            "observed": self.observed,
            "status": self.status.value,
            "snapshot_refs": _to_data(self.snapshot_refs),
            "error_code": self.error_code,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> VerificationResult:
        return cls(
            receipt_id=str(data["receipt_id"]),
            operation=str(data["operation"]),
            observed=bool(data["observed"]),
            status=ReceiptStatus(str(data["status"])),
            snapshot_refs=tuple(
                SnapshotRef.from_dict(dict(item))
                for item in data.get("snapshot_refs", [])
            ),
            error_code=str(data.get("error_code", "")),
        )


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: bool
    provider: str = ""
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "matched": self.matched,
            "provider": self.provider,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> MatchResult:
        return cls(
            matched=bool(data["matched"]),
            provider=str(data.get("provider", "")),
            confidence=float(data.get("confidence", 0.0)),
            reason=str(data.get("reason", "")),
        )


class StructuredSourceAdapter(Protocol):
    """Provider/format adapter for structured source evidence."""

    def match(self, evidence: DiscoveryEvidence) -> MatchResult: ...

    def compile(self, evidence: DiscoveryEvidence) -> SourceCapability: ...

    def normalize(
        self, resource_type: ResourceType, payload: object
    ) -> NormalizedBatch: ...


__all__ = [
    "ConsistencyMode",
    "DiscoveryConstraints",
    "DiscoveryEvidence",
    "DiscoveryResult",
    "ExecutionReceipt",
    "MatchResult",
    "NormalizedBatch",
    "OperationContract",
    "OperationName",
    "ReceiptId",
    "ReceiptStatus",
    "ResourceRecord",
    "ResourceRef",
    "ResourceType",
    "SnapshotCommit",
    "SnapshotDiff",
    "SnapshotRef",
    "SourceCapability",
    "SourceId",
    "SourceRef",
    "StructuredQuery",
    "StructuredResult",
    "StructuredSnapshot",
    "StructuredSourceAdapter",
    "SyncReceipt",
    "TransportKind",
    "TrustState",
    "VerificationResult",
]
