"""Whether an Agent reports a tool before running it.

The voice responder may end a turn early only then: on the compatibility path
the whole turn, tools included, runs before anything is reported.
"""

from __future__ import annotations

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.agents.runtime import NativeAgentRuntime
from tests.agents.test_orchestrator import StreamingEngine


class _CompatibilityEngine(StreamingEngine):
    def supports_semantic_reasoning_stream(self, model: str) -> bool:
        del model
        return False


def _binding(agent: OrchestratorAgent):
    return NativeAgentRuntime(agent).bind(model="m")


def test_native_streaming_reports_tools_first():
    agent = OrchestratorAgent(StreamingEngine(["x"]), "m")

    assert agent.streams_tool_starts() is True
    assert _binding(agent).streams_tool_starts() is True


def test_compatibility_engine_does_not():
    agent = OrchestratorAgent(_CompatibilityEngine(["x"]), "m")

    assert agent.streams_tool_starts() is False
    assert _binding(agent).streams_tool_starts() is False


def test_structured_mode_does_not():
    agent = OrchestratorAgent(StreamingEngine(["x"]), "m", mode="structured")

    assert agent.streams_tool_starts() is False
