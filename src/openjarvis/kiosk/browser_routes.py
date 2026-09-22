"""Authenticated one-viewer WebSocket for the shared browser page."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from openjarvis.server.auth_middleware import is_loopback_host, websocket_authorized


def create_browser_router(bridge: Any) -> APIRouter:
    router = APIRouter()
    active_viewer: WebSocket | None = None

    @router.websocket("/api/kiosk/browser/ws")
    async def browser_socket(websocket: WebSocket) -> None:
        nonlocal active_viewer
        origin = websocket.headers.get("origin")
        if origin:
            origin_host = urlsplit(origin).hostname
            target_host = websocket.url.hostname
            if origin_host != target_host and not (
                is_loopback_host(origin_host) and is_loopback_host(target_host)
            ):
                await websocket.close(code=1008)
                return
        expected_key = getattr(websocket.app.state, "api_key", "")
        if not websocket_authorized(websocket, expected_key, allow_loopback=True):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if active_viewer is not None:
            try:
                await active_viewer.close(code=1012)
            except RuntimeError:
                pass
        active_viewer = websocket
        navigation: asyncio.Task[None] | None = None

        async def handle(command: dict[str, Any]) -> None:
            try:
                await bridge.handle(command)
            except (ValueError, KeyError, TypeError, RuntimeError, TimeoutError):
                await websocket.send_json(
                    {"type": "error", "message": "Browser action failed"}
                )

        async def send_events() -> None:
            async for event in bridge.subscribe():
                await websocket.send_json(event)

        async def receive_commands() -> None:
            nonlocal navigation
            while True:
                command = await websocket.receive_json()
                if not isinstance(command, dict):
                    continue
                if command.get("type") in {"navigate", "back", "forward", "reload"}:
                    if navigation is not None:
                        navigation.cancel()
                        await asyncio.gather(navigation, return_exceptions=True)
                    navigation = asyncio.create_task(handle(command))
                else:
                    await handle(command)

        sender = asyncio.create_task(send_events())
        receiver = asyncio.create_task(receive_commands())
        try:
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    task.result()
                except WebSocketDisconnect:
                    pass
        finally:
            if navigation is not None:
                navigation.cancel()
                await asyncio.gather(navigation, return_exceptions=True)
            for task in (sender, receiver):
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
            if active_viewer is websocket:
                active_viewer = None
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()

    return router
