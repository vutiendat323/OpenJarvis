"""CDP connection to the one page shared by Human input and Playwright MCP."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, AsyncIterator

from websockets.asyncio.client import connect

from openjarvis.kiosk.shared_browser import BrowserEndpoint


class BrowserBridge:
    def __init__(self, endpoint: BrowserEndpoint) -> None:
        self._endpoint = endpoint
        self._socket: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._load_waiter: asyncio.Future[None] | None = None
        self._frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._touch_enabled = False
        self._focus_refresh: asyncio.Task[None] | None = None
        self._closed = asyncio.Event()
        self._last_frame: dict[str, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sensitive_values: list[str] = []
        self._state: dict[str, Any] = {
            "url": "about:blank",
            "title": "",
            "focused_element": None,
            "loading": False,
            "target_id": endpoint.target_id,
            "agent_action": None,
        }

    async def connect(self) -> None:
        self._closed.clear()
        self._loop = asyncio.get_running_loop()
        self._socket = await connect(
            self._endpoint.page_websocket_url, max_size=8_000_000
        )
        self._reader = asyncio.create_task(self._read())
        await self._command("Page.enable")
        await self._command("Runtime.enable")
        await self._command("Runtime.addBinding", {"name": "__openjarvisInput"})
        script = """(() => {
            if (window.__openjarvisShared) return;
            window.__openjarvisShared = true;
            window.open = (url) => { if (url) location.assign(url); return window; };
            document.addEventListener('click', e => {
                const a = e.target.closest?.('a');
                if (a) a.target = '_self';
            }, true);
            document.addEventListener('submit', e => {
                e.target.target = '_self';
            }, true);
            document.addEventListener('focusin', () => {
                window.__openjarvisInput('{}');
            }, true);
            document.addEventListener('input', e => {
                const el = e.target;
                const hint = [el.type, el.name, el.id, el.autocomplete].join(' ');
                if (/password|token|secret|cc-|card|cvv|cvc/i.test(hint) && el.value)
                    window.__openjarvisInput(JSON.stringify({sensitive: el.value}));
            }, true);
        })()"""
        await self._command("Page.addScriptToEvaluateOnNewDocument", {"source": script})
        await self._command("Runtime.evaluate", {"expression": script})
        await self._refresh_state()
        await self._command("Page.startScreencast", {"format": "jpeg", "quality": 60})

    async def close(self) -> None:
        self._closed.set()
        if self._focus_refresh is not None:
            self._focus_refresh.cancel()
            with suppress(asyncio.CancelledError):
                await self._focus_refresh
            self._focus_refresh = None
        if self._socket is not None:
            await self._socket.close()
            self._socket = None
        if self._reader is not None:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None

    def state(self) -> dict[str, Any]:
        return dict(self._state)

    def redact(self, text: str) -> str:
        from openjarvis.kiosk.browser_privacy import redact_browser_text

        for value in sorted(tuple(self._sensitive_values), key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        return redact_browser_text(text)

    def activity(self, name: str | None) -> None:
        """Called by synchronous MCP tools; never wait on the streaming loop."""
        if self._loop is not None and not self._closed.is_set():
            self._loop.call_soon_threadsafe(self._set_activity, name)

    def _set_activity(self, name: str | None) -> None:
        self._state["agent_action"] = name
        self._publish({"type": "state", **self.state()})
        if name is None:
            self._schedule_refresh()

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "state", **self.state()}
        if self._last_frame is not None:
            yield {**self._last_frame, **self.state()}
        while not self._closed.is_set():
            frame = asyncio.create_task(self._frames.get())
            closed = asyncio.create_task(self._closed.wait())
            try:
                await asyncio.wait({frame, closed}, return_when=asyncio.FIRST_COMPLETED)
                if closed.done():
                    return
                yield frame.result()
            finally:
                frame.cancel()
                closed.cancel()
                await asyncio.gather(frame, closed, return_exceptions=True)

    def _schedule_refresh(self) -> None:
        if self._focus_refresh is None or self._focus_refresh.done():
            self._focus_refresh = asyncio.create_task(self._refresh_safely())

    async def _refresh_safely(self) -> None:
        # Coalesce focus/navigation changes without blocking input or frames.
        await asyncio.sleep(0.02)
        with suppress(RuntimeError):
            await self._refresh_state()

    def _publish(self, event: dict[str, Any]) -> None:
        if event["type"] == "frame":
            self._last_frame = event
        if self._frames.full():
            previous = self._frames.get_nowait()
            if event["type"] == "state" and previous["type"] == "frame":
                event = {**previous, **event, "type": "frame"}
        self._frames.put_nowait(event)

    async def handle(self, command: dict[str, Any]) -> None:
        kind = command.get("type")
        if kind == "navigate":
            await self._navigate_and_wait("Page.navigate", {"url": command["url"]})
        elif kind in ("back", "forward"):
            history = await self._command("Page.getNavigationHistory")
            offset = -1 if kind == "back" else 1
            index = history["currentIndex"] + offset
            if 0 <= index < len(history["entries"]):
                entry_id = history["entries"][index]["id"]
                await self._navigate_and_wait(
                    "Page.navigateToHistoryEntry", {"entryId": entry_id}
                )
        elif kind == "reload":
            await self._navigate_and_wait("Page.reload")
        elif kind == "text":
            await self._command("Input.insertText", {"text": command["text"]})
        elif kind == "key":
            await self._command(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown" if command["event"] == "down" else "keyUp",
                    "key": command["key"],
                    "code": command.get("code", command["key"]),
                    "windowsVirtualKeyCode": command.get(
                        "key_code", 13 if command["key"] == "Enter" else 0
                    ),
                    "text": command.get("text", "")
                    if command["event"] == "down"
                    else "",
                    "modifiers": command.get("modifiers", 0),
                },
            )
        elif kind == "resize":
            await self._command(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": command["width"],
                    "height": command["height"],
                    "deviceScaleFactor": 1,
                    "mobile": False,
                },
            )
            self._state["width"] = command["width"]
            self._state["height"] = command["height"]
        elif kind == "wheel":
            await self._command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": command["x"],
                    "y": command["y"],
                    "deltaX": command.get("delta_x", 0),
                    "deltaY": command["delta_y"],
                },
            )
        elif kind == "pointer":
            event_type = {
                "pressed": "mousePressed",
                "released": "mouseReleased",
                "moved": "mouseMoved",
            }[command["event"]]
            await self._command(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": command["x"],
                    "y": command["y"],
                    "button": command.get("button", "none"),
                    "clickCount": 1 if event_type != "mouseMoved" else 0,
                },
            )
            if event_type == "mouseReleased":
                self._schedule_refresh()
        elif kind == "touch":
            if not self._touch_enabled:
                await self._command(
                    "Emulation.setTouchEmulationEnabled", {"enabled": True}
                )
                self._touch_enabled = True
            event_type = {
                "start": "touchStart",
                "move": "touchMove",
                "end": "touchEnd",
                "cancel": "touchCancel",
            }[command["event"]]
            points = (
                []
                if event_type in ("touchEnd", "touchCancel")
                else [{"x": command["x"], "y": command["y"]}]
            )
            await self._command(
                "Input.dispatchTouchEvent",
                {"type": event_type, "touchPoints": points},
            )
            if event_type == "touchEnd":
                self._schedule_refresh()
        else:
            raise ValueError(f"Unknown browser command: {kind}")

    async def _navigate_and_wait(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        self._state["loading"] = True
        self._load_waiter = asyncio.get_running_loop().create_future()
        try:
            result = await self._command(method, params)
            if result.get("errorText"):
                raise RuntimeError("Browser navigation failed")
            await asyncio.wait_for(self._load_waiter, timeout=10)
        finally:
            self._load_waiter = None
            self._state["loading"] = False
        await self._refresh_state()

    async def _command(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._socket is None:
            raise RuntimeError("Browser bridge is not connected")
        self._next_id += 1
        command_id = self._next_id
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[command_id] = future
        await self._socket.send(
            json.dumps({"id": command_id, "method": method, "params": params or {}})
        )
        try:
            return await asyncio.wait_for(future, timeout=15)
        finally:
            self._pending.pop(command_id, None)

    async def _send_no_wait(self, method: str, params: dict[str, Any]) -> None:
        self._next_id += 1
        await self._socket.send(
            json.dumps({"id": self._next_id, "method": method, "params": params})
        )

    async def _read(self) -> None:
        try:
            async for raw in self._socket:
                message = json.loads(raw)
                command_id = message.get("id")
                if command_id is not None:
                    future = self._pending.get(command_id)
                    if future is not None and not future.done():
                        if "error" in message:
                            future.set_exception(
                                RuntimeError(message["error"]["message"])
                            )
                        else:
                            future.set_result(message.get("result", {}))
                    continue
                method = message.get("method")
                params = message.get("params", {})
                if (
                    method == "Runtime.bindingCalled"
                    and params.get("name") == "__openjarvisInput"
                ):
                    try:
                        payload = json.loads(params.get("payload", "{}"))
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    value = payload.get("sensitive")
                    if isinstance(value, str) and len(value) >= 3:
                        if value not in self._sensitive_values:
                            self._sensitive_values.append(value)
                            self._sensitive_values = self._sensitive_values[-128:]
                    else:
                        self._schedule_refresh()
                elif method == "Page.frameNavigated":
                    frame = params.get("frame", {})
                    if "parentId" not in frame:
                        self._state["url"] = frame.get("url", "")
                        self._state["title"] = ""
                        self._state["focused_element"] = None
                        if params.get("type") == "BackForwardCacheRestore":
                            self._state["loading"] = False
                            if (
                                self._load_waiter is not None
                                and not self._load_waiter.done()
                            ):
                                self._load_waiter.set_result(None)
                            self._schedule_refresh()
                elif method == "Page.navigatedWithinDocument":
                    self._schedule_refresh()
                elif method == "Page.loadEventFired":
                    self._state["loading"] = False
                    self._schedule_refresh()
                    if self._load_waiter is not None and not self._load_waiter.done():
                        self._load_waiter.set_result(None)
                elif method == "Page.screencastFrame":
                    await self._send_no_wait(
                        "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
                    )
                    self._publish(
                        {"type": "frame", "data": params["data"], **self.state()}
                    )
        finally:
            self._closed.set()
            if self._load_waiter is not None and not self._load_waiter.done():
                self._load_waiter.set_exception(
                    RuntimeError("Browser CDP disconnected")
                )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Browser CDP disconnected"))

    async def _refresh_state(self) -> None:
        expression = """(() => {
            const e = document.activeElement;
            return {
                url: location.href,
                title: document.title,
                width: innerWidth,
                height: innerHeight,
                focused_element: e ? {
                    tag: e.tagName.toLowerCase(),
                    name: e.getAttribute('aria-label') || e.getAttribute('name') || '',
                    type: e.getAttribute('type') || '',
                    editable: e.isContentEditable
                } : null
            };
        })()"""
        result = await self._command(
            "Runtime.evaluate", {"expression": expression, "returnByValue": True}
        )
        value = result.get("result", {}).get("value")
        if isinstance(value, dict):
            self._state.update(value)
            self._publish({"type": "state", **self.state()})
