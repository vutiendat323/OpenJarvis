"""One Chromium process shared by the kiosk viewport and Playwright MCP."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


@dataclass(frozen=True)
class BrowserEndpoint:
    http_url: str
    page_websocket_url: str
    target_id: str


class SharedBrowserProcess:
    """Own a headless Chrome with one page and a loopback-only CDP port."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        chrome_executable: str | None = None,
    ) -> None:
        self._profile_dir = profile_dir
        self._chrome_executable = chrome_executable
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> BrowserEndpoint:
        if self._process is not None:
            raise RuntimeError("Shared browser is already running")
        executable = (
            self._chrome_executable
            or shutil.which("google-chrome")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")  # Fedora's binary name
        )
        if not executable:
            raise RuntimeError("Chrome is required for the shared browser")
        self._profile_dir.mkdir(parents=True, exist_ok=True)
        port_file = self._profile_dir / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        self._process = subprocess.Popen(
            [
                executable,
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={self._profile_dir}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError(
                        "Chrome exited before CDP became ready "
                        f"({self._process.returncode})"
                    )
                if port_file.exists():
                    port = int(port_file.read_text().splitlines()[0])
                    http_url = f"http://127.0.0.1:{port}"
                    try:
                        with urlopen(f"{http_url}/json/list", timeout=1) as response:
                            targets = json.load(response)
                    except (OSError, URLError):
                        time.sleep(0.05)
                        continue
                    pages = [
                        target for target in targets if target.get("type") == "page"
                    ]
                    if len(pages) != 1:
                        raise RuntimeError(
                            "Shared browser must start with exactly one page"
                        )
                    page = pages[0]
                    return BrowserEndpoint(
                        http_url=http_url,
                        page_websocket_url=page["webSocketDebuggerUrl"],
                        target_id=page["id"],
                    )
                time.sleep(0.05)
            raise RuntimeError("Timed out waiting for Chrome CDP")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def attach_shared_cdp(config: Any, endpoint: Any) -> None:
    """Point the configured Playwright MCP server at the owned browser."""
    servers = json.loads(config.tools.mcp.servers)
    for server in servers:
        if server.get("name") != "playwright":
            continue
        args = list(server.get("args", []))
        for option in ("--user-data-dir", "--cdp-endpoint"):
            if option in args:
                index = args.index(option)
                del args[index : index + 2]
        server["args"] = [*args, "--cdp-endpoint", endpoint.http_url]
        config.tools.mcp.servers = json.dumps(servers)
        return
    raise ValueError("Shared browser requires a Playwright MCP server")
