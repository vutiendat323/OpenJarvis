"""Which conversation the current call belongs to.

Tool instances are process-wide: one ``ToolExecutor`` serves every
conversation, and so does one ``ReplTool``. Anything a tool remembers between
calls therefore needs an explicit owner, or one customer inherits another's
working set -- the defect the ordering cart shipped with.

``ToolExecutor`` copies the caller's context before dispatching into its thread
pool, so a variable set here reaches the tool. Mirrors the existing
``presentation_generation`` scope in ``kiosk/presentation.py``.
"""

from __future__ import annotations

import secrets
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import Iterable, Iterator

_CONVERSATION_ID: ContextVar[str] = ContextVar("openjarvis_conversation_id", default="")
_TURN_NONCE: ContextVar[str] = ContextVar("openjarvis_turn_nonce", default="")


@dataclass
class _TurnState:
    nonce: str
    active: bool = True
    claimed: bool = False
    # The voice session ended: its committed dispatches stop at the next step.
    revoked: bool = False


_TURN_STATE: ContextVar[_TurnState | None] = ContextVar(
    "openjarvis_turn_state", default=None
)
_TURN_LOCK = RLock()
_ACTIVE_TURNS: dict[str, _TurnState] = {}
# The turn whose completed tool calls this context is running (spec
# 2026-10-17 barge-in §5.2): its nonce stays valid here after the turn ends.
_COMMITTED_TURN: ContextVar[_TurnState | None] = ContextVar(
    "openjarvis_committed_turn", default=None
)
_COMMITTED: dict[str, list[_TurnState]] = {}
# Conversations whose voice session ended, newest last: a committed dispatch
# whose worker thread starts after the revoke is revoked on entry.
_REVOKED: OrderedDict[str, None] = OrderedDict()
_REVOKED_LIMIT = 256

# None = the speaker is confirmed (the default for every caller). A frozenset
# = the Voice gate could not confirm who spoke this turn; only those tools
# may run until the customer confirms.
_UNCERTAIN_SPEAKER_TOOLS: ContextVar[frozenset[str] | None] = ContextVar(
    "openjarvis_uncertain_speaker_tools", default=None
)

SPEAKER_UNCONFIRMED_MESSAGE = (
    "speaker_unconfirmed: this request may not have come from the customer at "
    "the kiosk. Nothing was changed. Ask the customer to confirm the request "
    "before changing the cart, order or payment."
)


def turn_nonce_is_active(nonce: str) -> bool:
    """Also invalidate copied worker contexts when their turn ends or is replaced."""
    with _TURN_LOCK:
        state = _TURN_STATE.get()
        if state is not None and state.revoked:
            return False
        if (
            state
            and state.active
            and state.nonce == nonce
            and _ACTIVE_TURNS.get(current_conversation_id()) is state
        ):
            return True
        committed = _COMMITTED_TURN.get()
        return bool(
            committed is not None
            and committed is state
            and committed.nonce == nonce
            and not committed.revoked
        )


def claim_turn_nonce(nonce: str) -> bool:
    """Consume a model's fresh checkout dispatch once within its owning turn."""
    with _TURN_LOCK:
        state = _TURN_STATE.get()
        if not turn_nonce_is_active(nonce) or state is None or state.claimed:
            return False
        state.claimed = True
        return True


def current_conversation_id() -> str:
    """Return the current conversation's id, or "" outside any conversation."""
    return _CONVERSATION_ID.get()


def current_turn_nonce() -> str:
    """Return the opaque nonce for the active agent turn, or ""."""
    return _TURN_NONCE.get()


@contextmanager
def uncertain_speaker_scope(allowed_tools: Iterable[str]) -> Iterator[None]:
    """Mark this turn's speaker as unconfirmed; only ``allowed_tools`` may run."""
    token = _UNCERTAIN_SPEAKER_TOOLS.set(frozenset(allowed_tools))
    try:
        yield
    finally:
        _UNCERTAIN_SPEAKER_TOOLS.reset(token)


def speaker_blocks_tool(tool_name: str) -> bool:
    """True when an unconfirmed speaker's turn may not run ``tool_name``."""
    allowed = _UNCERTAIN_SPEAKER_TOOLS.get()
    return allowed is not None and tool_name not in allowed


@contextmanager
def allowed_tool_steps(tool_name: str) -> Iterator[None]:
    """Let an allow-listed prepared skill run its own fixed steps.

    The allow-list names what an unconfirmed speaker may ask for (e.g. the
    read-only ``skill_trendcoffee-menu``); the skill's templated steps
    (``http_request`` GET, ``display_menu``) are its implementation, not the
    speaker's choice. A skill that is not allowed keeps every step blocked.
    """
    if _UNCERTAIN_SPEAKER_TOOLS.get() is None or speaker_blocks_tool(tool_name):
        yield
        return
    token = _UNCERTAIN_SPEAKER_TOOLS.set(None)
    try:
        yield
    finally:
        _UNCERTAIN_SPEAKER_TOOLS.reset(token)


@contextmanager
def agent_turn_scope() -> Iterator[str]:
    """Issue one unguessable nonce that expires with the current agent turn."""
    nonce = secrets.token_hex(16)
    state = _TurnState(nonce)
    owner = current_conversation_id()
    parent = _TURN_STATE.get()
    with _TURN_LOCK:
        _ACTIVE_TURNS[owner] = state
    state_token = _TURN_STATE.set(state)
    token = _TURN_NONCE.set(nonce)
    try:
        yield nonce
    finally:
        with _TURN_LOCK:
            state.active = False
            if _ACTIVE_TURNS.get(owner) is state:
                if parent is not None and parent.active:
                    _ACTIVE_TURNS[owner] = parent
                else:
                    _ACTIVE_TURNS.pop(owner, None)
        _TURN_STATE.reset(state_token)
        _TURN_NONCE.reset(token)


@contextmanager
def committed_turn_scope() -> Iterator[None]:
    """Keep the current turn's nonce valid for the work run inside this block.

    The orchestrator runs a round's completed tool calls here, so a barge-in
    that ends the turn does not stop them (spec 2026-10-17 barge-in §5.2). The
    nonce is still claimable once, and the session's end revokes it.
    """
    state = _TURN_STATE.get()
    if state is None:
        yield
        return
    owner = current_conversation_id()
    with _TURN_LOCK:
        if owner in _REVOKED:
            state.revoked = True
        _COMMITTED.setdefault(owner, []).append(state)
    token = _COMMITTED_TURN.set(state)
    try:
        yield
    finally:
        _COMMITTED_TURN.reset(token)
        with _TURN_LOCK:
            states = _COMMITTED.get(owner, [])
            if state in states:
                states.remove(state)
            if not states:
                _COMMITTED.pop(owner, None)


def revoke_committed_turns(conversation_id: str) -> int:
    """The conversation's session ended: stop its committed dispatches."""
    with _TURN_LOCK:
        if conversation_id:
            _REVOKED[conversation_id] = None
            _REVOKED.move_to_end(conversation_id)
            while len(_REVOKED) > _REVOKED_LIMIT:
                _REVOKED.popitem(last=False)
        states = _COMMITTED.pop(conversation_id, [])
        for state in states:
            state.revoked = True
        return len(states)


@contextmanager
def conversation_scope(conversation_id: str) -> Iterator[None]:
    """Own every tool working set created inside this block."""
    token = _CONVERSATION_ID.set(conversation_id)
    try:
        yield
    finally:
        _CONVERSATION_ID.reset(token)


__all__ = [
    "SPEAKER_UNCONFIRMED_MESSAGE",
    "agent_turn_scope",
    "claim_turn_nonce",
    "committed_turn_scope",
    "conversation_scope",
    "current_conversation_id",
    "current_turn_nonce",
    "speaker_blocks_tool",
    "allowed_tool_steps",
    "revoke_committed_turns",
    "turn_nonce_is_active",
    "uncertain_speaker_scope",
]
