"""Each dispatched write and its outcome, per conversation, until reported.

A turn interrupted by the customer loses its model work but not the tools it
already dispatched (AgentWorkerLease keeps them running). This ledger keeps
their outcomes so a later turn, or the voice layer when no turn runs, can tell
the customer exactly once (spec 2026-10-10-background-actions-design.md).
Entries are written from tool worker threads; every method is thread-safe.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace

from openjarvis.core.types import ToolResult

logger = logging.getLogger("openjarvis.agents.action_ledger")

Listener = Callable[["LedgerEntry"], None]


@dataclass
class LedgerEntry:
    key: str
    call_id: str
    tool: str
    run_id: str
    status: str = "running"
    customer_message: str = ""
    error: str = ""
    seen_by_run: str = ""
    reported: bool = False
    # A customer turn completed after it: told, and no longer carried.
    settled: bool = False

    def as_context(self) -> dict[str, str]:
        item = {"tool": self.tool, "status": self.status}
        if self.customer_message:
            item["message"] = self.customer_message
        if self.error:
            item["error"] = self.error
        return item


class ActionLedger:
    MAX_ENTRIES = 20
    MAX_CONVERSATIONS = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conversations: OrderedDict[str, OrderedDict[str, LedgerEntry]] = (
            OrderedDict()
        )
        self._listeners: dict[str, list[Listener]] = {}
        # Conversations whose interrupted request is carried into the next turn.
        self._carrying: set[str] = set()

    @staticmethod
    def _key(run_id: str, call_id: str) -> str:
        return f"{run_id}:{call_id}"

    def start(
        self, conversation_id: str, *, call_id: str, tool: str, run_id: str
    ) -> None:
        key = self._key(run_id, call_id)
        with self._lock:
            entries = self._conversations.setdefault(conversation_id, OrderedDict())
            self._conversations.move_to_end(conversation_id)
            entries[key] = LedgerEntry(
                key=key, call_id=call_id, tool=tool, run_id=run_id
            )
            while len(entries) > self.MAX_ENTRIES:
                entries.popitem(last=False)
            while len(self._conversations) > self.MAX_CONVERSATIONS:
                evicted, _ = self._conversations.popitem(last=False)
                self._listeners.pop(evicted, None)
                self._carrying.discard(evicted)
        logger.warning(
            "background_action_started tool=%s run=%s call=%s", tool, run_id, call_id
        )

    def finish(
        self, conversation_id: str, *, call_id: str, run_id: str, result: ToolResult
    ) -> None:
        key = self._key(run_id, call_id)
        metadata = result.metadata or {}
        with self._lock:
            entries = self._conversations.get(conversation_id)
            entry = entries.get(key) if entries is not None else None
            if entry is None:
                status = "succeeded" if result.success else "failed"
                logger.warning(
                    "background_action_after_session_end tool=%s status=%s",
                    result.tool_name,
                    status,
                )
                return
            if metadata.get("dispatched") is False:
                del entries[key]
                return
            if result.success:
                entry.status = "succeeded"
                message = metadata.get("customer_message")
                entry.customer_message = (
                    message.strip() if isinstance(message, str) else ""
                )
            elif metadata.get("outcome_unknown"):
                entry.status = "unknown"
                entry.error = (result.content or "")[:200]
            else:
                entry.status = "failed"
                entry.error = (result.content or "")[:200]
            snapshot = replace(entry)
            listeners = list(self._listeners.get(conversation_id, ()))
        logger.warning(
            "background_action_finished tool=%s status=%s run=%s call=%s",
            snapshot.tool,
            snapshot.status,
            run_id,
            call_id,
        )
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                # Tool name only: exception text can carry customer words.
                logger.warning(
                    "background_action_listener_failed tool=%s", snapshot.tool
                )

    def background_for(self, conversation_id: str, run_id: str) -> list[dict[str, str]]:
        """Earlier runs' writes still running or not yet reported.

        Running entries are listed but not marked seen: the run has only seen
        "not done yet", so its outcome must still be reported later. While an
        interrupted request is carried, what it already told is listed too,
        marked ``already_told``, so the carried request does not do it again.
        """
        with self._lock:
            carrying = conversation_id in self._carrying
            listed = []
            for entry in self._conversations.get(conversation_id, {}).values():
                if entry.run_id == run_id:
                    continue
                if entry.reported:
                    if carrying and not entry.settled:
                        listed.append({**entry.as_context(), "already_told": "true"})
                    continue
                if entry.status != "running":
                    entry.seen_by_run = run_id
                listed.append(entry.as_context())
            return listed

    def carry(self, conversation_id: str) -> None:
        """An interrupted request is carried into the conversation's next turn."""
        with self._lock:
            self._carrying.add(conversation_id)

    def settle(self, conversation_id: str) -> None:
        """A customer turn completed: nothing earlier is carried any more."""
        with self._lock:
            self._carrying.discard(conversation_id)
            for entry in self._conversations.get(conversation_id, {}).values():
                entry.settled = True

    def unseen_failures(self, conversation_id: str, run_id: str) -> list[LedgerEntry]:
        """Earlier runs' failed or unknown outcomes this run's model has not read.

        A later write must not go ahead past them (spec D4): the customer is
        told and asked first.
        """
        with self._lock:
            return [
                replace(entry)
                for entry in self._conversations.get(conversation_id, {}).values()
                if entry.run_id != run_id
                and entry.status in ("failed", "unknown")
                and not entry.reported
                and entry.seen_by_run != run_id
            ]

    def mark_run_completed(self, conversation_id: str, run_id: str) -> None:
        """A run that ended normally told the customer what it saw and did."""
        with self._lock:
            for entry in self._conversations.get(conversation_id, {}).values():
                if entry.status == "running" or entry.reported:
                    continue
                if entry.run_id == run_id or entry.seen_by_run == run_id:
                    entry.reported = True
                    logger.warning(
                        "background_action_reported via=model tool=%s run=%s call=%s",
                        entry.tool,
                        entry.run_id,
                        entry.call_id,
                    )

    def mark_reported(self, conversation_id: str, key: str, *, via: str) -> None:
        with self._lock:
            entry = self._conversations.get(conversation_id, {}).get(key)
            if entry is None or entry.reported:
                return
            entry.reported = True
        logger.warning(
            "background_action_reported via=%s tool=%s run=%s call=%s",
            via,
            entry.tool,
            entry.run_id,
            entry.call_id,
        )

    def unreported_finished(self, conversation_id: str) -> list[LedgerEntry]:
        with self._lock:
            return [
                replace(entry)
                for entry in self._conversations.get(conversation_id, {}).values()
                if entry.status != "running" and not entry.reported
            ]

    def entries(self, conversation_id: str) -> list[LedgerEntry]:
        with self._lock:
            return [
                replace(entry)
                for entry in self._conversations.get(conversation_id, {}).values()
            ]

    def subscribe(self, conversation_id: str, listener: Listener) -> Callable[[], None]:
        with self._lock:
            self._listeners.setdefault(conversation_id, []).append(listener)

        def unsubscribe() -> None:
            with self._lock:
                listeners = self._listeners.get(conversation_id, [])
                if listener in listeners:
                    listeners.remove(listener)

        return unsubscribe

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._conversations.pop(conversation_id, None)
            self._listeners.pop(conversation_id, None)
            self._carrying.discard(conversation_id)


ACTION_LEDGER = ActionLedger()

__all__ = ["ACTION_LEDGER", "ActionLedger", "LedgerEntry"]
