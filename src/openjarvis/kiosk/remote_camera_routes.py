"""Signalling for the remote camera: a kiosk page posts its video-only offer.

Only a page opened with ``?camera=remote`` calls this, as soon as it loads.
One remote camera at a time: a new offer replaces the previous camera.
Authentication is the ``/api/kiosk/`` rule of ``AuthMiddleware``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.kiosk.remote_camera import RemoteCameraSession

router = APIRouter()


class RemoteCameraOffer(BaseModel):
    sdp: str
    type: str


@router.post("/api/kiosk/remote-camera/offer")
async def remote_camera_offer(body: RemoteCameraOffer, request: Request) -> dict:
    vision = getattr(request.app.state, "vision_client", None)
    if vision is None:
        raise HTTPException(status_code=503, detail="vision_unavailable")
    previous = getattr(request.app.state, "remote_camera", None)
    if previous is not None:
        await previous.close()
        request.app.state.remote_camera = None
    session = RemoteCameraSession(vision.url)
    try:
        answer = await session.answer(body.sdp, body.type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.remote_camera = session
    return {"sdp": answer.sdp, "type": answer.type}
