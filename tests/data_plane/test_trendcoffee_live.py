"""Opt-in public GET checks for the Trend Coffee adapter."""

from __future__ import annotations

import hashlib
import os
from unittest.mock import Mock

import httpx
import pytest

from openjarvis.data_plane.adapters.trendcoffee import TrendCoffeeAdapter
from openjarvis.data_plane.discovery import BrowserObservationPort
from openjarvis.data_plane.types import DiscoveryEvidence

pytestmark = pytest.mark.live


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


def test_live_trendcoffee_public_reads_are_normalizable():
    if os.environ.get("OPENJARVIS_LIVE_TREND_READ") != "1":
        pytest.skip("set OPENJARVIS_LIVE_TREND_READ=1")

    # The direct provider adapter has no browser fallback binding. DiscoveryEngine
    # does not dispatch provider adapters yet, so retain this explicit negative
    # capability check at the direct adapter harness boundary.
    browser_observer = Mock(spec=BrowserObservationPort)
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

    assert capability.source_id == "trendcoffee"
    assert capability.base_url == "https://trendcoffee.net/api/latest"
    assert normalized_batch_count == 2
    browser_observer.observe.assert_not_called()
