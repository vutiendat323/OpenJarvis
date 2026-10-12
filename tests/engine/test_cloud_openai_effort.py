"""The Luna reasoning effort is an operator's latency control.

Callers cannot lower it (a verified follow-up must keep full reasoning), but a
deployment can, through ``OPENJARVIS_OPENAI_REASONING_EFFORT``. Measured on the
live kiosk, gpt-6-luna took a median 13.9 s from the customer's last word to
the first spoken word, mostly hidden reasoning before the first sentence.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from openjarvis.core.types import Message, Role
from openjarvis.engine.cloud import CloudEngine

ENV = "OPENJARVIS_OPENAI_REASONING_EFFORT"
MESSAGES = [Message(role=Role.USER, content="Cho mình một cà phê sữa")]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


async def _stream_effort(model: str = "gpt-6-luna", **kwargs: str) -> dict:
    async def events():
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[], usage=None),
        )

    client = mock.MagicMock()
    client.responses.create = mock.AsyncMock(return_value=events())
    engine = CloudEngine()
    engine._openai_async_client = client
    _ = [c async for c in engine.stream_full(MESSAGES, model=model, **kwargs)]
    return client.responses.create.call_args.kwargs["reasoning"]


def _generate_effort(model: str = "gpt-6-luna", **kwargs: str) -> dict:
    client = mock.MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        output_text="OK", output=[], usage=None, model=model, status="completed"
    )
    engine = CloudEngine()
    engine._openai_client = client
    engine.generate(MESSAGES, model=model, **kwargs)
    return client.responses.create.call_args.kwargs["reasoning"]


@pytest.mark.asyncio
async def test_unset_keeps_high():
    assert await _stream_effort() == {"effort": "high"}
    assert _generate_effort() == {"effort": "high"}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["low", "medium", "high", "LOW"])
async def test_deployment_setting_applies_to_the_streaming_agent_path(
    monkeypatch, value
):
    monkeypatch.setenv(ENV, value)
    assert await _stream_effort() == {"effort": value.lower()}


@pytest.mark.asyncio
async def test_deployment_setting_applies_to_the_blocking_path(monkeypatch):
    monkeypatch.setenv(ENV, "medium")
    assert _generate_effort() == {"effort": "medium"}


@pytest.mark.asyncio
async def test_a_caller_still_cannot_lower_effort(monkeypatch):
    monkeypatch.setenv(ENV, "high")
    assert await _stream_effort(reasoning_effort="low") == {"effort": "high"}


@pytest.mark.asyncio
async def test_setting_covers_gpt_5_6_luna_too(monkeypatch):
    monkeypatch.setenv(ENV, "low")
    assert await _stream_effort(model="gpt-5.6-luna") == {"effort": "low"}


@pytest.mark.asyncio
async def test_unknown_value_is_refused_not_silently_ignored(monkeypatch):
    monkeypatch.setenv(ENV, "fast")
    with pytest.raises(ValueError, match=ENV):
        await _stream_effort()


@pytest.mark.asyncio
async def test_each_model_uses_its_own_effort_in_both_request_paths(
    monkeypatch, tmp_path
):
    path = tmp_path / "effort.json"
    path.write_text(json.dumps({"gpt-6-luna": "medium", "gpt-5.6-luna": "low"}))
    monkeypatch.setenv("OPENJARVIS_EFFORT_FILE", str(path))

    for model, expected in [("gpt-6-luna", "medium"), ("gpt-5.6-luna", "low")]:
        assert await _stream_effort(model) == {"effort": expected}
        assert _generate_effort(model) == {"effort": expected}


@pytest.mark.asyncio
@pytest.mark.parametrize("values, expected", [
    ({"gpt-5-mini": "medium", "gpt-6-luna": "low"}, "medium"),
    ({"gpt-6-luna": "low"}, None),
])
async def test_chat_completions_only_uses_the_explicit_model_preset(
    monkeypatch, tmp_path, values, expected
):
    path = tmp_path / "effort.json"
    path.write_text(json.dumps(values))
    monkeypatch.setenv("OPENJARVIS_EFFORT_FILE", str(path))
    monkeypatch.setenv(ENV, "high")
    client = mock.MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="OK", tool_calls=None),
            finish_reason="stop",
        )],
        usage=None, model="gpt-5-mini",
    )
    engine = CloudEngine()
    engine._openai_client = client

    engine.generate(MESSAGES, model="gpt-5-mini")

    request = client.chat.completions.create.call_args.kwargs
    assert request.get("reasoning_effort") == expected

    chunk = SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content="OK", tool_calls=None), finish_reason="stop",
    )])
    client.chat.completions.create.return_value = iter([chunk])
    _ = [c async for c in engine.stream(MESSAGES, model="gpt-5-mini")]
    request = client.chat.completions.create.call_args.kwargs
    assert request.get("reasoning_effort") == expected

    async def chunks():
        yield chunk

    async_client = mock.MagicMock()
    async_client.chat.completions.create = mock.AsyncMock(return_value=chunks())
    engine._openai_async_client = async_client
    _ = [c async for c in engine.stream_full(MESSAGES, model="gpt-5-mini")]
    request = async_client.chat.completions.create.call_args.kwargs
    assert request.get("reasoning_effort") == expected
