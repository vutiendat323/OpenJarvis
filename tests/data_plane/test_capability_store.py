import sqlite3

import pytest

from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.types import (
    DiscoveryEvidence,
    OperationContract,
    SourceCapability,
    TransportKind,
    TrustState,
)


@pytest.fixture
def trend_capability() -> SourceCapability:
    return SourceCapability(
        source_id="trend-coffee",
        provider="trendcoffee",
        origin="https://trendcoffee.net",
        base_url="https://trendcoffee.net/api/latest",
        auth_mode="none",
        credential_ref="",
        transport=TransportKind.REST,
        operations={
            "menu.list": OperationContract(
                name="menu.list",
                method="GET",
                path="/products",
                resource_type="menu_item",
                trust=TrustState.READ_VALIDATED,
                safe=True,
            ),
            "order.place": OperationContract(
                name="order.place",
                method="POST",
                path="/orders",
                resource_type="order",
                trust=TrustState.WRITE_VALIDATED,
                safe=False,
            ),
        },
        fingerprint="sha256:bundle",
        schema_hash="sha256:schema",
        evidence=(
            DiscoveryEvidence(
                kind="openapi",
                source_url="https://trendcoffee.net/openapi.json",
            ),
        ),
        validated_at="2026-08-20T00:00:00+00:00",
        expires_at="2026-08-21T00:00:00+00:00",
        revision=1,
        resource_mappings={"menu_item": {"id": "product_id"}},
    )


def test_save_get_and_demote_are_durable(tmp_path, trend_capability):
    path = tmp_path / "structured.db"
    store = SQLiteCapabilityStore(path)
    store.save(trend_capability)
    store.demote("trend-coffee", ["order.place"], "schema_changed")
    store.close()

    reopened = SQLiteCapabilityStore(path)
    saved = reopened.get("trend-coffee")
    assert saved is not None
    assert saved.operations["menu.list"].trust is TrustState.READ_VALIDATED
    assert saved.operations["order.place"].trust is TrustState.QUARANTINED
    assert saved.revision == trend_capability.revision + 1
    reopened.close()


def test_get_returns_none_for_unknown_source(tmp_path):
    store = SQLiteCapabilityStore(tmp_path / "structured.db")

    assert store.get("missing") is None
    store.close()


@pytest.mark.parametrize(
    "unsafe_key", ["authorization", "Cookie", "ACCESS_TOKEN", "secret_value"]
)
def test_save_rejects_sensitive_mapping_keys_before_persistence(
    tmp_path, trend_capability, unsafe_key
):
    capability_data = trend_capability.to_dict()
    capability_data["resource_mappings"] = {
        "menu_item": {"nested": {unsafe_key: "do-not-persist"}},
    }
    unsafe_capability = SourceCapability.from_dict(capability_data)
    path = tmp_path / "structured.db"
    store = SQLiteCapabilityStore(path)

    with pytest.raises(ValueError, match="sensitive"):
        store.save(unsafe_capability)

    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM source_capabilities").fetchone()[0]
            == 0
        )
    store.close()


def test_credential_ref_is_not_rejected(tmp_path, trend_capability):
    capability_data = trend_capability.to_dict()
    capability_data["resource_mappings"] = {"credential_ref": {"field": "ref-id"}}
    capability = SourceCapability.from_dict(capability_data)
    store = SQLiteCapabilityStore(tmp_path / "structured.db")

    store.save(capability)

    assert store.get(capability.source_id) == capability
    store.close()


def test_empty_database_migrates_to_version_one_idempotently(tmp_path):
    path = tmp_path / "structured.db"

    first = SQLiteCapabilityStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM data_plane_schema"
        ).fetchall() == [(1,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'source_capabilities'"
        ).fetchone() == ("source_capabilities",)
    first.close()

    second = SQLiteCapabilityStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM data_plane_schema"
        ).fetchall() == [(1,)]
    second.close()


def test_capability_store_remains_compatible_after_snapshot_migrates_to_v2(
    tmp_path, trend_capability
):
    from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore

    path = tmp_path / "structured.db"
    capability_store = SQLiteCapabilityStore(path)
    capability_store.close()
    snapshot_store = StructuredSnapshotStore(path)
    snapshot_store.close()

    reopened = SQLiteCapabilityStore(path)
    reopened.save(trend_capability)

    assert reopened.get(trend_capability.source_id) == trend_capability
    reopened.close()


def test_capability_store_rejects_schema_versions_above_three(tmp_path):
    path = tmp_path / "structured.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE data_plane_schema (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO data_plane_schema (version) VALUES (4)")

    with pytest.raises(RuntimeError, match="unsupported data-plane schema version"):
        SQLiteCapabilityStore(path)
