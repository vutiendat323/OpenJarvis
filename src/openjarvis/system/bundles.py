"""Bundle dataclasses that group cohesive subsystems of JarvisSystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjarvis.agents._stubs import BaseAgent
    from openjarvis.agents.executor import AgentExecutor
    from openjarvis.agents.manager import AgentManager
    from openjarvis.agents.scheduler import AgentScheduler
    from openjarvis.data_plane.approval import ExecutionApprovalGate
    from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
    from openjarvis.data_plane.discovery import DiscoveryEngine
    from openjarvis.data_plane.execution import DirectExecutionEngine
    from openjarvis.data_plane.snapshot_store import StructuredSnapshotStore
    from openjarvis.scheduler.scheduler import TaskScheduler
    from openjarvis.scheduler.store import SchedulerStore
    from openjarvis.security.audit import AuditLogger
    from openjarvis.security.boundary import BoundaryGuard
    from openjarvis.security.capabilities import CapabilityPolicy
    from openjarvis.telemetry.gpu_monitor import GpuMonitor
    from openjarvis.telemetry.store import TelemetryStore
    from openjarvis.traces.collector import TraceCollector
    from openjarvis.traces.store import TraceStore


@dataclass
class SecurityContext:
    """Security policy, audit, and boundary enforcement."""

    capability_policy: Optional[CapabilityPolicy] = None
    audit_logger: Optional[AuditLogger] = None
    boundary_guard: Optional[BoundaryGuard] = None


@dataclass
class Observability:
    """Telemetry, traces, and hardware monitoring."""

    telemetry_store: Optional[TelemetryStore] = None
    trace_store: Optional[TraceStore] = None
    trace_collector: Optional[TraceCollector] = None
    gpu_monitor: Optional[GpuMonitor] = None


@dataclass
class AgentRuntime:
    """Active agent and agent lifecycle managers."""

    agent: Optional[BaseAgent] = None
    agent_name: str = ""
    manager: Optional[AgentManager] = None
    scheduler: Optional[AgentScheduler] = None
    executor: Optional[AgentExecutor] = None


@dataclass
class Scheduling:
    """Task scheduler and its persistent store."""

    store: Optional[SchedulerStore] = None
    runner: Optional[TaskScheduler] = None


@dataclass
class DataPlaneRuntime:
    """One owned composition of the three Data Plane modules."""

    capabilities: SQLiteCapabilityStore
    snapshots: StructuredSnapshotStore
    discovery: DiscoveryEngine
    direct: DirectExecutionEngine
    approval_gate: ExecutionApprovalGate | None = None
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        """Release Data Plane-owned resources exactly once."""
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for owner in (
            self.discovery,
            self.direct,
            self.approval_gate,
            self.snapshots,
            self.capabilities,
        ):
            if owner is None:
                continue
            try:
                owner.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
