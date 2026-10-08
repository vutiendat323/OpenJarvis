"""Configured goals over existing tools; no language or merchant routing in core."""

import json

import pytest

from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from tests.agents.fake_engine import FakeEngine


@pytest.mark.parametrize("goal,expected_rounds", [("NONE", 1), ("FINALIZE", 2)])
def test_verified_display_acknowledgement_ends_only_unrelated_information(
    goal, expected_rounds
):
    class AcknowledgedDisplay(ExistingTool):
        def execute(self, **params):
            result = super().execute(**params)
            result.metadata["completed_display"] = True
            return result

    display = AcknowledgedDisplay("show")
    target = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("show", goal)]},
            {"tool_calls": [invoke("finish", "FINALIZE")]},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[display, target], intent_normalization=True
    ).run("request")
    assert engine.call_count == expected_rounds
    assert result.content == "verified result"
    if goal == "NONE":
        assert target.calls == []
        assert result.metadata["terminal_reason"] == "ANSWER"
    else:
        assert target.calls == [{}]
        assert "customer_message" not in result.tool_results[0].metadata


@pytest.mark.parametrize(
    "failure", ["empty_message", "failed_display", "continue_requested"]
)
def test_display_acknowledgement_requires_success_message_and_no_continuation(failure):
    class IncompleteDisplay(ExistingTool):
        def execute(self, **params):
            result = super().execute(**params)
            result.metadata["completed_display"] = True
            if failure == "empty_message":
                result.metadata["customer_message"] = "  "
            elif failure == "failed_display":
                result.success = False
            else:
                result.metadata["continue_agent"] = True
            return result

    display = IncompleteDisplay("show")
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("show", "NONE")]},
            {
                "content": json.dumps(
                    {"_intent": "NONE", "state": "ANSWER", "message": "Final response."}
                )
            },
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[display], intent_normalization=True
    ).run("request")
    assert engine.call_count == 2
    assert result.content == "Final response."


def test_none_preparation_requires_explicit_final_normalization():
    prepare = ExistingTool("prepare")
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("prepare", "NONE")]},
            {
                "content": json.dumps(
                    {
                        "_intent": "NONE",
                        "state": "ANSWER",
                        "message": "Information only.",
                    }
                )
            },
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[prepare, finish], intent_normalization=True
    ).run("request")
    assert engine.call_count == 2
    assert result.content == "Information only."
    assert result.metadata["intent_satisfied"] is False


def test_first_plain_success_without_normalization_is_blocked():
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"content": "Finalized successfully."},
            {
                "content": json.dumps(
                    {
                        "_intent": "FINALIZE",
                        "state": "NEEDS_INPUT",
                        "message": "Please clarify.",
                    }
                )
            },
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[finish], intent_normalization=True
    ).run("request")
    assert result.content == "Please clarify."
    assert finish.calls == []
    assert result.metadata["intent_satisfied"] is False


def test_failed_target_allows_read_only_outcome_observation():
    finish = ExistingTool(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}, fail=True
    )
    status = ExistingTool("status", metadata={"read_only": True})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("finish", "FINALIZE")]},
            {"tool_calls": [invoke("status", "FINALIZE")]},
            {"content": json.dumps({"state": "FAILED", "message": "Unknown outcome."})},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[finish, status], intent_normalization=True
    ).run("request")
    assert finish.calls == [{}]
    assert status.calls == [{}]
    assert result.metadata["intent_satisfied"] is False


def test_structured_mode_rejects_unsupported_intent_normalization():
    with pytest.raises(ValueError, match="function_calling"):
        OrchestratorAgent(
            FakeEngine([]), "fake", mode="structured", intent_normalization=True
        )


def test_invalid_final_json_does_not_freeze_an_undeclared_goal():
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {
                "content": json.dumps(
                    {"_intent": "NONE", "state": "INVALID", "message": "x"}
                )
            },
            {"tool_calls": [invoke("finish", "FINALIZE")]},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[finish], intent_normalization=True
    ).run("request")
    assert finish.calls == [{}]
    assert result.metadata["intent_satisfied"] is True


def test_read_only_skill_metadata_is_exposed_to_the_generic_dispatch_guard():
    from openjarvis.skills.executor import SkillExecutor
    from openjarvis.skills.tool_adapter import SkillTool
    from openjarvis.skills.types import SkillManifest
    from openjarvis.tools._stubs import ToolExecutor

    observer = SkillTool(
        SkillManifest(name="status", metadata={"openjarvis": {"read_only": True}}),
        SkillExecutor(ToolExecutor([])),
    )
    assert observer.spec.metadata["read_only"] is True


def test_successful_target_requesting_continuation_allows_followup():
    class ContinuingTarget(ExistingTool):
        def execute(self, **params):
            result = super().execute(**params)
            result.metadata["continue_agent"] = True
            return result

    target = ContinuingTarget(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}
    )
    followup = ExistingTool("configure")
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("finish", "FINALIZE")]},
            {"tool_calls": [invoke("configure", "FINALIZE")]},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[target, followup], intent_normalization=True
    ).run("request")
    assert followup.calls == [{}]
    assert result.content == "verified result"


def test_failed_terminal_envelope_after_target_is_not_exposed_as_json():
    class ContinuingTarget(ExistingTool):
        def execute(self, **params):
            result = super().execute(**params)
            result.metadata["continue_agent"] = True
            return result

    target = ContinuingTarget(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}
    )
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("finish", "FINALIZE")]},
            {
                "content": json.dumps(
                    {
                        "_intent": "FINALIZE",
                        "state": "FAILED",
                        "message": "Configuration was not saved.",
                    }
                )
            },
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[target], intent_normalization=True
    ).run("request")
    assert result.content == "Configuration was not saved."
    assert result.metadata["terminal_reason"] == "FAILED"
    assert result.metadata["intent_satisfied"] is False


@pytest.mark.asyncio
async def test_stream_does_not_speak_terminal_json_after_nonterminal_target():
    from openjarvis.agents._stubs import AgentTextDelta
    from openjarvis.engine._stubs import StreamChunk
    from tests.agents.test_orchestrator import StreamingEngine

    class ContinuingTarget(ExistingTool):
        def execute(self, **params):
            result = super().execute(**params)
            result.metadata["continue_agent"] = True
            return result

    target = ContinuingTarget(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}
    )
    engine = StreamingEngine(
        [
            [
                StreamChunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "f",
                            "function": {
                                "name": "finish",
                                "arguments": json.dumps({"_intent": "FINALIZE"}),
                            },
                        }
                    ]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ],
            [
                StreamChunk(
                    content=json.dumps(
                        {
                            "_intent": "FINALIZE",
                            "state": "FAILED",
                            "message": "Configuration was not saved.",
                        }
                    )
                ),
                StreamChunk(finish_reason="stop"),
            ],
        ]
    )
    events = [
        event
        async for event in OrchestratorAgent(
            engine,
            "fake",
            tools=[target],
            intent_normalization=True,
        ).run_stream("request")
    ]
    assert [event.content for event in events if isinstance(event, AgentTextDelta)] == [
        "Configuration was not saved."
    ]
    assert events[-1].result.metadata["intent_satisfied"] is False


def test_rejected_none_target_does_not_prevent_correcting_intent_next_turn():
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("finish", "NONE")]},
            {"tool_calls": [invoke("finish", "FINALIZE")]},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[finish], intent_normalization=True
    ).run("request")
    assert finish.calls == [{}]
    assert result.metadata["normalized_intent"] == "FINALIZE"
    assert result.metadata["intent_satisfied"] is True


def test_configured_arguments_are_bound_from_verified_runtime_without_model_copy():
    class RuntimeTool(ExistingTool):
        def agent_context(self):
            return {"verified": {"finish": False}}

    target = RuntimeTool(
        "finish",
        metadata={
            "intent_contract": {"name": "FINALIZE"},
            "runtime_arguments": {"finish_turn": "verified.finish"},
        },
    )
    engine = FakeEngine(
        [{"tool_calls": [invoke("finish", "FINALIZE", finish_turn=True)]}]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[target], intent_normalization=True
    ).run("request")
    assert target.calls == [{"finish_turn": False}]
    assert result.metadata["intent_satisfied"] is True


def test_missing_runtime_binding_blocks_dispatch():
    target = ExistingTool(
        "finish",
        metadata={
            "intent_contract": {"name": "FINALIZE"},
            "runtime_arguments": {"finish_turn": "verified.finish"},
        },
    )
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("finish", "FINALIZE")]},
            {"content": json.dumps({"state": "FAILED", "message": "Unavailable."})},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[target], intent_normalization=True
    ).run("request")
    assert target.calls == []
    assert result.metadata["intent_satisfied"] is False


class ExistingTool(BaseTool):
    def __init__(self, name, *, metadata=None, fail=False):
        self.name, self.metadata, self.fail = name, metadata or {}, fail
        self.calls = []

    @property
    def spec(self):
        return ToolSpec(
            name=self.name,
            description="Existing test capability",
            parameters={
                "type": "object",
                "properties": {"finish_turn": {"type": "boolean"}},
            },
            metadata=self.metadata,
        )

    def execute(self, **params):
        self.calls.append(params)
        return ToolResult(
            tool_name=self.name,
            content="evidence",
            success=not self.fail,
            metadata={"complete_turn": True, "customer_message": "verified result"},
        )


def test_trace_records_canonical_goal_and_execution_path():
    from openjarvis.traces.collector import TraceCollector

    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    collector = TraceCollector(
        OrchestratorAgent(
            FakeEngine([{"tool_calls": [invoke("finish", "FINALIZE")]}]),
            "fake",
            tools=[finish],
            intent_normalization=True,
        )
    )
    collector.run("request")
    assert collector.last_trace.metadata["normalized_intent"] == "FINALIZE"
    assert collector.last_trace.metadata["execution_path"] == ["finish"]
    assert collector.last_trace.metadata["intent_satisfied"] is True


@pytest.mark.asyncio
async def test_pending_approval_retains_intent_in_stream_and_trace():
    from openjarvis.agents._stubs import AgentTextDelta
    from openjarvis.engine._stubs import StreamChunk
    from openjarvis.traces.collector import TraceCollector
    from tests.agents.test_orchestrator import StreamingEngine

    class AwaitingApproval(ExistingTool):
        def execute(self, **params):
            return ToolResult(
                tool_name=self.name, content="", metadata={"pending_approval": True}
            )

    target = AwaitingApproval(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}
    )
    engine = StreamingEngine(
        [
            [
                StreamChunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "f",
                            "function": {
                                "name": "finish",
                                "arguments": json.dumps({"_intent": "FINALIZE"}),
                            },
                        }
                    ]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ]
        ]
    )
    collector = TraceCollector(
        OrchestratorAgent(
            engine,
            "fake",
            tools=[target],
            intent_normalization=True,
        )
    )
    events = [event async for event in collector.run_stream("request")]
    assert not any(isinstance(event, AgentTextDelta) for event in events)
    assert events[-1].result.metadata["pending_approval"] is True
    assert events[-1].result.metadata["intent_satisfied"] is False
    assert collector.last_trace.metadata["normalized_intent"] == "FINALIZE"
    assert collector.last_trace.metadata["execution_path"] == ["finish"]


@pytest.mark.asyncio
async def test_no_tool_stream_exposes_only_validated_final_message():
    from openjarvis.agents._stubs import AgentTextDelta
    from openjarvis.engine._stubs import StreamChunk
    from tests.agents.test_orchestrator import StreamingEngine

    engine = StreamingEngine(
        [
            [
                StreamChunk(content="Done without evidence."),
                StreamChunk(finish_reason="stop"),
            ],
            [
                StreamChunk(
                    content=json.dumps(
                        {
                            "_intent": "NONE",
                            "state": "ANSWER",
                            "message": "Information.",
                        }
                    )
                ),
                StreamChunk(finish_reason="stop"),
            ],
        ]
    )
    events = [
        event
        async for event in OrchestratorAgent(
            engine,
            "fake",
            tools=[],
            intent_normalization=True,
        ).run_stream("request")
    ]
    assert [event.content for event in events if isinstance(event, AgentTextDelta)] == [
        "Information."
    ]


def invoke(tool, intent, **arguments):
    return {
        "id": tool,
        "name": tool,
        "arguments": json.dumps({"_intent": intent, **arguments}),
    }


@pytest.mark.parametrize("goal", ["CHECKOUT_NOW", "EXPLAIN_LESSON", "SUMMARIZE_REPORT"])
def test_domain_independent_goal_cannot_finish_at_preparation(goal):
    prepare = ExistingTool(
        "prepare", metadata={"continuation_arguments": {"finish_turn": False}}
    )
    target = ExistingTool("finish", metadata={"intent_contract": {"name": goal}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("prepare", goal, finish_turn=True)]},
            {"tool_calls": [invoke("finish", goal)]},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[prepare, target], intent_normalization=True
    ).run("request")
    assert engine.call_count == 2
    assert prepare.calls == [{"finish_turn": False}]
    assert target.calls == [{}]
    assert result.metadata["normalized_intent"] == goal
    assert result.metadata["intent_satisfied"] is True
    assert result.metadata["execution_path"] == ["prepare", "finish"]
    assert "customer_message" not in result.tool_results[0].metadata


def test_premature_plain_answer_is_not_reported_as_goal_completion():
    prepare = ExistingTool("prepare")
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("prepare", "FINALIZE")]},
            {"content": "Done after preparing."},
            {"tool_calls": [invoke("finish", "FINALIZE")]},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[prepare, finish], intent_normalization=True
    ).run("request")
    assert finish.calls == [{}]
    assert result.content == "verified result"
    assert result.metadata["intent_satisfied"]


def test_goal_cannot_be_downgraded_after_first_dispatch():
    prepare = ExistingTool("prepare")
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("prepare", "FINALIZE")]},
            {"tool_calls": [invoke("prepare", "NONE")]},
            {
                "content": json.dumps(
                    {"state": "NEEDS_INPUT", "message": "Please clarify."}
                )
            },
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[prepare, finish], intent_normalization=True
    ).run("request")
    assert len(prepare.calls) == 1 and finish.calls == []
    assert result.metadata["intent_satisfied"] is False
    assert result.metadata["terminal_reason"] == "NEEDS_INPUT"


def test_unknown_intent_and_target_batch_dispatch_nothing():
    prepare = ExistingTool("prepare")
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    for calls in [
        [invoke("finish", "UNKNOWN")],
        [invoke("prepare", "FINALIZE"), invoke("finish", "FINALIZE")],
    ]:
        engine = FakeEngine([{"tool_calls": calls}, {"content": "No action."}])
        result = OrchestratorAgent(
            engine,
            "fake",
            tools=[prepare, finish],
            max_turns=2,
            intent_normalization=True,
        ).run("request")
        assert prepare.calls == [] and finish.calls == []
        assert not result.tool_results[0].success


def test_failed_target_is_not_automatically_retried():
    finish = ExistingTool(
        "finish", metadata={"intent_contract": {"name": "FINALIZE"}}, fail=True
    )
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("finish", "FINALIZE")]},
            {"tool_calls": [invoke("finish", "FINALIZE")]},
            {
                "content": json.dumps(
                    {"state": "FAILED", "message": "Outcome unavailable."}
                )
            },
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[finish], intent_normalization=True
    ).run("request")
    assert len(finish.calls) == 1
    assert result.metadata["intent_satisfied"] is False


def test_schema_adds_no_tool_names_and_removes_marker_before_real_dispatch():
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine([{"tool_calls": [invoke("finish", "FINALIZE")]}])
    agent = OrchestratorAgent(engine, "fake", tools=[finish], intent_normalization=True)
    result = agent.run("request")
    assert [t.spec.name for t in agent._tools] == ["finish"]
    assert finish.calls == [{}]
    assert result.metadata["execution_path"] == ["finish"]


def test_exhausted_pending_goal_discards_an_unverified_success_message():
    prepare = ExistingTool("prepare")
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = FakeEngine(
        [
            {"tool_calls": [invoke("prepare", "FINALIZE")]},
            {"content": "Payment completed."},
        ]
    )
    result = OrchestratorAgent(
        engine, "fake", tools=[prepare, finish], max_turns=2, intent_normalization=True
    ).run("request")
    assert "Payment completed" not in result.content
    assert result.metadata["intent_satisfied"] is False


@pytest.mark.asyncio
async def test_semantic_stream_does_not_emit_preparation_acknowledgement():
    from openjarvis.agents._stubs import AgentRunCompleted, AgentTextDelta
    from openjarvis.engine._stubs import StreamChunk
    from tests.agents.test_orchestrator import StreamingEngine

    prepare = ExistingTool(
        "prepare", metadata={"continuation_arguments": {"finish_turn": False}}
    )
    finish = ExistingTool("finish", metadata={"intent_contract": {"name": "FINALIZE"}})
    engine = StreamingEngine(
        [
            [
                StreamChunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "p",
                            "function": {
                                "name": "prepare",
                                "arguments": json.dumps(
                                    {"_intent": "FINALIZE", "finish_turn": True}
                                ),
                            },
                        }
                    ]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ],
            [
                StreamChunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "f",
                            "function": {
                                "name": "finish",
                                "arguments": json.dumps({"_intent": "FINALIZE"}),
                            },
                        }
                    ]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ],
        ]
    )
    events = [
        event
        async for event in OrchestratorAgent(
            engine, "fake", tools=[prepare, finish], intent_normalization=True
        ).run_stream("request")
    ]
    assert prepare.calls == [{"finish_turn": False}] and finish.calls == [{}]
    assert isinstance(events[-1], AgentRunCompleted)
    assert events[-1].result.metadata["execution_path"] == ["prepare", "finish"]
    assert [event.content for event in events if isinstance(event, AgentTextDelta)] == [
        "verified result"
    ]
