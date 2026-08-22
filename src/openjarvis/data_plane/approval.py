"""Exact operator trust and one-time user approval for provider mutations."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field, replace

from openjarvis.data_plane.types import SourceCapability, TrustState
from openjarvis.tools.approval_store import (
    STATUS_EXECUTED,
    STATUS_UNKNOWN,
    TIER_HIGH,
    ApprovalStore,
    PendingAction,
)

_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# The operator must see what they are authorizing, so the preview carries the
# actual argument values -- bounded so a hostile provider argument cannot bloat
# the persisted payload, and stripped of control characters so it cannot forge
# structure in whatever surface renders it.
_PREVIEW_MAX_TEXT = 120
_PREVIEW_MAX_ITEMS = 20
_PREVIEW_MAX_DEPTH = 3


def _preview_text(value: str) -> str:
    text = _CONTROL.sub(" ", value)
    return text if len(text) <= _PREVIEW_MAX_TEXT else text[:_PREVIEW_MAX_TEXT] + "..."


def _preview_value(value: object, depth: int = 0) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _preview_text(value)
    if depth >= _PREVIEW_MAX_DEPTH:
        return "<nested>"
    if isinstance(value, dict):
        return {
            _preview_text(str(key)): _preview_value(item, depth + 1)
            for key, item in list(value.items())[:_PREVIEW_MAX_ITEMS]
        }
    if isinstance(value, (list, tuple)):
        return [
            _preview_value(item, depth + 1) for item in list(value)[:_PREVIEW_MAX_ITEMS]
        ]
    return "<omitted>"


class ApprovalError(Exception):
    """A stable, Agent-safe approval failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrustedWrite:
    provider: str
    operation: str
    fingerprint: str


def parse_trusted_write_operations(configured: str) -> tuple[TrustedWrite, ...]:
    """Parse exact comma-separated provider write identities."""
    if not isinstance(configured, str):
        raise ValueError("trusted write operations must be a string")
    if not configured:
        return ()
    entries: list[TrustedWrite] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in configured.split(","):
        if not raw or raw != raw.strip() or any(char.isspace() for char in raw):
            raise ValueError("trusted write entries may not contain whitespace")
        parts = raw.split(":", 2)
        if len(parts) != 3:
            raise ValueError("trusted write entry is malformed")
        provider, operation, fingerprint = parts
        if (
            not _IDENTIFIER.fullmatch(provider)
            or not _IDENTIFIER.fullmatch(operation)
            or not _FINGERPRINT.fullmatch(fingerprint)
            or "*" in raw
        ):
            raise ValueError("trusted write entry is not exact")
        identity = (provider, operation, fingerprint)
        if identity in seen:
            raise ValueError("duplicate trusted write entry")
        seen.add(identity)
        entries.append(TrustedWrite(*identity))
    return tuple(entries)


def promote_trusted_writes(
    capability: SourceCapability, configured: str | tuple[TrustedWrite, ...]
) -> SourceCapability:
    """Apply exact operator trust and quarantine every unmatched write."""
    trusted = (
        parse_trusted_write_operations(configured)
        if isinstance(configured, str)
        else configured
    )
    identities = {
        (entry.provider, entry.operation, entry.fingerprint) for entry in trusted
    }
    operations = dict(capability.operations)
    for name, operation in operations.items():
        if operation.safe:
            continue
        identity = (capability.provider, name, capability.fingerprint)
        operations[name] = replace(
            operation,
            trust=(
                TrustState.WRITE_VALIDATED
                if identity in identities
                else TrustState.QUARANTINED
            ),
        )
    return replace(capability, operations=operations)


def execution_request_hash(
    source_id: str,
    operation: str,
    arguments: dict[str, object],
    capability_revision: int,
) -> str:
    try:
        payload = json.dumps(
            {
                "source_id": source_id,
                "operation": operation,
                "arguments": arguments,
                "capability_revision": capability_revision,
            },
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ApprovalError("approval_arguments_not_serializable") from exc
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(slots=True)
class ExecutionGrant:
    """Memory-only, single-use authority for one exact provider request."""

    approval_id: str
    source_id: str
    operation: str
    capability_revision: int
    request_hash: str
    _store: ApprovalStore = field(repr=False)
    _used: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def consume(
        self,
        source_id: str,
        operation: str,
        arguments: dict[str, object],
        capability_revision: int,
    ) -> None:
        """Validate and burn the grant immediately before provider dispatch."""
        actual_hash = execution_request_hash(
            source_id, operation, arguments, capability_revision
        )
        with self._lock:
            if self._used:
                raise ApprovalError("approval_grant_consumed")
            self._used = True
            if (
                self.source_id != source_id
                or self.operation != operation
                or self.capability_revision != capability_revision
                or self.request_hash != actual_hash
            ):
                self._store.finish_execution(
                    self.approval_id,
                    request_hash=self.request_hash,
                    status=STATUS_EXECUTED,
                )
                raise ApprovalError("approval_grant_mismatch")

    def reject(self) -> None:
        """Burn a claimed grant rejected before dispatch."""
        with self._lock:
            if self._used:
                return
            self._used = True
        self.finish(STATUS_EXECUTED)

    def finish(self, status: str) -> None:
        if status not in {STATUS_EXECUTED, STATUS_UNKNOWN}:
            raise ValueError("invalid execution approval terminal status")
        self._store.finish_execution(
            self.approval_id,
            request_hash=self.request_hash,
            status=status,
        )


class ExecutionApprovalGate:
    """Prepare persisted proposals and mint exact one-time grants after approval."""

    def __init__(self, store: ApprovalStore, *, owns_store: bool = False) -> None:
        self._store = store
        self._owns_store = owns_store

    def prepare(
        self,
        source_id: str,
        operation: str,
        arguments: dict[str, object],
        capability_revision: int,
    ) -> PendingAction:
        request_hash = execution_request_hash(
            source_id, operation, arguments, capability_revision
        )
        return self._store.queue_action(
            action_type="source_execute",
            description=f"Execute {operation} for {source_id}",
            payload={
                "source_id": source_id,
                "operation": operation,
                "preview": _preview_value(dict(sorted(arguments.items()))),
                "capability_revision": capability_revision,
                "request_hash": request_hash,
            },
            permission_key=f"source_execute:{source_id}:{operation}",
            tier=TIER_HIGH,
        )

    def authorize(self, approval_id: str, expected_hash: str) -> ExecutionGrant:
        claim = self._store.claim_execution(
            approval_id,
            action_type="source_execute",
            request_hash=expected_hash,
        )
        if claim.status != "claimed" or claim.action is None:
            raise ApprovalError(claim.reason or "approval_not_approved")
        payload = claim.action.payload
        try:
            source_id = payload["source_id"]
            operation = payload["operation"]
            revision = payload["capability_revision"]
            request_hash = payload["request_hash"]
        except KeyError as exc:
            self._store.finish_execution(
                approval_id, request_hash=expected_hash, status=STATUS_EXECUTED
            )
            raise ApprovalError("approval_payload_invalid") from exc
        if (
            not isinstance(source_id, str)
            or not isinstance(operation, str)
            or type(revision) is not int
            or not isinstance(request_hash, str)
            or request_hash != expected_hash
        ):
            self._store.finish_execution(
                approval_id, request_hash=expected_hash, status=STATUS_EXECUTED
            )
            raise ApprovalError("approval_payload_invalid")
        return ExecutionGrant(
            approval_id=approval_id,
            source_id=source_id,
            operation=operation,
            capability_revision=revision,
            request_hash=request_hash,
            _store=self._store,
        )

    def close(self) -> None:
        if self._owns_store:
            self._store.close()


__all__ = [
    "ApprovalError",
    "ExecutionApprovalGate",
    "ExecutionGrant",
    "TrustedWrite",
    "execution_request_hash",
    "parse_trusted_write_operations",
    "promote_trusted_writes",
]
