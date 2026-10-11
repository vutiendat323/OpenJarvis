"""An engine can say how to turn private reasoning off for one request.

Live 2026-10-10: at ``low`` a DeepSeek round spent its whole token budget on
hidden reasoning (``finish=length``, no text, no tool call) and the customer
waited ~30 s. The orchestrator retries such a round with reasoning off, using
what the engine reports here.
"""

from __future__ import annotations

import pytest

from openjarvis.engine._stubs import InferenceEngine
from openjarvis.engine.cloud import _deepseek_thinking_options
from tests.engine.test_cloud_stream_full import _make_cloud_engine

DISABLED = {"extra_body": {"thinking": {"type": "disabled"}}}


def test_base_engine_reports_no_override():
    class Plain(InferenceEngine):
        engine_id = "plain"

        def generate(self, *a, **k):  # pragma: no cover
            raise NotImplementedError

        async def stream(self, *a, **k):  # pragma: no cover
            yield ""

        def list_models(self):  # pragma: no cover
            return []

        def health(self):  # pragma: no cover
            return True

    assert Plain().reasoning_off_options("anything") is None


def test_cloud_engine_reports_it_for_deepseek_only():
    engine = _make_cloud_engine()

    assert engine.reasoning_off_options("deepseek-flash") == DISABLED
    assert engine.reasoning_off_options("gpt-6-luna") is None
    assert engine.reasoning_off_options("claude-sonnet") is None


def test_the_returned_options_are_a_fresh_copy():
    engine = _make_cloud_engine()

    first = engine.reasoning_off_options("deepseek-flash")
    first["extra_body"]["thinking"]["type"] = "enabled"

    assert engine.reasoning_off_options("deepseek-flash") == DISABLED


def test_disabled_thinking_in_the_request_suppresses_the_effort_setting(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("OPENJARVIS_DEEPSEEK_THINKING", "low")
    request = {"extra_body": {"thinking": {"type": "disabled"}}}

    _deepseek_thinking_options(request)

    assert "reasoning_effort" not in request
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize(
    "wrapper",
    ["multi", "guardrails", "instrumented"],
)
def test_wrappers_delegate(wrapper):
    inner = _make_cloud_engine()
    if wrapper == "multi":
        from openjarvis.engine.multi import MultiEngine

        engine = MultiEngine.__new__(MultiEngine)
        engine._engine_for = lambda model: inner  # type: ignore[method-assign]
    elif wrapper == "guardrails":
        from openjarvis.security.guardrails import GuardrailsEngine

        engine = GuardrailsEngine.__new__(GuardrailsEngine)
        engine._engine = inner
    else:
        from openjarvis.telemetry.instrumented_engine import InstrumentedEngine

        engine = InstrumentedEngine.__new__(InstrumentedEngine)
        engine._inner = inner

    assert engine.reasoning_off_options("deepseek-flash") == DISABLED


@pytest.mark.parametrize("wrapper", ["multi", "guardrails", "instrumented"])
def test_wrappers_tolerate_an_engine_without_the_method(wrapper):
    class Duck:
        pass

    inner = Duck()
    if wrapper == "multi":
        from openjarvis.engine.multi import MultiEngine

        engine = MultiEngine.__new__(MultiEngine)
        engine._engine_for = lambda model: inner  # type: ignore[method-assign]
    elif wrapper == "guardrails":
        from openjarvis.security.guardrails import GuardrailsEngine

        engine = GuardrailsEngine.__new__(GuardrailsEngine)
        engine._engine = inner
    else:
        from openjarvis.telemetry.instrumented_engine import InstrumentedEngine

        engine = InstrumentedEngine.__new__(InstrumentedEngine)
        engine._inner = inner

    assert engine.reasoning_off_options("anything") is None
