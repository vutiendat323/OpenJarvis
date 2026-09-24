"""Real CDP input controls for the shared browser page."""

from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from openjarvis.kiosk.browser_bridge import BrowserBridge
from openjarvis.kiosk.shared_browser import SharedBrowserProcess


def test_back_forward_cache_navigation_completes_without_waiting_for_load(tmp_path):
    class PageHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>Cached page</title><p>Ready</p>")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())
        origin = f"http://127.0.0.1:{server.server_port}"

        async def exercise():
            await bridge.connect()
            try:
                await bridge.handle({"type": "navigate", "url": origin + "/first"})
                await bridge.handle({"type": "navigate", "url": origin + "/second"})
                started = time.monotonic()
                await bridge.handle({"type": "back"})
                assert time.monotonic() - started < 2
                assert bridge.state()["url"] == origin + "/first"
                assert bridge.state()["loading"] is False
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resize_changes_the_shared_page_viewport(tmp_path) -> None:
    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle({"type": "resize", "width": 640, "height": 480})
                result = await bridge._command(
                    "Runtime.evaluate",
                    {"expression": "[innerWidth, innerHeight]", "returnByValue": True},
                )
                assert result["result"]["value"] == [640, 480]
                assert bridge.state()["width"] == 640
                assert bridge.state()["height"] == 480
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()


def test_wheel_scroll_moves_the_shared_page(tmp_path) -> None:
    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle(
                    {
                        "type": "navigate",
                        "url": "data:text/html,"
                        "<body style='height:3000px'>Scroll</body>",
                    }
                )
                await bridge.handle(
                    {"type": "wheel", "x": 100, "y": 100, "delta_y": 500}
                )
                await asyncio.sleep(0.1)
                result = await bridge._command(
                    "Runtime.evaluate",
                    {"expression": "scrollY", "returnByValue": True},
                )
                assert result["result"]["value"] > 0
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()


def test_key_event_reaches_focused_page_element(tmp_path) -> None:
    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle(
                    {
                        "type": "navigate",
                        "url": "data:text/html,<input autofocus "
                        "onkeydown=\"if(event.key==='Enter') "
                        "document.body.dataset.entered='yes'\">",
                    }
                )
                await bridge.handle(
                    {
                        "type": "pointer",
                        "event": "pressed",
                        "x": 30,
                        "y": 20,
                        "button": "left",
                    }
                )
                await bridge.handle(
                    {
                        "type": "pointer",
                        "event": "released",
                        "x": 30,
                        "y": 20,
                        "button": "left",
                    }
                )
                await asyncio.sleep(0.05)
                assert bridge.state()["focused_element"]["tag"] == "input"
                await bridge.handle(
                    {"type": "key", "event": "down", "key": "Enter", "code": "Enter"}
                )
                await bridge.handle(
                    {"type": "key", "event": "up", "key": "Enter", "code": "Enter"}
                )
                result = await bridge._command(
                    "Runtime.evaluate",
                    {
                        "expression": "document.body.dataset.entered",
                        "returnByValue": True,
                    },
                )
                assert result["result"]["value"] == "yes"
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()


def test_touch_event_reaches_the_shared_page(tmp_path) -> None:
    browser = SharedBrowserProcess(profile_dir=tmp_path / "profile")
    try:
        bridge = BrowserBridge(browser.start())

        async def exercise() -> None:
            await bridge.connect()
            try:
                await bridge.handle(
                    {
                        "type": "navigate",
                        "url": "data:text/html,"
                        "<button style='width:300px;height:100px' "
                        "ontouchstart=\"this.textContent='touched'\">tap</button>",
                    }
                )
                await bridge.handle(
                    {"type": "touch", "event": "start", "x": 50, "y": 50}
                )
                await bridge.handle({"type": "touch", "event": "end", "x": 50, "y": 50})
                result = await bridge._command(
                    "Runtime.evaluate",
                    {
                        "expression": "document.querySelector('button').textContent",
                        "returnByValue": True,
                    },
                )
                assert result["result"]["value"] == "touched"
            finally:
                await bridge.close()

        asyncio.run(exercise())
    finally:
        browser.close()
