"""Opt-in public GET checks for the Trend Coffee adapter."""

from __future__ import annotations

import hashlib
import json
import os
from unittest.mock import Mock
from urllib.parse import urlsplit

import httpx
import pytest

from openjarvis.data_plane.adapters.trendcoffee import TrendCoffeeAdapter
from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.discovery import BrowserObservationPort, DiscoveryEngine
from openjarvis.data_plane.execution import DirectExecutionEngine
from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
from openjarvis.data_plane.types import (
    DiscoveryConstraints,
    DiscoveryEvidence,
    SourceRef,
    StructuredQuery,
)
from openjarvis.merchants.trendcoffee import TrendCoffeeMerchant
from openjarvis.system.bundles import DataPlaneRuntime
from openjarvis.tools.data_plane import SourceSyncTool
from openjarvis.tools.ordering import MenuSearchTool

pytestmark = pytest.mark.live


class _NoNetworkDiscoveryHttp:
    """Deterministic discovery seam that records calls without network I/O."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.deadline: float | None = None
        self.require_https: bool | None = None
        self.closed = False

    def set_deadline(self, deadline: float | None) -> None:
        self.deadline = deadline

    def set_require_https(self, require_https: bool) -> None:
        self.require_https = require_https

    def fetch(self, url: str, method: str = "GET") -> httpx.Response:
        self.calls.append((method, url))
        path = urlsplit(url).path or "/"
        if path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><body>Trend Coffee</body></html>",
                request=httpx.Request(method, url),
            )
        return httpx.Response(
            404,
            headers={"content-type": "text/plain"},
            content=b"not found",
            request=httpx.Request(method, url),
        )

    def close(self) -> None:
        self.closed = True


def _evidence(
    *,
    kind: str,
    url: str,
    response: httpx.Response,
    provenance: str,
) -> DiscoveryEvidence:
    return DiscoveryEvidence(
        kind=kind,
        source_url=url,
        status_code=response.status_code,
        content_type=response.headers.get("content-type", "").split(";", 1)[0],
        body_hash="sha256:" + hashlib.sha256(response.content).hexdigest(),
        provenance=provenance,
    )


def test_live_trendcoffee_public_reads_are_normalizable(tmp_path):
    if os.environ.get("OPENJARVIS_LIVE_TREND_READ") != "1":
        pytest.skip("set OPENJARVIS_LIVE_TREND_READ=1")

    browser_observer = Mock(spec=BrowserObservationPort)
    store = SQLiteCapabilityStore(tmp_path / "trendcoffee-live.db")
    discovery_http = _NoNetworkDiscoveryHttp()
    try:
        discovery_result = DiscoveryEngine(
            store,
            http_client=discovery_http,
            browser_observer=browser_observer,
        ).discover(
            SourceRef("https://trendcoffee.net"),
            DiscoveryConstraints(budget_seconds=10.0, browser_fallback=False),
        )
    finally:
        discovery_http.close()
        store.close()

    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        homepage = client.get("https://trendcoffee.net/")
        branches = client.get("https://trendcoffee.net/api/latest/branch")
        products = client.get("https://trendcoffee.net/api/latest/products")

    adapter = TrendCoffeeAdapter()
    capability = adapter.compile(
        (
            _evidence(
                kind="publisher_html",
                url="https://trendcoffee.net/",
                response=homepage,
                provenance="publisher_document",
            ),
            _evidence(
                kind="api_read",
                url="https://trendcoffee.net/api/latest/branch",
                response=branches,
                provenance="publisher_response",
            ),
            _evidence(
                kind="api_read",
                url="https://trendcoffee.net/api/latest/products",
                response=products,
                provenance="publisher_response",
            ),
        )
    )
    normalized_batch_count = sum(
        bool(batch.records)
        for batch in (
            adapter.normalize("branch", branches.json()),
            adapter.normalize("menu_item", products.json()),
        )
    )

    assert capability.source_id == "trend-coffee"
    assert capability.base_url == "https://trendcoffee.net/api/latest"
    assert normalized_batch_count == 2
    assert discovery_result.browser_actions == 0
    browser_observer.observe.assert_not_called()
    assert discovery_http.calls == [
        ("GET", "https://trendcoffee.net"),
        ("GET", "https://trendcoffee.net/robots.txt"),
    ]
    assert discovery_http.deadline is not None
    assert discovery_http.require_https is True
    assert discovery_http.closed is True


def test_live_source_sync_persists_the_trendcoffee_menu(tmp_path):
    if os.environ.get("OPENJARVIS_LIVE_TREND_READ") != "1":
        pytest.skip("set OPENJARVIS_LIVE_TREND_READ=1")

    db_path = tmp_path / "structured.db"
    capabilities = SQLiteCapabilityStore(db_path)
    snapshots = StructuredSnapshotStore(db_path)
    discovery = DiscoveryEngine(capabilities)
    direct = DirectExecutionEngine(
        capabilities,
        snapshots,
        adapters={"trendcoffee": TrendCoffeeAdapter()},
    )
    runtime = DataPlaneRuntime(capabilities, snapshots, discovery, direct)
    source_sync = SourceSyncTool()
    source_sync._runtime = runtime
    try:
        discovered = discovery.discover(
            SourceRef("https://trendcoffee.net/"),
            DiscoveryConstraints(budget_seconds=20.0, browser_fallback=False),
        )
        result = source_sync.execute(
            source_id=discovered.source_id,
            resources=["branch", "menu_item"],
        )

        assert result.success is True
        menu = snapshots.query(
            StructuredQuery(
                source_id="trend-coffee", resource_type="menu_item", limit=200
            )
        )
        branches = snapshots.query(
            StructuredQuery(source_id="trend-coffee", resource_type="branch")
        )
        assert len(menu.items) >= 120
        assert json.loads(result.content)["sync"]["resource_count"] == (
            len(branches.items) + len(menu.items)
        )

        menu_search = MenuSearchTool()
        menu_search._merchant = TrendCoffeeMerchant(runtime)
        search = menu_search.execute(query="", branch="ba9355f797")
        assert search.success is True
        assert json.loads(search.content)["products"]
    finally:
        runtime.close()
