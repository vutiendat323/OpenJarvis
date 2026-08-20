import json

from openjarvis.core.events import EventType
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.types import (
    OperationContract,
    SnapshotDiff,
    SnapshotRef,
    SourceCapability,
    TransportKind,
    TrustState,
)


def test_source_capability_round_trips_without_secrets():
    capability = SourceCapability(
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
            )
        },
        fingerprint="sha256:bundle",
        schema_hash="sha256:schema",
        evidence=(),
        validated_at="2026-08-20T00:00:00+00:00",
        expires_at="2026-08-21T00:00:00+00:00",
        revision=1,
    )

    encoded = capability.to_dict()

    assert "actual-secret-value" not in json.dumps(encoded)
    assert SourceCapability.from_dict(encoded) == capability


def test_snapshot_diff_round_trips_tuple_fields_as_json_arrays():
    diff = SnapshotDiff(
        older=SnapshotRef(source_id="trend-coffee", resource_type="menu", version=1),
        newer=SnapshotRef(source_id="trend-coffee", resource_type="menu", version=2),
        added_ids=("espresso",),
        changed_ids=("latte",),
        removed_ids=("tea",),
    )

    encoded = diff.to_dict()

    assert encoded["added_ids"] == ["espresso"]
    assert SnapshotDiff.from_dict(encoded) == diff


def test_data_plane_error_keeps_details_out_of_string_form():
    error = DataPlaneError(
        DataPlaneErrorCode.AUTHENTICATION_EXPIRED,
        "Credential expired",
        credential="actual-secret-value",
    )

    assert error.code is DataPlaneErrorCode.AUTHENTICATION_EXPIRED
    assert error.details == {"credential": "actual-secret-value"}
    assert str(error) == "Credential expired"


def test_data_plane_event_wire_values_are_dotted():
    assert EventType.SOURCE_DISCOVERY_STARTED.value == "source.discovery.started"
    assert EventType.ARTIFACT_ROLLED_BACK.value == "artifact.rolled_back"
