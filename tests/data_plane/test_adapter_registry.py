from openjarvis.data_plane.adapters import SourceAdapterRegistry


def test_source_adapter_registry_creates_registered_adapter():
    SourceAdapterRegistry.clear()

    @SourceAdapterRegistry.register("fixture")
    class FixtureAdapter:
        pass

    assert isinstance(SourceAdapterRegistry.create("fixture"), FixtureAdapter)
