"""SQLite persistence for discovered source capabilities."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

from openjarvis.data_plane.types import SourceCapability, TrustState

_SENSITIVE_KEY_PARTS = ("authorization", "cookie", "token", "secret")

_CREATE_SCHEMA_TABLE = """\
CREATE TABLE IF NOT EXISTS data_plane_schema (
    version INTEGER NOT NULL
)
"""

_CREATE_CAPABILITY_TABLE = """\
CREATE TABLE IF NOT EXISTS source_capabilities (
    source_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    origin TEXT NOT NULL,
    base_url TEXT NOT NULL,
    revision INTEGER NOT NULL,
    trust_json TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    demotion_reason TEXT NOT NULL DEFAULT ''
)
"""


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _reject_sensitive_keys(value: object) -> None:
    """Reject secret-bearing keys in capability-provided mappings."""
    if isinstance(value, dict):
        for key, nested in value.items():
            key_text = str(key).casefold()
            if any(part in key_text for part in _SENSITIVE_KEY_PARTS):
                raise ValueError(f"sensitive capability mapping key: {key}")
            _reject_sensitive_keys(nested)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_keys(item)


def _validate_capability_mappings(capability: SourceCapability) -> None:
    """Validate user-provided mapping portions of a capability.

    ``OperationContract.csrf_cookie`` and other contract metadata are valid
    fields. The recursive policy therefore applies to mapping payloads, not
    the fixed wire keys of the contracts themselves.
    """
    for operation in capability.operations.values():
        _reject_sensitive_keys(operation.request_schema)
        _reject_sensitive_keys(operation.response_schema)
    for evidence in capability.evidence:
        _reject_sensitive_keys(evidence.payload)
    _reject_sensitive_keys(capability.resource_mappings)


class SQLiteCapabilityStore:
    """WAL-backed, thread-safe persistence for source capabilities."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(_CREATE_SCHEMA_TABLE)
            rows = self._conn.execute(
                "SELECT version FROM data_plane_schema"
            ).fetchall()
            if not rows:
                self._conn.execute(
                    "INSERT INTO data_plane_schema (version) VALUES (?)", (1,)
                )
            elif len(rows) != 1 or rows[0][0] not in (1, 2, 3):
                raise RuntimeError("unsupported data-plane schema version")
            self._conn.execute(_CREATE_CAPABILITY_TABLE)

    def save(self, capability: SourceCapability) -> None:
        """Insert or replace a capability in one transaction."""
        _validate_capability_mappings(capability)
        with self._lock, self._conn:
            self._write_capability(capability, "")

    def get(self, source_id: str) -> SourceCapability | None:
        """Return the persisted capability for *source_id*, if present."""
        with self._lock:
            row = self._conn.execute(
                "SELECT capability_json FROM source_capabilities WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return SourceCapability.from_dict(json.loads(row["capability_json"]))

    def list_source_ids(self) -> list[str]:
        """Return canonical source ids currently known to the store."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_id FROM source_capabilities ORDER BY source_id"
            ).fetchall()
        return [str(row["source_id"]) for row in rows]

    def demote(self, source_id: str, operation_names: list[str], reason: str) -> None:
        """Quarantine selected operations and persist a new capability revision."""
        if not operation_names:
            raise ValueError("operation_names must not be empty")

        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT capability_json FROM source_capabilities WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if row is None:
                raise KeyError(source_id)
            capability = SourceCapability.from_dict(json.loads(row["capability_json"]))
            missing = [
                name for name in operation_names if name not in capability.operations
            ]
            if missing:
                raise KeyError(missing[0])
            operations = dict(capability.operations)
            for name in operation_names:
                operations[name] = replace(
                    operations[name], trust=TrustState.QUARANTINED
                )
            demoted = replace(
                capability,
                operations=operations,
                revision=capability.revision + 1,
            )
            _validate_capability_mappings(demoted)
            self._write_capability(demoted, reason)

    def _write_capability(self, capability: SourceCapability, reason: str) -> None:
        capability_data = capability.to_dict()
        trust_data = {
            name: operation.trust.value
            for name, operation in capability.operations.items()
        }
        self._conn.execute(
            """
            INSERT INTO source_capabilities (
                source_id, provider, origin, base_url, revision, trust_json,
                capability_json, fingerprint, schema_hash, validated_at,
                expires_at, demotion_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                provider = excluded.provider,
                origin = excluded.origin,
                base_url = excluded.base_url,
                revision = excluded.revision,
                trust_json = excluded.trust_json,
                capability_json = excluded.capability_json,
                fingerprint = excluded.fingerprint,
                schema_hash = excluded.schema_hash,
                validated_at = excluded.validated_at,
                expires_at = excluded.expires_at,
                demotion_reason = excluded.demotion_reason
            """,
            (
                capability.source_id,
                capability.provider,
                capability.origin,
                capability.base_url,
                capability.revision,
                _canonical_json(trust_data),
                _canonical_json(capability_data),
                capability.fingerprint,
                capability.schema_hash,
                capability.validated_at,
                capability.expires_at,
                reason,
            ),
        )

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            self._conn.close()

    @property
    def db_path(self) -> str:
        """Return the shared structured-database path for sibling owners."""
        return self._db_path


__all__ = ["SQLiteCapabilityStore"]
