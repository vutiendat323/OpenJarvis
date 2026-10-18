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
    # Verified workflow facts for a model-written notification, never spoken text.
    event_context: str = ""
    # Its run was cut by the customer: a late result is phrased, not read out
    # (spec 2026-10-17 barge-in §5.6). Never shown to the model.
    interrupted: bool = False
    # "tool": one dispatched write. "run": the whole background Agent run, so a
    # run without writes is still seen and reported as one unit (spec
    # 2026-10-18 parallel agent runs).
    kind: str = "tool"
    request: str = ""

    def as_context(self) -> dict[str, str]:
        if self.kind == "run":
            run_item = {"request": self.request, "status": self.status}
            if self.customer_message:
                run_item["answer"] = self.customer_message
            return run_item
        item = {"tool": self.tool, "status": self.status}
        if self.event_context:
            item["event_context"] = self.event_context
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
        # Runs the customer interrupted, per conversation.
        self._interrupted_runs: dict[str, set[str]] = {}

    @staticmethod
    def _key(run_id: str, call_id: str) -> str:
        return f"{run_id}:{call_id}"

    def start(
        self, conversation_id: str, *, call_id: str, tool: str, run_id: str
    ) -> None:
        self._add(conversation_id, call_id=call_id, tool=tool, run_id=run_id)
        logger.warning(
            "background_action_started tool=%s run=%s call=%s", tool, run_id, call_id
        )

    def start_run(self, conversation_id: str, run_id: str, request: str) -> None:
        """A background Agent run begins: one entry for the run as a whole."""
        self._add(
            conversation_id,
            call_id="run",
            tool="agent_run",
            run_id=run_id,
            kind="run",
            request=request,
        )
        logger.warning("background_run_started run=%s", run_id)

    def _add(
        self,
        conversation_id: str,
        *,
        call_id: str,
        tool: str,
        run_id: str,
        kind: str = "tool",
        request: str = "",
    ) -> None:
        key = self._key(run_id, call_id)
        with self._lock:
            entries = self._conversations.setdefault(conversation_id, OrderedDict())
            self._conversations.move_to_end(conversation_id)
            entries[key] = LedgerEntry(
                key=key,
                call_id=call_id,
                tool=tool,
                run_id=run_id,
                interrupted=run_id in self._interrupted_runs.get(conversation_id, ()),
                kind=kind,
                request=request,
            )
            while len(entries) > self.MAX_ENTRIES:
                entries.popitem(last=False)
            while len(self._conversations) > self.MAX_CONVERSATIONS:
                evicted, _ = self._conversations.popitem(last=False)
                self._listeners.pop(evicted, None)
                self._carrying.discard(evicted)
                self._interrupted_runs.pop(evicted, None)

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
            if (
                metadata.get("dispatched") is False
                or metadata.get("execution_started") is False
            ):
                # Refused before it acted: not a write (spec 2026-10-17 §5.2).
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
            event_context = metadata.get("event_context")
            entry.event_context = (
                event_context if isinstance(event_context, str) else ""
            )
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

    def finish_run(
        self, conversation_id: str, run_id: str, *, answer: str, status: str
    ) -> None:
        """The run ended: ``succeeded`` or ``stopped``, with what it answered."""
        key = self._key(run_id, "run")
        with self._lock:
            entries = self._conversations.get(conversation_id)
            entry = entries.get(key) if entries is not None else None
            if entry is None:
                logger.warning(
                    "background_run_after_session_end status=%s run=%s", status, run_id
                )
                return
            entry.status = status
            entry.customer_message = answer.strip() if isinstance(answer, str) else ""
            snapshot = replace(entry)
            listeners = list(self._listeners.get(conversation_id, ()))
        logger.warning("background_run_finished status=%s run=%s", status, run_id)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:
                logger.warning("background_run_listener_failed run=%s", run_id)

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

    def mark_run_interrupted(self, conversation_id: str, run_id: str) -> None:
        """The customer cut this run: flag its entries, now and later."""
        with self._lock:
            self._interrupted_runs.setdefault(conversation_id, set()).add(run_id)
            for entry in self._conversations.get(conversation_id, {}).values():
                if entry.run_id == run_id:
                    entry.interrupted = True

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
                if entry.kind == "tool"
                and entry.run_id != run_id
                and entry.status in ("failed", "unknown")
                and not entry.reported
                and entry.seen_by_run != run_id
            ]

    def mark_run_completed(self, conversation_id: str, run_id: str) -> None:
        """A run that ended normally told the customer what it saw and did.

        A run any of whose entries is folded in here is reported whole: its
        other finished entries, the run entry included, are marked too. One
        still running is left, so its own outcome is still reported later.
        """
        with self._lock:
            entries = list(self._conversations.get(conversation_id, {}).values())
            folded_runs: set[str] = set()
            for entry in entries:
                if entry.status == "running" or entry.reported:
                    continue
                if entry.run_id == run_id or entry.seen_by_run == run_id:
                    self._report_locked(entry, "model")
                    folded_runs.add(entry.run_id)
            for entry in entries:
                if (
                    entry.run_id in folded_runs
                    and entry.status != "running"
                    and not entry.reported
                ):
                    self._report_locked(entry, "model")

    def mark_run_reported(self, conversation_id: str, run_id: str, *, via: str) -> None:
        """Every finished entry of this run, the run entry included, is now told.

        An entry still running is left, as in ``mark_run_completed``: its own
        outcome is still to be reported once it finishes.
        """
        with self._lock:
            for entry in self._conversations.get(conversation_id, {}).values():
                if (
                    entry.run_id == run_id
                    and entry.status != "running"
                    and not entry.reported
                ):
                    self._report_locked(entry, via)

    @staticmethod
    def _report_locked(entry: LedgerEntry, via: str) -> None:
        entry.reported = True
        logger.warning(
            "background_action_reported via=%s tool=%s run=%s call=%s",
            via,
            entry.tool,
            entry.run_id,
            entry.call_id,
        )

    def run_group(
        self, conversation_id: str, run_id: str
    ) -> tuple[LedgerEntry | None, list[LedgerEntry]]:
        """Copies of a run's own entry and of its tool entries, oldest first."""
        with self._lock:
            run_entry: LedgerEntry | None = None
            tools: list[LedgerEntry] = []
            for entry in self._conversations.get(conversation_id, {}).values():
                if entry.run_id != run_id:
                    continue
                if entry.kind == "run":
                    run_entry = replace(entry)
                else:
                    tools.append(replace(entry))
            return run_entry, tools

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
            self._interrupted_runs.pop(conversation_id, None)


ACTION_LEDGER = ActionLedger()

__all__ = ["ACTION_LEDGER", "ActionLedger", "LedgerEntry"]
