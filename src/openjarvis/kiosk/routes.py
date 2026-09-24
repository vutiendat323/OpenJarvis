"""FastAPI route for kiosk frontend callbacks."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from openjarvis.kiosk.presentation import PresentationUnavailableError

router = APIRouter()
logger = logging.getLogger(__name__)


class KioskRespondRequest(BaseModel):
    accept: bool


class EnsurePresentationRequest(BaseModel):
    display_origin: str
    language: str | None = None


class KioskLanguageRequest(BaseModel):
    language: str


class ResetPresentationRequest(BaseModel):
    generation: str | None = None


class _TouchEdit(BaseModel):
    # Prices, names and table labels come from what the display was shown or
    # from the merchant, never from the page.
    model_config = ConfigDict(extra="forbid")


class TouchAddItem(_TouchEdit):
    action: Literal["add"]
    variant_id: str = Field(min_length=1, max_length=128)
    quantity: int = Field(ge=1, le=99)
    note: str = Field(default="", max_length=200)


class TouchUpdateLine(_TouchEdit):
    action: Literal["update"]
    line_id: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=99)


class TouchRemoveLine(_TouchEdit):
    action: Literal["remove"]
    line_id: str = Field(min_length=1, max_length=64)


class TouchClearCart(_TouchEdit):
    action: Literal["clear"]


class TouchSetOrderType(_TouchEdit):
    action: Literal["set_order_type"]
    order_type: Literal["at-table", "take-out"]


class TouchSetTable(_TouchEdit):
    action: Literal["set_table"]
    table: str = Field(min_length=1, max_length=128)


class TouchSetPickupTime(_TouchEdit):
    action: Literal["set_pickup_time"]
    pickup_minutes: Literal[0, 5, 10, 15, 30, 45, 60]


TouchCartEdit = Annotated[
    Union[
        TouchAddItem,
        TouchUpdateLine,
        TouchRemoveLine,
        TouchClearCart,
        TouchSetOrderType,
        TouchSetTable,
        TouchSetPickupTime,
    ],
    Field(discriminator="action"),
]


class TouchScreenSearch(BaseModel):
    # Ids only: names and prices come from the menu this display was shown,
    # and no typed text reaches the agent's context.
    model_config = ConfigDict(extra="forbid")

    item_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        max_length=200
    )


# Recorded on the checkout result; a touch checkout has no spoken reply.
_TOUCH_CHECKOUT_MESSAGE = "Order placed from the customer display; payment QR shown."


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


@router.get("/api/kiosk/language")
async def get_kiosk_language(request: Request):
    """Return the active kiosk UI language ('vi' or 'en')."""
    lang = getattr(request.app.state, "kiosk_ui_language", "vi")
    return {"language": lang}


@router.post("/api/kiosk/language")
async def set_kiosk_language(body: KioskLanguageRequest, request: Request):
    """Update the kiosk UI language and broadcast to presentation session."""
    lang = "en" if body.language.lower() == "en" else "vi"
    request.app.state.kiosk_ui_language = lang
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is not None and hasattr(manager, "set_language"):
        manager.set_language(lang)
    bus = getattr(request.app.state, "bus", None)
    session = getattr(manager, "_session", None) if manager else None
    session_id = getattr(session, "session_id", None)
    if bus is not None and session_id:
        from openjarvis.core.events import EventType

        bus.publish(
            EventType.DISPLAY_UPDATE,
            {
                "presentation_session_id": session_id,
                "ui_language": lang,
            },
        )
    return {"ok": True, "language": lang}


@router.post("/api/kiosk/presentation/ensure")
async def ensure_presentation(
    body: EnsurePresentationRequest,
    request: Request,
):
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    if body.language:
        lang = "en" if body.language.lower() == "en" else "vi"
        request.app.state.kiosk_ui_language = lang
        if hasattr(manager, "set_language"):
            manager.set_language(lang)
    elif hasattr(request.app.state, "kiosk_ui_language") and hasattr(
        manager, "set_language"
    ):
        manager.set_language(request.app.state.kiosk_ui_language)
    try:
        session = await run_in_threadpool(manager.ensure, body.display_origin)
    except PresentationUnavailableError as exc:
        logger.warning("Customer display unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="presentation_unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"presentation_session_id": session.session_id}


@router.post("/api/kiosk/presentation/{session_id}/reset")
async def reset_presentation(
    session_id: str,
    request: Request,
    body: ResetPresentationRequest | None = None,
):
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    if manager.replay(session_id) is None:
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    requested_generation = body.generation if body is not None else None
    active_generation = (
        manager.active_generation(session_id)
        if hasattr(manager, "active_generation")
        else None
    )
    if requested_generation is not None and active_generation != requested_generation:
        return {"ok": True}
    voice_task = getattr(request.app.state, "pipecat_voice_task", None)
    voice_generation = getattr(request.app.state, "pipecat_voice_generation", None)
    if (
        requested_generation is not None
        and voice_generation is not None
        and voice_generation != requested_generation
    ):
        return {"ok": True}
    if isinstance(voice_task, asyncio.Future) and not voice_task.done():
        voice_task.cancel()
        await asyncio.gather(voice_task, return_exceptions=True)
    if getattr(request.app.state, "pipecat_voice_task", None) is not voice_task:
        return {"ok": True}
    if getattr(request.app.state, "pipecat_voice_generation", None) not in (
        None,
        voice_generation,
    ):
        return {"ok": True}
    if isinstance(voice_task, asyncio.Future):
        request.app.state.pipecat_voice_task = None
        request.app.state.pipecat_voice_generation = None
    generation = requested_generation or active_generation
    reset = (
        manager.reset(session_id, generation=generation)
        if generation is not None
        else manager.reset(session_id)
    )
    if not reset and manager.replay(session_id) is None:
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    return {"ok": True}


def _touch_owner(request: Request, session_id: str) -> tuple[Any, Any, Any]:
    """Resolve the display, the Voice session that owns it, and its draft cart."""
    state = request.app.state
    manager = getattr(state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    if manager.replay(session_id) is None:
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    sessions = getattr(state, "voice_session_service", None)
    voice = sessions.active_session() if sessions is not None else None
    if voice is None or voice.chat_thread_id != manager.active_generation(session_id):
        raise HTTPException(status_code=409, detail="voice_session_required")
    executor = getattr(getattr(state, "system", None), "tool_executor", None)
    cart = executor.get_tool("display_cart") if executor is not None else None
    if cart is None:
        raise HTTPException(status_code=503, detail="cart_unavailable")
    return manager, voice, cart


@router.post("/api/kiosk/presentation/{session_id}/cart")
async def edit_touch_cart(
    session_id: str,
    body: TouchCartEdit,
    request: Request,
):
    """Apply a customer's tap to the live Voice session's draft cart.

    The Voice agent reads that same draft on its next turn, so touch and
    speech edit one cart. Adding from the menu leaves that screen in place;
    every other edit happens on the receipt and republishes it.
    """
    from openjarvis.core.conversation import conversation_scope
    from openjarvis.kiosk.presentation import presentation_generation

    manager, voice, cart = _touch_owner(request, session_id)
    params: dict[str, Any]
    if isinstance(body, TouchAddItem):
        variant = manager.menu_variant(session_id, body.variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="menu_item_not_found")
        if not variant["available"]:
            raise HTTPException(status_code=409, detail="menu_item_unavailable")
        params = {"item": {**variant, "quantity": body.quantity, "note": body.note}}
    elif isinstance(body, TouchUpdateLine):
        params = {"line_id": body.line_id, "quantity": body.quantity}
    elif isinstance(body, TouchRemoveLine):
        params = {"line_ids": [body.line_id]}
    elif isinstance(body, TouchSetOrderType):
        params = {"order_type": body.order_type}
    elif isinstance(body, TouchSetTable):
        table = manager.table(session_id, body.table)
        if table is None:
            raise HTTPException(status_code=404, detail="table_not_found")
        params = {"table": table["slug"], "table_name": table["name"]}
    elif isinstance(body, TouchSetPickupTime):
        params = {"pickup_minutes": body.pickup_minutes}
    else:
        params = {}

    def edit():
        with (
            conversation_scope(voice.voice_session_id),
            presentation_generation(voice.chat_thread_id),
        ):
            if body.action == "add":
                result = cart.edit_without_display("add", params)
            else:
                result = cart.execute(action=body.action, **params)
            return result, cart.current_snapshot()

    # The cart lock can be held by an agent tool that is waiting on the browser.
    result, snapshot = await run_in_threadpool(edit)
    if not result.success:
        raise HTTPException(status_code=409, detail=result.content)
    return {"cart": snapshot}


@router.post("/api/kiosk/presentation/{session_id}/search")
async def share_touch_search(
    session_id: str,
    body: TouchScreenSearch,
    request: Request,
):
    """Tell the Voice agent which menu rows a typed search is showing."""
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    shared = manager.share_screen_search(session_id, body.item_ids)
    if shared is None:
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    return {"items": shared}


@router.get("/api/kiosk/presentation/{session_id}/tables")
async def list_touch_tables(session_id: str, request: Request):
    """Read the merchant's tables for the receipt's table picker."""
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    try:
        tables = await run_in_threadpool(manager.live_tables, session_id)
    except PresentationUnavailableError as exc:
        logger.warning("Touch table read failed: %s", exc)
        raise HTTPException(status_code=503, detail="tables_unavailable") from exc
    if tables is None:
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    return {"tables": tables}


@router.post("/api/kiosk/presentation/{session_id}/checkout")
async def checkout_touch_cart(session_id: str, request: Request):
    """Place the saved draft through the guarded merchant checkout recipe.

    The recipe runs in its own agent turn inside the Voice conversation, so
    the cart claim, turn nonce and merchant-write guard all apply exactly as
    they do for a spoken checkout. The recipe shows the bill and payment QR.
    """
    from openjarvis.core.conversation import agent_turn_scope, conversation_scope
    from openjarvis.kiosk.presentation import presentation_generation

    manager, voice, cart = _touch_owner(request, session_id)
    checkout = manager.touch_checkout
    if checkout is None:
        raise HTTPException(status_code=503, detail="checkout_unavailable")

    def place_order() -> tuple[str | None, Any]:
        with (
            conversation_scope(voice.voice_session_id),
            presentation_generation(voice.chat_thread_id),
            agent_turn_scope() as nonce,
        ):
            draft = cart.current_snapshot()
            if not draft["lines"]:
                return "cart_empty", None
            if draft["order_type"] not in {"at-table", "take-out"}:
                return "order_type_required", None
            if draft["order_type"] == "at-table" and not draft["table"]:
                return "table_required", None
            return None, checkout.run(
                order_type=draft["order_type"],
                order_note=draft["order_note"],
                table=draft["table"],
                cart_revision=draft["revision"],
                turn_nonce=nonce,
                customer_message=_TOUCH_CHECKOUT_MESSAGE,
            )

    refusal, result = await run_in_threadpool(place_order)
    if refusal is not None:
        raise HTTPException(status_code=409, detail=refusal)
    if not result.success:
        logger.warning("Touch checkout failed: %s", result.content)
        raise HTTPException(status_code=502, detail="checkout_failed")
    return {"ok": True}


@router.get("/api/kiosk/presentation/{session_id}/payment")
async def touch_payment_status(session_id: str, request: Request):
    """Ask the merchant whether the order on screen is paid yet."""
    manager = getattr(request.app.state, "presentation_session_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="presentation_unavailable")
    try:
        status = await run_in_threadpool(manager.check_payment, session_id)
    except PresentationUnavailableError as exc:
        logger.warning("Touch payment status failed: %s", exc)
        raise HTTPException(
            status_code=503, detail="payment_status_unavailable"
        ) from exc
    if status is None:
        raise HTTPException(status_code=404, detail="presentation_session_not_found")
    return {"status": status}
