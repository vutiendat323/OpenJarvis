"""Shared Chromium ownership and Playwright MCP attachment."""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

import openjarvis.kiosk.shared_browser as shared_browser
from openjarvis.core.config import JarvisConfig

attach_shared_cdp = shared_browser.attach_shared_cdp


def test_playwright_mcp_attaches_to_owned_cdp_without_launching_another_browser() -> (
    None
):
    server = {
        "name": "playwright",
        "command": "npx",
        "args": [
            "-y",
            "@playwright/mcp@0.0.79",
            "--browser",
            "chromium",
            "--user-data-dir",
            ".openjarvis/ordering-kiosk/browser-profile",
            "--snapshot-mode",
            "none",
        ],
    }
    unrelated = {"name": "other", "command": "other-mcp", "args": []}
    config = SimpleNamespace(
        tools=SimpleNamespace(
            mcp=SimpleNamespace(servers=json.dumps([server, unrelated]))
        )
    )

    attach_shared_cdp(config, SimpleNamespace(http_url="http://127.0.0.1:9223"))

    actual = json.loads(config.tools.mcp.servers)
    assert actual[0]["args"] == [
        "-y",
        "@playwright/mcp@0.0.79",
        "--browser",
        "chromium",
        "--snapshot-mode",
        "none",
        "--cdp-endpoint",
        "http://127.0.0.1:9223",
    ]
    assert actual[1] == unrelated


@pytest.mark.live  # launches a real Chrome
def test_chrome_exposes_one_page_on_loopback(tmp_path) -> None:
    browser = shared_browser.SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        endpoint = browser.start()
        assert endpoint.http_url.startswith("http://127.0.0.1:")
        with urlopen(f"{endpoint.http_url}/json/list", timeout=2) as response:
            targets = json.load(response)
        pages = [target for target in targets if target["type"] == "page"]
        assert len(pages) == 1
        assert pages[0]["id"] == endpoint.target_id
        assert pages[0]["webSocketDebuggerUrl"] == endpoint.page_websocket_url
    finally:
        browser.close()


def test_kiosk_serve_composes_one_browser_before_mcp_discovery(
    tmp_path, monkeypatch
) -> None:
    serve_module = importlib.import_module("openjarvis.cli.serve")
    monkeypatch.setenv("KIOSK_ENABLED", "true")
    config = JarvisConfig()
    config.tools.mcp.enabled = True
    config.tools.mcp.servers = json.dumps(
        [{"name": "playwright", "command": "npx", "args": ["@playwright/mcp@0.0.79"]}]
    )

    browser, bridge = serve_module._start_shared_browser_for_kiosk(
        config, tmp_path / "profile"
    )
    try:
        assert browser is not None
        assert bridge is not None
        args = json.loads(config.tools.mcp.servers)[0]["args"]
        assert args[-2] == "--cdp-endpoint"
        assert args[-1].startswith("http://127.0.0.1:")
        assert bridge.state()["target_id"]
    finally:
        if browser is not None:
            browser.close()
