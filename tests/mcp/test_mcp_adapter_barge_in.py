"""A model's direct MCP tool call stays cancellable by barge-in (spec 2026-10-17 §7)."""

from __future__ import annotations

import contextvars
import threading

from openjarvis.agents._stubs import _RUN_WORKER_LEASE, AgentWorkerLease
from openjarvis.core.types import ToolCall
from openjarvis.mcp.client import MCPClient
from openjarvis.tools._stubs import ToolExecutor, ToolSpec
from openjarvis.tools.mcp_adapter import MCPToolAdapter
from tests.mcp.test_client import _CancellableSession, _SDKTransportDouble


def test_a_direct_model_mcp_call_through_the_executor_is_cut_by_barge_in(
    monkeypatch,
):
    from openjarvis.mcp.runtime import MCPRuntime

    session = _CancellableSession()

    class _Runtime(MCPRuntime):
        def __init__(self, transport):
            super().__init__(transport, session_factory=lambda *a, **k: session)

    monkeypatch.setattr("openjarvis.mcp.runtime.MCPRuntime", _Runtime)
    lease = AgentWorkerLease()
    client = MCPClient(_SDKTransportDouble(), server_name="playwright")
    adapter = MCPToolAdapter(
        client,
        ToolSpec(
            name="browser_navigate",
            description="x",
            parameters={"type": "object", "properties": {}},
        ),
    )
    executor = ToolExecutor([adapter])
    results: list = []

    def call():
        _RUN_WORKER_LEASE.set(lease)
        results.append(executor.execute(ToolCall("1", "browser_navigate", "{}")))

    def barge_in():
        _RUN_WORKER_LEASE.set(lease)
        lease._signal_cancelled()

    worker = threading.Thread(
        target=lambda: contextvars.copy_context().run(call), daemon=True
    )
    worker.start()
    try:
        assert session.call_started.wait(5)
        barge = threading.Thread(
            target=lambda: contextvars.copy_context().run(barge_in), daemon=True
        )
        barge.start()
        barge.join(5)
        worker.join(2)
        assert not worker.is_alive()
        assert results and results[0].success is False
    finally:
        # Free a call the barge-in failed to cut, so the test cannot hang.
        client._runtime._loop.call_soon_threadsafe(session._release.set)
        worker.join(5)
        client.close()
