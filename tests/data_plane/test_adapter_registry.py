from openjarvis.data_plane.adapters import SourceAdapterRegistry
from openjarvis.data_plane.types import DiscoveryEvidence


def test_source_adapter_registry_creates_registered_adapter():
    SourceAdapterRegistry.clear()

    @SourceAdapterRegistry.register("fixture")
    class FixtureAdapter:
        pass

    assert isinstance(SourceAdapterRegistry.create("fixture"), FixtureAdapter)


def test_generic_adapter_wraps_existing_generic_compile_and_normalize() -> None:
    """The required generic registry entry must be usable, not a marker."""
    from openjarvis.data_plane.adapters import GenericAdapter

    adapter = GenericAdapter()
    capability = adapter.compile(
        (
            DiscoveryEvidence(
                kind="embedded_json",
                source_url="https://example.test/menu",
                provenance="publisher_document",
                payload={"schema": {"type": "object"}},
            ),
        )
    )
    adapter.bind_capability(capability)

    batch = adapter.normalize("structured_record", {"id": "espresso"})

    assert capability.provider == "generic"
    assert batch.source_id == "example.test"
    assert batch.records[0].resource_id == "espresso"
