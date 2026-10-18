"""Tracking of parallel agent runs within one voice conversation."""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator


class StableOutcome(str, Enum):
    STABLE = "stable"
    TIMEOUT = "timeout"
    CHANGED = "changed"
    CLOSED = "closed"


@dataclass
class _Conversation:
    runs: dict[str, str] = field(default_factory=dict)  # run_id -> request
    waiting: set[str] = field(default_factory=set)
    writes: list[str] = field(default_factory=list)  # run ids, in write order
    freeze_owner: str = ""
    closed: bool = False


class RunTracker:
    """Thread-safe registry of in-flight runs per conversation."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._convs: dict[str, _Conversation] = {}

    def _get(self, conversation_id: str) -> _Conversation:
        conv = self._convs.get(conversation_id)
        if conv is None:
            conv = self._convs[conversation_id] = _Conversation()
        return conv

    def begin(self, conversation_id: str, request: str) -> str:
        run_id = uuid.uuid4().hex
        if not conversation_id:
            return run_id
        with self._cond:
            self._get(conversation_id).runs[run_id] = request
            self._cond.notify_all()
        return run_id

    def end(self, conversation_id: str, run_id: str) -> None:
        if not conversation_id or not run_id:
            return
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is None:
                return
            conv.runs.pop(run_id, None)
            conv.waiting.discard(run_id)
            if conv.freeze_owner == run_id:
                conv.freeze_owner = ""
            self._cond.notify_all()

    def in_flight(self, conversation_id: str, *, exclude: str = "") -> list[str]:
        if not conversation_id:
            return []
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is None:
                return []
            return [req for rid, req in conv.runs.items() if rid != exclude]

    def in_flight_ids(self, conversation_id: str, *, exclude: str = "") -> set[str]:
        """Run ids in flight now (read-only; ``in_flight`` returns requests)."""
        if not conversation_id:
            return set()
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is None:
                return set()
            return {rid for rid in conv.runs if rid != exclude}

    def note_write(self, conversation_id: str, run_id: str) -> None:
        if not conversation_id or not run_id:
            return
        with self._cond:
            self._get(conversation_id).writes.append(run_id)
            self._cond.notify_all()

    def wait_stable(
        self, conversation_id: str, run_id: str, timeout: float
    ) -> StableOutcome:
        if not conversation_id or not run_id:
            return StableOutcome.STABLE
        deadline = time.monotonic() + timeout
        with self._cond:
            conv = self._get(conversation_id)
            snapshot = {
                rid for rid in conv.runs if rid != run_id and rid not in conv.waiting
            }
            seen = len(conv.writes)
            conv.waiting.add(run_id)
            try:
                while True:
                    if conv.closed:
                        return StableOutcome.CLOSED
                    if any(
                        w != run_id and w not in snapshot for w in conv.writes[seen:]
                    ):
                        return StableOutcome.CHANGED
                    if not any(
                        rid in conv.runs and rid not in conv.waiting for rid in snapshot
                    ):
                        return StableOutcome.STABLE
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return StableOutcome.TIMEOUT
                    self._cond.wait(remaining)
            finally:
                # Stays marked until end(): a checkout that has waited is
                # never something another checkout must wait for.
                self._cond.notify_all()

    def wait_others(self, conversation_id: str, run_id: str, timeout: float) -> bool:
        if not conversation_id or not run_id:
            return True
        deadline = time.monotonic() + timeout
        with self._cond:
            conv = self._get(conversation_id)
            others = {rid for rid in conv.runs if rid != run_id}
            while True:
                if conv.closed:
                    return False
                if not any(rid in conv.runs for rid in others):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)

    def freeze(self, conversation_id: str, run_id: str) -> bool:
        """Take the exclusive freeze; False if another run holds it."""
        if not conversation_id or not run_id:
            return True
        with self._cond:
            conv = self._get(conversation_id)
            if conv.freeze_owner not in ("", run_id):
                return False
            conv.freeze_owner = run_id
            self._cond.notify_all()
            return True

    def unfreeze(self, conversation_id: str, run_id: str) -> None:
        if not conversation_id or not run_id:
            return
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is not None and conv.freeze_owner == run_id:
                conv.freeze_owner = ""
                self._cond.notify_all()

    def wait_unfrozen(self, conversation_id: str, run_id: str, timeout: float) -> bool:
        if not conversation_id or not run_id:
            return True
        deadline = time.monotonic() + timeout
        with self._cond:
            conv = self._get(conversation_id)
            while True:
                if conv.closed:
                    return False
                if conv.freeze_owner in ("", run_id):
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(remaining)

    def close(self, conversation_id: str) -> None:
        if not conversation_id:
            return
        with self._cond:
            conv = self._convs.pop(conversation_id, None)
            if conv is not None:
                conv.closed = True
            self._cond.notify_all()


RUN_TRACKER = RunTracker()

_VOICE_RUN: ContextVar[str] = ContextVar("voice_run", default="")


@contextmanager
def voice_run_scope(run_id: str) -> Iterator[None]:
    token = _VOICE_RUN.set(run_id)
    try:
        yield
    finally:
        _VOICE_RUN.reset(token)


def current_voice_run() -> str:
    return _VOICE_RUN.get()
