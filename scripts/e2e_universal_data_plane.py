#!/usr/bin/env python3
"""Opt-in live acceptance harness for the Universal Data Plane.

Read-only by default: cold discovery and sync against a real provider, a
process-level restart from the persisted database, then a warm sync that must
perform zero browser actions.

Every number printed is derived from the events the system already emits on the
``EventBus`` (the same stream the server relays) and from the receipts and
capabilities it already persists. There is deliberately no second metrics
service.

  uv run python scripts/e2e_universal_data_plane.py --source https://trendcoffee.net/

A mutation is refused unless the operator has BOTH configured an exact trusted
write fingerprint for the operation AND approved an action id for that exact
request. It runs at most once and is never retried.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjarvis.core.config import load_config
from openjarvis.core.events import Event, EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.data_plane.approval import parse_trusted_write_operations
from openjarvis.data_plane.errors import DataPlaneError
from openjarvis.data_plane.types import ReceiptStatus, TrustState
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.approval_store import STATUS_APPROVED, ApprovalStore

PRESET = Path("configs/openjarvis/examples/ordering-kiosk.toml")

_WATCHED_EVENTS = (
    EventType.SOURCE_DISCOVERY_STARTED,
    EventType.SOURCE_DISCOVERY_STAGE_COMPLETED,
    EventType.SOURCE_DISCOVERY_COMPLETED,
    EventType.SOURCE_CAPABILITY_VALIDATED,
    EventType.SOURCE_CAPABILITY_DEMOTED,
    EventType.SOURCE_SYNC_COMMITTED,
    EventType.SOURCE_SYNC_FAILED,
    EventType.SOURCE_EXECUTE_RECEIPT_CREATED,
    EventType.SOURCE_VERIFY_COMPLETED,
)


class HarnessError(RuntimeError):
    """A refusal or failed invariant, reported without a traceback."""


@dataclass(slots=True)
class EventRecorder:
    """The harness' only telemetry: what the system already publishes."""

    bus: EventBus
    events: list[Event] = field(default_factory=list)

    def attach(self) -> EventRecorder:
        for event_type in _WATCHED_EVENTS:
            self.bus.subscribe(event_type, self.events.append)
        return self

    def of(self, event_type: EventType) -> list[Event]:
        return [event for event in self.events if event.event_type is event_type]

    @property
    def stage_timings(self) -> list[tuple[str, float]]:
        started = self.of(EventType.SOURCE_DISCOVERY_STARTED)
        completed = self.of(EventType.SOURCE_DISCOVERY_STAGE_COMPLETED)
        timings: list[tuple[str, float]] = []
        previous = started[0].timestamp if started else 0.0
        for event in completed:
            stage = str(event.data.get("stage", "?"))
            timings.append((stage, (event.timestamp - previous) * 1000))
            previous = event.timestamp
        return timings

    @property
    def schema_demotions(self) -> int:
        return len(self.of(EventType.SOURCE_CAPABILITY_DEMOTED))

    @property
    def ambiguous_mutations(self) -> int:
        return sum(
            1
            for event in self.of(EventType.SOURCE_EXECUTE_RECEIPT_CREATED)
            if event.data.get("status") == ReceiptStatus.UNKNOWN.value
        )


def build_system(config, bus: EventBus):
    """Build the Kiosk preset exactly as the kiosk runs it."""
    return SystemBuilder(config).event_bus(bus).build()


def call_tool(system, name: str, **arguments: Any) -> ToolResult:
    """Invoke a tool the way the Agent does — through the executor."""
    if system.tool_executor is None:
        raise HarnessError("the preset built no tool executor")
    return system.tool_executor.execute(
        ToolCall(id=f"harness-{name}", name=name, arguments=json.dumps(arguments))
    )


def payload(result: ToolResult) -> dict[str, Any]:
    try:
        return json.loads(result.content)
    except json.JSONDecodeError as exc:
        raise HarnessError(f"{result.tool_name} did not return JSON: {exc}") from exc


def run_read_only(config, source: str) -> dict[str, Any]:
    """Cold discovery + sync, a restart, then a warm sync."""
    bus = EventBus()
    recorder = EventRecorder(bus).attach()

    cold_system = build_system(config, bus)
    try:
        started = time.monotonic()
        cold = payload(call_tool(cold_system, "source_discover", source_ref=source))
        cold_seconds = time.monotonic() - started
        discovery = cold.get("discovery", {})
        capability = discovery.get("capability")
        if (
            capability is None
            or discovery.get("state") != TrustState.READ_VALIDATED.value
        ):
            raise HarnessError(
                "cold discovery did not validate a read capability: "
                f"state={discovery.get('state')} "
                f"error_code={discovery.get('error_code')!r}"
            )
        source_id = str(capability["source_id"])
        print(f"cold discovery: {source_id} rev {capability['revision']}")
        for stage, elapsed_ms in recorder.stage_timings:
            print(f"  stage {stage:<24} {elapsed_ms:8.1f} ms")
    finally:
        cold_system.close()

    # The restart is the point: the warm path must answer from the database the
    # cold run persisted, not from anything still in memory.
    warm_system = build_system(config, bus)
    try:
        warm = payload(call_tool(warm_system, "source_discover", source_ref=source))
        cache_hit = bool(warm.get("discovery", {}).get("cache_hit"))
        browser_actions = int(warm.get("discovery", {}).get("browser_actions", 0))
        # The engine already knows which reads need nothing but engine-supplied
        # paging; that list is the warm path's scope.
        resources = warm_system.data_plane.direct.syncable_resources(source_id)
        started = time.monotonic()
        synced = call_tool(
            warm_system,
            "source_sync",
            source_id=source_id,
            resources=resources,
        )
        warm_seconds = time.monotonic() - started
        if not synced.success:
            raise HarnessError(f"warm sync failed: {payload(synced)}")
        if browser_actions:
            raise HarnessError(
                f"warm path performed {browser_actions} browser actions; "
                "discovery is HTTP-first only"
            )
        freshness = _snapshot_freshness(warm_system, source_id, resources)
    finally:
        warm_system.close()

    return {
        "source_id": source_id,
        "cold_seconds": cold_seconds,
        "warm_seconds": warm_seconds,
        "capability_cache_hit": cache_hit,
        "browser_actions": browser_actions,
        "snapshots": freshness,
        "schema_demotions": recorder.schema_demotions,
        "ambiguous_mutations": recorder.ambiguous_mutations,
    }


def _snapshot_freshness(system, source_id: str, resources: list[str]) -> dict[str, Any]:
    freshness: dict[str, Any] = {}
    for resource in resources:
        result = payload(
            call_tool(
                system,
                "structured_query",
                source_id=source_id,
                resource_type=resource,
                consistency="cached",
            )
        ).get("result", {})
        freshness[resource] = {
            "version": result.get("version"),
            "synced_at": result.get("synced_at"),
            "stale": result.get("stale"),
            "count": len(result.get("items", [])),
        }
    return freshness


@dataclass(frozen=True, slots=True)
class ApprovedRequest:
    """The one mutation an operator has already approved."""

    approval_id: str
    source_id: str
    operation: str
    arguments: dict[str, Any]


def resolve_approved_request(approval_id: str) -> ApprovedRequest:
    """Refuse an unauthorized mutation before the provider is touched at all."""
    if not approval_id:
        raise HarnessError("--allow-order refused: --approval-id is required")
    store = ApprovalStore()
    try:
        action = store.get_action(approval_id)
        if (
            action is None
            or action.action_type != "source_execute"
            or action.status != STATUS_APPROVED
        ):
            raise HarnessError(
                f"--allow-order refused: action {approval_id!r} is not an "
                "approved source_execute"
            )
        source_id = str(action.payload.get("source_id", ""))
        operation = str(action.payload.get("operation", ""))
        arguments = action.payload.get("preview")
        if not source_id or not operation or not isinstance(arguments, dict):
            raise HarnessError(
                f"--allow-order refused: action {approval_id!r} has no exact "
                "source, operation and request preview"
            )
    finally:
        store.close()
    return ApprovedRequest(approval_id, source_id, operation, dict(arguments))


def run_order(config, request: ApprovedRequest) -> None:
    """Run one authorized mutation, or refuse before touching the provider."""
    bus = EventBus()
    recorder = EventRecorder(bus).attach()
    system = build_system(config, bus)
    try:
        capability = system.data_plane.discovery.get_capability(request.source_id)
        if capability is None:
            raise HarnessError(
                "--allow-order refused: no capability on file for "
                f"{request.source_id!r}; run the read-only pass first"
            )
        _require_exact_write_trust(config, capability, request.operation)

        # The preview is a candidate, not the authority: `source_execute`
        # re-hashes these arguments and the gate rejects anything that is not
        # byte-for-byte the approved request.
        print(f"exact request preview for {request.operation} on {request.source_id}:")
        print(
            json.dumps(request.arguments, ensure_ascii=False, indent=2, sort_keys=True)
        )

        receipt = call_tool(
            system,
            "source_execute",
            source_id=request.source_id,
            operation=request.operation,
            arguments=request.arguments,
            approval_id=request.approval_id,
        )
        print(f"receipt: {receipt.content}")
        if not receipt.success:
            # Executed once or refused before dispatch; either way, no retry.
            raise HarnessError("mutation was not accepted; not retrying")
        verified = call_tool(
            system,
            "source_verify",
            receipt_id=payload(receipt)["receipt"]["receipt_id"],
        )
        print(f"verification: {verified.content}")
        print(f"ambiguous mutations: {recorder.ambiguous_mutations}")
    finally:
        system.close()


def _require_exact_write_trust(config, capability, operation: str) -> None:
    trusted = parse_trusted_write_operations(config.data_plane.trusted_write_operations)
    identity = (capability.provider, operation, capability.fingerprint)
    if identity not in {
        (entry.provider, entry.operation, entry.fingerprint) for entry in trusted
    }:
        raise HarnessError(
            "--allow-order refused: no exact trusted write entry for "
            f"{capability.provider}:{operation}:{capability.fingerprint}"
        )
    contract = capability.operations.get(operation)
    if contract is None or contract.trust is not TrustState.WRITE_VALIDATED:
        raise HarnessError(
            f"--allow-order refused: {operation} is not write validated on the "
            "capability on file"
        )


def print_summary(summary: dict[str, Any]) -> None:
    print("")
    print(f"source                {summary['source_id']}")
    print(f"cold latency          {summary['cold_seconds']:.2f}s")
    print(f"warm latency          {summary['warm_seconds']:.2f}s")
    print(f"capability cache hit  {summary['capability_cache_hit']}")
    print(f"browser actions       {summary['browser_actions']}")
    print(f"schema demotions      {summary['schema_demotions']}")
    print(f"ambiguous mutations   {summary['ambiguous_mutations']}")
    for resource, state in summary["snapshots"].items():
        print(
            f"snapshot {resource:<12} v{state['version']} "
            f"{state['count']} records stale={state['stale']} "
            f"synced_at={state['synced_at']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        default="",
        help="Source origin to discover (defaults to the preset's source_url).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=True,
        help="Discovery and sync only. The default.",
    )
    parser.add_argument(
        "--allow-order",
        action="store_false",
        dest="read_only",
        help=(
            "Also run the one mutation named by --approval-id. Refused unless "
            "the config carries an exact trusted write fingerprint for that "
            "operation AND the action id is approved."
        ),
    )
    parser.add_argument(
        "--approval-id",
        default="",
        help="The approved action id authorizing --allow-order.",
    )
    args = parser.parse_args(argv)

    config = load_config(PRESET)
    if not config.data_plane.enabled:
        print("data_plane is disabled in the preset", file=sys.stderr)
        return 2
    source = args.source or config.data_plane.source_url

    try:
        request = None if args.read_only else resolve_approved_request(args.approval_id)
        print_summary(run_read_only(config, source))
        if request is not None:
            run_order(config, request)
    except (HarnessError, DataPlaneError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
