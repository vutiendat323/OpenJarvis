"""What tools have actually observed, per conversation.

The store deliberately does not belong to a ``ToolExecutor``. There are at
least three instances in a running system -- the agent builds its own
(``tools/_stubs.py:607``), ``SystemBuilder`` makes one, and ``SkillExecutor``
dispatches through whichever it was handed (``skills/executor.py:131``). A
saved skill therefore fetches through a different executor than the one the
agent calls display tools on. Instance-owned buffers would let a skill obtain
a real payment QR that the guard cannot see, and the refusal would look like
the agent's fault.

Every executor writes here; every reader reads here; the conversation owns the
contents.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional, Tuple
from urllib.parse import urlsplit

from openjarvis.core.conversation import current_conversation_id


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """One thing a tool actually observed, kept so output can cite it.

    ``url`` and ``status_code`` are carried because provenance is not only
    "some tool said this" -- a payment QR has to have come from a successful
    HTTP call to a trusted host, not from a page a hostile site rendered.
    """

    tool_name: str
    arguments: Dict[str, Any]
    content: str
    status_code: Optional[int]
    url: Optional[str]


# Three bounds, because two are not enough. One response body in this system is
# 89 KB, so an entry cap alone does not bound memory; and a per-conversation cap
# alone does not either, since every chat request scopes itself to a fresh id
# and would leave its bucket behind. Module-level so tests can lower them.
_MAX_ENTRIES = 20
_MAX_BYTES = 2_000_000
_MAX_CONVERSATIONS = 8

_EVIDENCE: "OrderedDict[str, Deque[ToolEvidence]]" = OrderedDict()
_BYTES: Dict[str, int] = {}
_LOCK = threading.Lock()


def record(
    tool_name: str,
    arguments: Dict[str, Any],
    content: str,
    status_code: Optional[int],
    url: Optional[str],
) -> None:
    """Remember one successful observation for the calling conversation."""
    if not content:
        return
    entry = ToolEvidence(
        tool_name=tool_name,
        arguments=arguments,
        content=content,
        status_code=status_code,
        url=url,
    )
    key = current_conversation_id()
    with _LOCK:
        bucket = _EVIDENCE.setdefault(key, deque())
        _EVIDENCE.move_to_end(key)
        while len(_EVIDENCE) > _MAX_CONVERSATIONS:
            stale, _ = _EVIDENCE.popitem(last=False)
            _BYTES.pop(stale, None)
        bucket.append(entry)
        _BYTES[key] = _BYTES.get(key, 0) + len(content)
        while bucket and (
            len(bucket) > _MAX_ENTRIES or _BYTES[key] > _MAX_BYTES
        ):
            dropped = bucket.popleft()
            _BYTES[key] -= len(dropped.content)


def observed_in_tool_output(
    needle: str,
    *,
    from_tool: Optional[str] = None,
    require_ok: bool = False,
    trusted_hosts: Tuple[str, ...] = (),
) -> bool:
    """Whether this exact string came back from a qualifying tool call."""
    if not needle:
        return False
    for entry in _conversation_evidence():
        if from_tool is not None and entry.tool_name != from_tool:
            continue
        if require_ok and not (
            entry.status_code is not None and 200 <= entry.status_code < 300
        ):
            continue
        if trusted_hosts:
            host = urlsplit(entry.url).hostname if entry.url else None
            if host not in trusted_hosts:
                continue
        if needle in entry.content:
            return True
    return False


def last_result(tool_name: Optional[str] = None) -> str:
    """The most recent successful body in this conversation, or ""."""
    for entry in reversed(_conversation_evidence()):
        if tool_name is None or entry.tool_name == tool_name:
            return entry.content
    return ""


def reset() -> None:
    """Drop everything. For tests; the store is process-wide by design."""
    with _LOCK:
        _EVIDENCE.clear()
        _BYTES.clear()


def _conversation_evidence() -> list:
    with _LOCK:
        return list(_EVIDENCE.get(current_conversation_id(), ()))


__all__ = [
    "ToolEvidence",
    "last_result",
    "observed_in_tool_output",
    "record",
    "reset",
]
