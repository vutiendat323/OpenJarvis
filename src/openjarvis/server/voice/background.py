"""Tell the customer about background outcomes that arrive while no turn runs.

A running turn sees them in ``background_actions`` and folds them into its
reply. When nobody is talking and no turn runs, a verified tool confirmation is
spoken as is; anything else (failure, unknown outcome, no confirmation) is
phrased by the model in a short event turn, so no sentence is hardcoded
(spec 2026-10-10 background actions §5.4). A confirmation from a request the
customer interrupted was written before they cut in, so the fast model phrases
it for the conversation as it is now instead (spec 2026-10-17 barge-in §5.6).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import copy_context
from typing import Any

from openjarvis.agents.action_ledger import ActionLedger, LedgerEntry


def event_text(entry: LedgerEntry) -> str:
    detail = entry.error or entry.event_context or "no confirmation was returned"
    return (
        "Kiosk event, not the customer's words: an action from the customer's "
        f"earlier request has finished. Tool: {entry.tool}. Status: "
        f"{entry.status}. Detail: {detail}. Tell the customer briefly what "
        "happened and what they can do next, following the workflow's instructions "
        "in your own words and the customer's language. This is a notification, "
        "not a new request. Do not repeat the action."
    )


def report_text(entry: LedgerEntry) -> str:
    """The event a fast model phrases for a late result of an interrupted request."""
    parts = [
        "An action from a request the customer interrupted has finished; they "
        "may have said something else since.",
        f"Tool: {entry.tool}. Status: {entry.status}.",
    ]
    if entry.customer_message:
        parts.append(f"Verified confirmation: {entry.customer_message}")
    if entry.event_context:
        parts.append(f"Facts: {entry.event_context}")
    return " ".join(parts)


class BackgroundReporter:
    def __init__(
        self,
        ledger: ActionLedger,
        conversation_id: str,
        *,
        speak: Callable[[str], Awaitable[None]],
        flush: Callable[[], Awaitable[None]],
        run_event_turn: Callable[[str], Awaitable[bool]],
        run_phrased_report: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._ledger = ledger
        self._conversation_id = conversation_id
        self._speak = speak
        self._flush = flush
        self._run_event_turn = run_event_turn
        self._run_phrased_report = run_phrased_report
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._event_task: asyncio.Task[Any] | None = None
        self._reporting = False
        self._context = copy_context()

    def start(self) -> None:
        """Subscribe on the event loop; outcomes arrive on worker threads."""
        self._loop = asyncio.get_running_loop()
        self._context = copy_context()
        self._unsubscribe = self._ledger.subscribe(
            self._conversation_id, self._finished
        )

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        task, self._event_task = self._event_task, None
        if task is not None and not task.done():
            # Its outcome stays unreported; nothing drives a dead session.
            task.cancel()

    def _finished(self, entry: LedgerEntry) -> None:
        del entry
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._flush()), context=self._context
        )

    async def on_idle(self) -> None:
        """Called by the session lifecycle only while nobody is talking.

        Not re-entrant: speaking goes back through the lifecycle's flush, which
        calls this again before the bot's audio has started.
        """
        if self._reporting or (
            self._event_task is not None and not self._event_task.done()
        ):
            return
        self._reporting = True
        try:
            while pending := self._ledger.unreported_finished(self._conversation_id):
                entry = pending[0]
                if entry.status == "succeeded" and entry.customer_message:
                    if entry.interrupted:
                        # Written before the customer cut in: never read out
                        # verbatim (spec 2026-10-17 barge-in §5.6).
                        self._event_task = asyncio.ensure_future(
                            self._report_phrased(entry)
                        )
                        return
                    # Marked before speaking: a TTS failure is not detectable
                    # here and a repeat would be worse than a lost confirmation.
                    self._ledger.mark_reported(
                        self._conversation_id, entry.key, via="reporter"
                    )
                    await self._speak(entry.customer_message)
                    continue
                # Not awaited: this runs inside the lifecycle's frame handling,
                # and the event turn's own frames pass through that processor.
                self._event_task = asyncio.ensure_future(self._report_by_event(entry))
                return
        finally:
            self._reporting = False

    async def _report_phrased(self, entry: LedgerEntry) -> None:
        outcome = "failed"
        if self._run_phrased_report is not None:
            outcome = await self._run_phrased_report(report_text(entry))
        if outcome == "spoken":
            self._ledger.mark_reported(self._conversation_id, entry.key, via="phrased")
        elif outcome == "failed":
            await self._report_by_event(entry)

    async def _report_by_event(self, entry: LedgerEntry) -> None:
        if await self._run_event_turn(event_text(entry)):
            self._ledger.mark_reported(
                self._conversation_id, entry.key, via="event_turn"
            )
