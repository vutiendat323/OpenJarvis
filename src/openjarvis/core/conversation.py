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

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_CONVERSATION_ID: ContextVar[str] = ContextVar(
    "openjarvis_conversation_id", default=""
)


def current_conversation_id() -> str:
    """Return the current conversation's id, or "" outside any conversation."""
    return _CONVERSATION_ID.get()


@contextmanager
def conversation_scope(conversation_id: str) -> Iterator[None]:
    """Own every tool working set created inside this block."""
    token = _CONVERSATION_ID.set(conversation_id)
    try:
        yield
    finally:
        _CONVERSATION_ID.reset(token)


__all__ = ["conversation_scope", "current_conversation_id"]
