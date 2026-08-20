import sqlite3
import threading

import pytest

from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    ConsistencyMode,
    NormalizedBatch,
    ResourceRecord,
    ResourceRef,
    StructuredQuery,
)


def menu_batch(
    price: int,
    synced_at: str = "2026-08-20T00:00:00Z",
    *,
    records: tuple[ResourceRecord, ...] | None = None,
) -> NormalizedBatch:
    return NormalizedBatch(
        source_id="trend-coffee",
        resource_type="menu_item",
        records=records
        or (
            ResourceRecord(
                resource_id="23f99adf51",
                payload={
                    "branch": "Quận 1",
                    "name": "Cà phê đen",
                    "status": "available",
                    "updated_at": synced_at,
                    "price": price,
                },
                provenance=("https://trendcoffee.net/menu",),
            ),
            ResourceRecord(
                resource_id="9912aabbcc",
                payload={
                    "name": "Bạc xỉu",
                    "price": 45_000,
                },
            ),
        ),
        synced_at=synced_at,
        capability_revision=7,
        provenance=("trendcoffee-api",),
    )


def current_menu_query() -> StructuredQuery:
    return StructuredQuery(
        source_id="trend-coffee",
        resource_type="menu_item",
        consistency=ConsistencyMode.CACHED,
    )


def batch_with_duplicate_ids() -> NormalizedBatch:
    duplicate = ResourceRecord(resource_id="duplicate", payload={"name": "A"})
    return menu_batch(35_000, records=(duplicate, duplicate))


def test_upsert_versions_query_current_snapshot_and_extracts_typed_fields(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    first = store.upsert(menu_batch(price=35_000))
    second = store.upsert(menu_batch(price=36_000, synced_at="2026-08-20T01:00:00Z"))

    result = store.query(
        StructuredQuery(
            source_id="trend-coffee",
            resource_type="menu_item",
            filters={"name": "Cà phê đen"},
            consistency=ConsistencyMode.CACHED,
        )
    )

    assert second.version == first.version + 1
    assert result.version == second.version
    assert result.items[0].payload["price"] == 36_000
    assert store.diff(first.ref, second.ref).changed_ids == ("23f99adf51",)
    current_items = store.query(current_menu_query()).items
    assert [record.resource_id for record in current_items] == [
        "23f99adf51",
        "9912aabbcc",
    ]
    assert [
        record.resource_id
        for record in store.query(
            StructuredQuery(
                source_id="trend-coffee", resource_type="menu_item", limit=1
            )
        ).items
    ] == ["23f99adf51"]
    with sqlite3.connect(tmp_path / "structured.db") as connection:
        assert connection.execute(
            "SELECT branch, name, status, updated_at FROM snapshot_resources "
            "WHERE source_id = ? AND resource_type = ? AND version = ? "
            "AND resource_id = ?",
            ("trend-coffee", "menu_item", second.version, "23f99adf51"),
        ).fetchone() == (
            "Quận 1",
            "Cà phê đen",
            "available",
            "2026-08-20T01:00:00Z",
        )
    store.close()


def test_failed_batch_keeps_last_complete_version(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    committed = store.upsert(menu_batch(price=35_000))

    with pytest.raises(ValueError, match="duplicate_resource_id"):
        store.upsert(batch_with_duplicate_ids())

    assert store.query(current_menu_query()).version == committed.version
    store.close()


def test_upsert_many_rolls_back_every_head_when_later_batch_is_invalid(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    previous = store.upsert(menu_batch(price=35_000))
    branch = NormalizedBatch(
        source_id="trend-coffee",
        resource_type="branch",
        records=(ResourceRecord(resource_id="branch-1", payload={"name": "Q1"}),),
        synced_at="2026-08-20T01:00:00Z",
    )

    with pytest.raises(ValueError, match="duplicate_resource_id"):
        store.upsert_many((branch, batch_with_duplicate_ids()))

    assert store.query(current_menu_query()).version == previous.version
    assert (
        store.query(
            StructuredQuery(source_id="trend-coffee", resource_type="branch")
        ).items
        == ()
    )
    store.close()


def test_query_requires_present_type_matched_payload_filters(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    store.upsert(
        menu_batch(
            35_000,
            records=(
                ResourceRecord(resource_id="boolean", payload={"value": True}),
                ResourceRecord(resource_id="integer", payload={"value": 1}),
                ResourceRecord(resource_id="missing", payload={"name": "missing"}),
                ResourceRecord(resource_id="null", payload={"value": None}),
            ),
        )
    )

    null_matches = store.query(
        StructuredQuery(
            source_id="trend-coffee",
            resource_type="menu_item",
            filters={"value": None},
        )
    )
    integer_matches = store.query(
        StructuredQuery(
            source_id="trend-coffee",
            resource_type="menu_item",
            filters={"value": 1},
        )
    )

    assert [record.resource_id for record in null_matches.items] == ["null"]
    assert [record.resource_id for record in integer_matches.items] == ["integer"]
    store.close()


def test_get_history_and_diff_preserve_snapshot_metadata(tmp_path):
    store = StructuredSnapshotStore(tmp_path / "structured.db")
    first = store.upsert(menu_batch(price=35_000))
    second = store.upsert(menu_batch(price=36_000, synced_at="2026-08-20T01:00:00Z"))

    snapshot = store.get(first.ref)
    history = store.history(
        ResourceRef(
            source_id="trend-coffee",
            resource_type="menu_item",
            resource_id="23f99adf51",
        )
    )

    assert snapshot is not None
    assert snapshot.ref == first.ref
    assert len(snapshot.records) == 2
    assert [item.ref for item in history] == [first.ref, second.ref]
    assert [len(item.records) for item in history] == [1, 1]
    assert history[1].records[0].payload["price"] == 36_000
    assert history[1].capability_revision == 7
    assert history[1].provenance == ("trendcoffee-api",)
    assert store.diff(first.ref, second.ref).added_ids == ()
    assert store.diff(first.ref, second.ref).removed_ids == ()
    store.close()


def test_snapshot_store_migrates_v1_database_to_v2(tmp_path):
    path = tmp_path / "structured.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE data_plane_schema (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO data_plane_schema (version) VALUES (1)")

    store = StructuredSnapshotStore(path)

    with sqlite3.connect(path) as connection:
        versions = connection.execute(
            "SELECT version FROM data_plane_schema"
        ).fetchall()
        assert versions == [(2,)]
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE 'snapshot_%'"
            )
        } == {"snapshot_versions", "snapshot_resources", "snapshot_heads"}
    store.close()


def test_reader_observes_only_complete_snapshot_across_writer_transaction(tmp_path):
    path = tmp_path / "structured.db"
    seed = StructuredSnapshotStore(path)
    committed = seed.upsert(menu_batch(price=35_000))
    seed.close()

    reader = StructuredSnapshotStore(path)
    writer = StructuredSnapshotStore(path)
    activation_started = threading.Event()
    allow_activation = threading.Event()
    writer_error: list[BaseException] = []

    def pause_head_activation() -> int:
        activation_started.set()
        assert allow_activation.wait(timeout=5)
        return 0

    writer._conn.create_function("pause_head_activation", 0, pause_head_activation)
    writer._conn.execute(
        "CREATE TRIGGER pause_snapshot_head BEFORE INSERT ON snapshot_heads "
        "BEGIN SELECT pause_head_activation(); END"
    )

    def write_new_snapshot() -> None:
        try:
            writer.upsert(menu_batch(price=36_000, synced_at="2026-08-20T01:00:00Z"))
        except BaseException as error:  # pragma: no cover - asserted below
            writer_error.append(error)

    write_thread = threading.Thread(target=write_new_snapshot)
    write_thread.start()
    assert activation_started.wait(timeout=5)

    before_commit = reader.query(current_menu_query())
    assert before_commit.version == committed.version
    assert before_commit.items[0].payload["price"] == 35_000

    allow_activation.set()
    write_thread.join(timeout=5)
    assert not write_thread.is_alive()
    assert writer_error == []

    after_commit = reader.query(current_menu_query())
    assert after_commit.version == committed.version + 1
    assert after_commit.items[0].payload["price"] == 36_000
    reader.close()
    writer.close()
