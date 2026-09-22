"""Disposable Chrome proof that Playwright MCP uses the viewport's page."""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.request import urlopen

from openjarvis.kiosk.shared_browser import SharedBrowserProcess
from openjarvis.mcp.client import MCPClient
from openjarvis.mcp.transport import StdioTransport


def test_mcp_navigation_uses_the_preexisting_chrome_page(tmp_path) -> None:
    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    client = None
    try:
        endpoint = browser.start()
        client = MCPClient(
            StdioTransport(
                [
                    "npx",
                    "-y",
                    "@playwright/mcp@0.0.79",
                    "--browser",
                    "chromium",
                    "--cdp-endpoint",
                    endpoint.http_url,
                    "--snapshot-mode",
                    "none",
                ]
            ),
            server_name="playwright",
        )
        client.initialize()
        result = client.call_tool(
            "browser_navigate",
            {"url": "data:text/html,<title>Shared</title><h1>Same page</h1>"},
        )
        assert result["isError"] is False
        snapshot = client.call_tool("browser_snapshot", {})
        assert snapshot["isError"] is False
        assert "Same page" in str(snapshot["content"])
        with urlopen(f"{endpoint.http_url}/json/list", timeout=2) as response:
            pages = [
                target for target in json.load(response) if target["type"] == "page"
            ]
        assert len(pages) == 1
        assert pages[0]["id"] == endpoint.target_id
        assert pages[0]["url"].startswith("data:text/html,")
    finally:
        if client is not None:
            client.close()
        browser.close()


def test_bridge_streams_a_real_chrome_frame(tmp_path) -> None:
    from openjarvis.kiosk.browser_bridge import BrowserBridge

    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle(
                    {
                        "type": "navigate",
                        "url": (
                            "data:text/html,<body style='background:red'>Shared</body>"
                        ),
                    }
                )

                async def read_frame() -> dict:
                    async for message in bridge.subscribe():
                        if message["type"] == "frame":
                            return message
                    raise AssertionError("frame stream stopped")

                frame = await asyncio.wait_for(read_frame(), timeout=3)
                assert base64.b64decode(frame["data"]).startswith(b"\xff\xd8")
                assert frame["url"].startswith("data:text/html,")
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()


def test_human_cdp_input_is_visible_to_playwright_mcp(tmp_path) -> None:
    from openjarvis.kiosk.browser_bridge import BrowserBridge

    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    client = None
    try:
        endpoint = browser.start()
        client = MCPClient(
            StdioTransport(
                [
                    "npx",
                    "-y",
                    "@playwright/mcp@0.0.79",
                    "--cdp-endpoint",
                    endpoint.http_url,
                    "--snapshot-mode",
                    "none",
                ]
            ),
            server_name="playwright",
        )
        client.initialize()
        bridge = BrowserBridge(endpoint)

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle(
                    {
                        "type": "navigate",
                        "url": (
                            "data:text/html,<input autofocus aria-label='Shared input'>"
                        ),
                    }
                )
                await bridge.handle({"type": "text", "text": "human-edited"})
                snapshot = await asyncio.to_thread(
                    client.call_tool, "browser_snapshot", {}
                )
                assert snapshot["isError"] is False
                assert "human-edited" in str(snapshot["content"])
                assert bridge.state()["url"].startswith("data:text/html,")
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        if client is not None:
            client.close()
        browser.close()


def test_human_pointer_click_is_visible_to_playwright_mcp(tmp_path) -> None:
    from openjarvis.kiosk.browser_bridge import BrowserBridge

    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    client = None
    try:
        endpoint = browser.start()
        client = MCPClient(
            StdioTransport(
                [
                    "npx",
                    "-y",
                    "@playwright/mcp@0.0.79",
                    "--cdp-endpoint",
                    endpoint.http_url,
                ]
            ),
            server_name="playwright",
        )
        client.initialize()
        bridge = BrowserBridge(endpoint)

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle(
                    {
                        "type": "navigate",
                        "url": (
                            "data:text/html,<button style='width:300px;height:100px' "
                            "onclick=\"this.textContent='clicked'\">tap</button>"
                        ),
                    }
                )
                await bridge.handle(
                    {
                        "type": "pointer",
                        "event": "pressed",
                        "x": 50,
                        "y": 50,
                        "button": "left",
                    }
                )
                await bridge.handle(
                    {
                        "type": "pointer",
                        "event": "released",
                        "x": 50,
                        "y": 50,
                        "button": "left",
                    }
                )
                snapshot = await asyncio.to_thread(
                    client.call_tool, "browser_snapshot", {}
                )
                assert "clicked" in str(snapshot["content"])
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        if client is not None:
            client.close()
        browser.close()


def test_browser_navigation_controls_change_the_shared_page(tmp_path) -> None:
    from openjarvis.kiosk.browser_bridge import BrowserBridge

    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle({"type": "navigate", "url": "data:text/html,First"})
                await bridge.handle(
                    {"type": "navigate", "url": "data:text/html,Second"}
                )
                assert bridge.state()["url"] == "data:text/html,Second"
                await bridge.handle({"type": "back"})
                assert bridge.state()["url"] == "data:text/html,First"
                await bridge.handle({"type": "forward"})
                assert bridge.state()["url"] == "data:text/html,Second"
                await bridge.handle({"type": "reload"})
                assert bridge.state()["url"] == "data:text/html,Second"
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()
