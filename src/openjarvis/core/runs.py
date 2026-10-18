"""Tracking of parallel agent runs within one voice conversation."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
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
    # Checkout freezes released so far (spec P8, ruling 17): a checkout that
    # sees this move while it waited lets the other checkout's order stand.
    epoch: int = 0
    # Per checkout that waited: the epoch and the runs it waited for, both
    # taken when its wait began.
    waits: dict[str, tuple[int, frozenset[str]]] = field(default_factory=dict)
    # Per run: the runs with words in flight when it began, which its prompt
    # names as in progress (I5).
    named: dict[str, frozenset[str]] = field(default_factory=dict)


class RunTracker:
    """Thread-safe registry of in-flight runs per conversation."""

    # Closed conversations remembered so a late call cannot reopen one.
    MAX_CLOSED = 1024

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._convs: dict[str, _Conversation] = {}
        self._closed: OrderedDict[str, None] = OrderedDict()

    def _get(self, conversation_id: str) -> _Conversation | None:
        """The conversation's state, created on first use; None once closed."""
        conv = self._convs.get(conversation_id)
        if conv is None:
            if conversation_id in self._closed:
                return None
            conv = self._convs[conversation_id] = _Conversation()
        return conv

    def begin(self, conversation_id: str, request: str) -> str:
        run_id = uuid.uuid4().hex
        if not conversation_id:
            return run_id
        with self._cond:
            conv = self._get(conversation_id)
            if conv is not None:
                conv.named[run_id] = frozenset(
                    rid for rid, words in conv.runs.items() if words
                )
                conv.runs[run_id] = request
                self._cond.notify_all()
        return run_id

    def named_at_begin(self, conversation_id: str, run_id: str) -> frozenset[str]:
        """The runs with words in flight when this run began, while it runs."""
        if not conversation_id or not run_id:
            return frozenset()
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is None:
                return frozenset()
            return conv.named.get(run_id, frozenset())

    def end(self, conversation_id: str, run_id: str) -> None:
        if not conversation_id or not run_id:
            return
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is None:
                return
            conv.runs.pop(run_id, None)
            conv.waiting.discard(run_id)
            conv.waits.pop(run_id, None)
            conv.named.pop(run_id, None)
            if conv.freeze_owner == run_id:
                self._release_freeze(conv)
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
            conv = self._get(conversation_id)
            if conv is None:
                return
            conv.writes.append(run_id)
            self._cond.notify_all()

    def wait_stable(
        self, conversation_id: str, run_id: str, timeout: float
    ) -> StableOutcome:
        """Wait until no other run that is not itself waiting is in flight.

        Every such run counts, also one begun during the wait (ruling 16): a
        request still thinking may yet change the cart. A write by a run that
        was not in flight when the wait began is a change: the customer asked
        for something new, so the checkout is dropped (spec 7.7).
        """
        if not conversation_id or not run_id:
            return StableOutcome.STABLE
        deadline = time.monotonic() + timeout
        with self._cond:
            conv = self._get(conversation_id)
            if conv is None:
                return StableOutcome.CLOSED
            snapshot = frozenset(
                rid for rid in conv.runs if rid != run_id and rid not in conv.waiting
            )
            conv.waits[run_id] = (conv.epoch, snapshot)
            seen = len(conv.writes)
            conv.waiting.add(run_id)
            # A peer checkout waits for this run until it is marked waiting.
            self._cond.notify_all()
            try:
                while True:
                    if conv.closed:
                        return StableOutcome.CLOSED
                    if any(
                        w != run_id and w not in snapshot for w in conv.writes[seen:]
                    ):
                        return StableOutcome.CHANGED
                    if not any(
                        rid != run_id and rid not in conv.waiting for rid in conv.runs
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

    def waited_for(self, conversation_id: str, run_id: str) -> frozenset[str]:
        """The runs in flight, and not waiting, when this run's wait began."""
        if not conversation_id or not run_id:
            return frozenset()
        with self._cond:
            conv = self._convs.get(conversation_id)
            wait = conv.waits.get(run_id) if conv is not None else None
            return wait[1] if wait is not None else frozenset()

    def superseded(self, conversation_id: str, run_id: str) -> bool:
        """True when another checkout's freeze ended since this run's wait began.

        Checked by a checkout once it holds the freeze, so no other checkout
        can move the epoch meanwhile (ruling 17). A closed conversation counts
        as superseded: nothing may be ordered for it any more.
        """
        if not conversation_id or not run_id:
            return False
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is None:
                return conversation_id in self._closed
            wait = conv.waits.get(run_id)
            return conv.closed or (wait is not None and wait[0] != conv.epoch)

    def wait_others(self, conversation_id: str, run_id: str, timeout: float) -> bool:
        """Wait for the runs begun before this one, except waiting checkouts.

        A later run is never waited for, nor is a checkout waiting for a stable
        cart: that checkout waits for this run, so waiting for it in turn would
        hold both until their timeouts (I4).
        """
        if not conversation_id or not run_id:
            return True
        deadline = time.monotonic() + timeout
        with self._cond:
            conv = self._get(conversation_id)
            if conv is None:
                return False
            order = [rid for rid in conv.runs if rid != run_id]
            if run_id in conv.runs:
                order = list(conv.runs)[: list(conv.runs).index(run_id)]
            earlier = frozenset(order)
            while True:
                if conv.closed:
                    return False
                if not any(
                    rid in conv.runs and rid not in conv.waiting for rid in earlier
                ):
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
            if conv is None or conv.freeze_owner not in ("", run_id):
                return False
            conv.freeze_owner = run_id
            self._cond.notify_all()
            return True

    @staticmethod
    def _release_freeze(conv: _Conversation) -> None:
        conv.freeze_owner = ""
        conv.epoch += 1

    def unfreeze(self, conversation_id: str, run_id: str) -> None:
        if not conversation_id or not run_id:
            return
        with self._cond:
            conv = self._convs.get(conversation_id)
            if conv is not None and conv.freeze_owner == run_id:
                self._release_freeze(conv)
                self._cond.notify_all()

    def wait_unfrozen(self, conversation_id: str, run_id: str, timeout: float) -> bool:
        if not conversation_id or not run_id:
            return True
        deadline = time.monotonic() + timeout
        with self._cond:
            conv = self._get(conversation_id)
            if conv is None:
                return False
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
        """End the conversation: wake every waiter; later calls find it closed."""
        if not conversation_id:
            return
        with self._cond:
            conv = self._convs.pop(conversation_id, None)
            if conv is not None:
                conv.closed = True
            self._closed[conversation_id] = None
            self._closed.move_to_end(conversation_id)
            while len(self._closed) > self.MAX_CLOSED:
                self._closed.popitem(last=False)
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
