"""Atomic local snapshots for normalized structured resources."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from openjarvis.data_plane.types import (
    NormalizedBatch,
    ResourceRecord,
    ResourceRef,
    SnapshotCommit,
    SnapshotDiff,
    SnapshotRef,
    StructuredQuery,
    StructuredResult,
    StructuredSnapshot,
)

_CREATE_SCHEMA_TABLE = """\
CREATE TABLE IF NOT EXISTS data_plane_schema (
    version INTEGER NOT NULL
)
"""

_CREATE_SNAPSHOT_VERSIONS = """\
CREATE TABLE IF NOT EXISTS snapshot_versions (
    source_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    synced_at TEXT NOT NULL,
    capability_revision INTEGER NOT NULL,
    provenance_json TEXT NOT NULL,
    PRIMARY KEY (source_id, resource_type, version)
)
"""

_CREATE_SNAPSHOT_RESOURCES = """\
CREATE TABLE IF NOT EXISTS snapshot_resources (
    source_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    resource_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    branch TEXT,
    name TEXT,
    status TEXT,
    updated_at TEXT,
    PRIMARY KEY (source_id, resource_type, version, resource_id)
)
"""

_CREATE_SNAPSHOT_HEADS = """\
CREATE TABLE IF NOT EXISTS snapshot_heads (
    source_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (source_id, resource_type)
)
"""

_CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS snapshot_resources_identity_idx "
    "ON snapshot_resources (source_id, resource_type, resource_id)",
    "CREATE INDEX IF NOT EXISTS snapshot_resources_version_idx "
    "ON snapshot_resources (source_id, resource_type, version)",
    "CREATE INDEX IF NOT EXISTS snapshot_resources_branch_idx "
    "ON snapshot_resources (branch)",
    "CREATE INDEX IF NOT EXISTS snapshot_resources_name_idx "
    "ON snapshot_resources (name)",
    "CREATE INDEX IF NOT EXISTS snapshot_resources_status_idx "
    "ON snapshot_resources (status)",
    "CREATE INDEX IF NOT EXISTS snapshot_resources_updated_at_idx "
    "ON snapshot_resources (updated_at)",
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _record_from_row(row: sqlite3.Row) -> ResourceRecord:
    return ResourceRecord(
        resource_id=str(row["resource_id"]),
        payload=dict(json.loads(row["payload_json"])),
        provenance=tuple(str(value) for value in json.loads(row["provenance_json"])),
    )


def _string_payload_field(record: ResourceRecord, field: str) -> str | None:
    value = record.payload.get(field)
    return value if isinstance(value, str) else None


class StructuredSnapshotStore:
    """WAL-backed read model with atomically activated resource snapshots."""

    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

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
                        "INSERT INTO data_plane_schema (version) VALUES (2)"
                    )
                elif len(rows) == 1 and rows[0]["version"] == 1:
                    self._conn.execute("UPDATE data_plane_schema SET version = 2")
                elif len(rows) == 1 and rows[0]["version"] in (2, 3):
                    pass
                else:
                    raise RuntimeError("unsupported data-plane schema version")
                self._conn.execute(_CREATE_SNAPSHOT_VERSIONS)
                self._conn.execute(_CREATE_SNAPSHOT_RESOURCES)
                self._conn.execute(_CREATE_SNAPSHOT_HEADS)
                for statement in _CREATE_INDEXES:
                    self._conn.execute(statement)
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def upsert(self, batch: NormalizedBatch) -> SnapshotCommit:
        """Persist one complete batch and atomically activate its new version."""
        resource_ids = [record.resource_id for record in batch.records]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("duplicate_resource_id")

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                version = self._next_version(batch.source_id, batch.resource_type)
                self._insert_version_and_records(batch, version)
                self._activate_head(batch.source_id, batch.resource_type, version)
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
        ref = SnapshotRef(batch.source_id, batch.resource_type, version)
        return SnapshotCommit(ref, version, len(batch.records), batch.synced_at)

    def get(self, ref: SnapshotRef) -> StructuredSnapshot | None:
        """Return the full immutable batch addressed by *ref*."""
        with self._lock:
            return self._snapshot_for_ref(ref)

    def query(self, query: StructuredQuery) -> StructuredResult:
        """Return the local current head, filtered only by normalized payload fields."""
        with self._lock:
            head = self._conn.execute(
                """
                SELECT version FROM snapshot_heads
                WHERE source_id = ? AND resource_type = ?
                """,
                (query.source_id, query.resource_type),
            ).fetchone()
            if head is None:
                return StructuredResult(items=(), version=0, synced_at="")
            ref = SnapshotRef(query.source_id, query.resource_type, head["version"])
            snapshot = self._snapshot_for_ref(ref)
        if snapshot is None:  # pragma: no cover - schema invariant
            return StructuredResult(items=(), version=0, synced_at="")
        items = tuple(
            record
            for record in snapshot.records
            if all(
                key in record.payload
                and type(record.payload[key]) is type(value)
                and record.payload[key] == value
                for key, value in query.filters.items()
            )
        )
        if query.limit > 0:
            items = items[: query.limit]
        return StructuredResult(
            items=items,
            version=ref.version,
            synced_at=snapshot.synced_at,
            stale=self._is_stale(snapshot.synced_at, query.max_age_seconds),
        )

    def history(self, ref: ResourceRef) -> tuple[StructuredSnapshot, ...]:
        """Return each version of one resource in chronological snapshot order."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT versions.version, versions.synced_at,
                       versions.capability_revision, versions.provenance_json,
                       resources.resource_id, resources.payload_json,
                       resources.provenance_json
                FROM snapshot_versions AS versions
                JOIN snapshot_resources AS resources
                  ON resources.source_id = versions.source_id
                 AND resources.resource_type = versions.resource_type
                 AND resources.version = versions.version
                WHERE resources.source_id = ?
                  AND resources.resource_type = ?
                  AND resources.resource_id = ?
                ORDER BY versions.version ASC
                """,
                (ref.source_id, ref.resource_type, ref.resource_id),
            ).fetchall()
        return tuple(
            StructuredSnapshot(
                ref=SnapshotRef(ref.source_id, ref.resource_type, row["version"]),
                records=(_record_from_row(row),),
                synced_at=row["synced_at"],
                capability_revision=row["capability_revision"],
                provenance=tuple(json.loads(row["provenance_json"])),
            )
            for row in rows
        )

    def diff(self, older: SnapshotRef, newer: SnapshotRef) -> SnapshotDiff:
        """Compare two complete snapshots by resource identity and normalized record."""
        older_snapshot = self.get(older)
        newer_snapshot = self.get(newer)
        if older_snapshot is None:
            raise KeyError(older)
        if newer_snapshot is None:
            raise KeyError(newer)
        older_records = {
            record.resource_id: record for record in older_snapshot.records
        }
        newer_records = {
            record.resource_id: record for record in newer_snapshot.records
        }
        return SnapshotDiff(
            older=older,
            newer=newer,
            added_ids=tuple(sorted(newer_records.keys() - older_records.keys())),
            changed_ids=tuple(
                sorted(
                    resource_id
                    for resource_id in newer_records.keys() & older_records.keys()
                    if newer_records[resource_id] != older_records[resource_id]
                )
            ),
            removed_ids=tuple(sorted(older_records.keys() - newer_records.keys())),
        )

    def close(self) -> None:
        """Close this store's SQLite connection."""
        with self._lock:
            self._conn.close()

    def _next_version(self, source_id: str, resource_type: str) -> int:
        row = self._conn.execute(
            """
            SELECT COALESCE(MAX(version), 0) AS version
            FROM snapshot_versions
            WHERE source_id = ? AND resource_type = ?
            """,
            (source_id, resource_type),
        ).fetchone()
        return int(row["version"]) + 1

    def _insert_version_and_records(self, batch: NormalizedBatch, version: int) -> None:
        self._conn.execute(
            """
            INSERT INTO snapshot_versions (
                source_id, resource_type, version, synced_at,
                capability_revision, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                batch.source_id,
                batch.resource_type,
                version,
                batch.synced_at,
                batch.capability_revision,
                _canonical_json(batch.provenance),
            ),
        )
        self._conn.executemany(
            """
            INSERT INTO snapshot_resources (
                source_id, resource_type, version, resource_id, payload_json,
                provenance_json, branch, name, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    batch.source_id,
                    batch.resource_type,
                    version,
                    record.resource_id,
                    _canonical_json(record.payload),
                    _canonical_json(record.provenance),
                    _string_payload_field(record, "branch"),
                    _string_payload_field(record, "name"),
                    _string_payload_field(record, "status"),
                    _string_payload_field(record, "updated_at"),
                )
                for record in batch.records
            ],
        )

    def _activate_head(self, source_id: str, resource_type: str, version: int) -> None:
        self._conn.execute(
            """
            INSERT INTO snapshot_heads (source_id, resource_type, version)
            VALUES (?, ?, ?)
            ON CONFLICT(source_id, resource_type) DO UPDATE SET
                version = excluded.version
            """,
            (source_id, resource_type, version),
        )

    def _snapshot_for_ref(self, ref: SnapshotRef) -> StructuredSnapshot | None:
        version = self._conn.execute(
            """
            SELECT synced_at, capability_revision, provenance_json
            FROM snapshot_versions
            WHERE source_id = ? AND resource_type = ? AND version = ?
            """,
            (ref.source_id, ref.resource_type, ref.version),
        ).fetchone()
        if version is None:
            return None
        rows = self._conn.execute(
            """
            SELECT resource_id, payload_json, provenance_json
            FROM snapshot_resources
            WHERE source_id = ? AND resource_type = ? AND version = ?
            ORDER BY resource_id ASC
            """,
            (ref.source_id, ref.resource_type, ref.version),
        ).fetchall()
        return StructuredSnapshot(
            ref=ref,
            records=tuple(_record_from_row(row) for row in rows),
            synced_at=version["synced_at"],
            capability_revision=version["capability_revision"],
            provenance=tuple(json.loads(version["provenance_json"])),
        )

    @staticmethod
    def _is_stale(synced_at: str, max_age_seconds: int) -> bool:
        if max_age_seconds <= 0:
            return False
        try:
            timestamp = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()
        return age_seconds > max_age_seconds


__all__ = ["StructuredSnapshotStore"]
