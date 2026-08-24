"""FastAPI route for kiosk frontend callbacks."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openjarvis.kiosk.presentation import PresentationUnavailableError

router = APIRouter()


class KioskRespondRequest(BaseModel):
    accept: bool


class EnsurePresentationRequest(BaseModel):
    display_origin: str


@router.post("/api/kiosk/respond")
async def kiosk_respond(body: KioskRespondRequest, request: Request):
    """Frontend calls this when the user taps Yes/No on the consent popup.

    Request body: ``{"accept": true}`` or ``{"accept": false}``.
    """
    from openjarvis.kiosk.runtime import push_user_response

    response = "accept" if body.accept else "decline"
    await push_user_response(response)
    return {"ok": True}


@router.get("/api/kiosk/state")
async def kiosk_state(request: Request):
    """Debug: return whether kiosk is running."""
    running = getattr(request.app.state, "kiosk_running", False)
    return {"ok": True, "running": running}


@router.post("/api/kiosk/presentation/ensure")
async def ensure_presentation(body: EnsurePresentationRequest, request: Request):
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    try:
        session = manager.ensure(body.display_origin)
    except PresentationUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="presentation_unavailable"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"presentation_session_id": session.session_id}


@router.post("/api/kiosk/presentation/{session_id}/reset")
async def reset_presentation(session_id: str, request: Request):
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    if not manager.reset(session_id):
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    return {"ok": True}
