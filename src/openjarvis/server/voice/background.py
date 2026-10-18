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


def run_report_text(run: LedgerEntry, tools: list[LedgerEntry]) -> str:
    """The event a fast model phrases for one finished background run.

    The run's answer was written before the customer spoke again, so it goes
    here as material to phrase, never as a sentence to read out.
    """
    parts = [
        "A request the customer made earlier has finished in the background; "
        "they may have said something else since.",
        f"Request: {run.request or 'not recorded'}.",
        f"Status: {run.status}.",
    ]
    if run.customer_message:
        parts.append(
            "The answer written before the customer spoke again: "
            f"{run.customer_message}"
        )
    for tool in tools:
        line = f"Tool: {tool.tool}. Status: {tool.status}."
        detail = tool.error if tool.status != "succeeded" else ""
        if tool.customer_message:
            line += f" Verified confirmation: {tool.customer_message}"
        if tool.event_context:
            line += f" Facts: {tool.event_context}"
        if detail:
            line += f" Detail: {detail}"
        parts.append(line)
    parts.append(
        "The conversation since may have changed what the customer wants: tell "
        "them only what still matters now, briefly and in their language. This "
        "is a notification, not a new request. Do not repeat any action."
    )
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
        # The order outcomes finished in, for reporting runs oldest-finished
        # first; the ledger lists them in the order they started.
        self._finish_order: dict[str, int] = {}

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
        self._finish_order.setdefault(entry.key, len(self._finish_order))
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
            while unit := self._next_unit():
                run_id, entry, tools = unit
                if run_id is not None:
                    # One report for the whole run; not awaited, like below.
                    self._event_task = asyncio.ensure_future(
                        self._report_run(run_id, entry, tools)
                    )
                    return
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

    def _next_unit(
        self,
    ) -> tuple[str | None, LedgerEntry, list[LedgerEntry]] | None:
        """The next thing to report: a whole finished run, or one lone entry.

        A run's tool entries are reported with its run entry, and not at all
        while the run still works. An entry whose run is not recorded (or was
        already reported) keeps the per-entry path.
        """
        units: list[tuple[str | None, LedgerEntry, list[LedgerEntry]]] = []
        for entry in self._ledger.unreported_finished(self._conversation_id):
            run, tools = self._ledger.run_group(self._conversation_id, entry.run_id)
            if run is None or run.reported:
                units.append((None, entry, []))
            elif entry.kind == "run" and run.status != "running":
                units.append((entry.run_id, run, tools))
        if not units:
            return None
        # Stable: outcomes whose finish was not observed keep ledger order.
        return min(
            units,
            key=lambda unit: self._finish_order.get(unit[1].key, float("inf")),
        )

    async def _report_run(
        self, run_id: str, run: LedgerEntry, tools: list[LedgerEntry]
    ) -> None:
        text = run_report_text(run, tools)
        outcome = "failed"
        if self._run_phrased_report is not None:
            outcome = await self._run_phrased_report(text)
        # Only what the text was built from: a tool still running then, or
        # finished since, has not been told and gets its own report.
        told = {run.key} | {t.key for t in tools if t.status != "running"}
        if outcome == "spoken":
            self._ledger.mark_run_reported(
                self._conversation_id, run_id, via="phrased", keys=told
            )
        elif outcome == "failed" and await self._run_event_turn(text):
            self._ledger.mark_run_reported(
                self._conversation_id, run_id, via="event_turn", keys=told
            )

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
