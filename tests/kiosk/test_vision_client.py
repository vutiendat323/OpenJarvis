"""VisionClient keeps face tracks out of the kiosk queue."""

import asyncio
import json

import pytest

from openjarvis.kiosk.vision_client import VisionClient
from openjarvis.server.voice.speaker import FaceTrackBuffer


@pytest.mark.anyio
async def test_faces_events_go_to_the_buffer_not_the_kiosk_queue():
    import websockets

    sent = []

    async def server(ws):
        sent.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"event": "person_near", "ts": 1.0, "nearest_m": 0.7}))
        await ws.send(json.dumps({"event": "faces", "ts": 1.0, "tracks": []}))
        await asyncio.sleep(0.2)

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        faces = FaceTrackBuffer()
        client = VisionClient(f"ws://127.0.0.1:{port}", faces=faces)
        task = asyncio.create_task(client.run())
        event = await asyncio.wait_for(client.events.get(), 2)
        await asyncio.sleep(0.1)
        await client.stop()
        task.cancel()

    assert sent == [{"cmd": "faces"}]
    assert event["event"] == "person_near"
    assert client.events.empty()
    assert faces.fresh(1.0)
