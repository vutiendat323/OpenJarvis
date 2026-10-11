"""DeepSeek's thinking budget is a latency control, set per deployment.

A live kiosk turn spent its whole 4096-token completion budget on hidden
reasoning (``finish=length``, no text, no tool call) and kept the customer
waiting 25-50 s. Raising ``max_tokens`` only lengthens that wait; the provider
exposes the effort and an off switch instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjarvis.core.types import Message, Role
from openjarvis.engine.cloud import CloudEngine
from tests.engine.test_cloud_stream_full import (
    _async_openai_client,
    _make_cloud_engine,
    _openai_chunk,
)

ENV = "OPENJARVIS_DEEPSEEK_THINKING"
MESSAGES = [Message(role=Role.USER, content="cho minh 2 ly latte")]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)


async def _stream_full_request(**extra: Any) -> dict[str, Any]:
    client = _async_openai_client([_openai_chunk(finish_reason="stop")])
    engine = _make_cloud_engine(deepseek_async_client=client)
    async for _ in engine._stream_full_openai(
        MESSAGES,
        model="deepseek-flash",
        temperature=0.2,
        max_tokens=4096,
        **extra,
    ):
        pass
    return client.chat.completions.create.await_args.kwargs


def _generate_request(**extra: Any) -> dict[str, Any]:
    recorded: dict[str, Any] = {}

    def create(**kwargs: Any) -> SimpleNamespace:
        recorded.update(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model="deepseek-flash",
        )

    engine = CloudEngine.__new__(CloudEngine)
    engine._deepseek_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    engine._generate_deepseek(
        MESSAGES, model="deepseek-flash", temperature=0.2, max_tokens=4096, **extra
    )
    return recorded


async def _stream_request() -> dict[str, Any]:
    client = _async_openai_client([_openai_chunk(content="ok")])
    engine = _make_cloud_engine(deepseek_async_client=client)
    async for _ in engine._stream_deepseek(
        MESSAGES, model="deepseek-flash", temperature=0.2, max_tokens=4096
    ):
        pass
    return client.chat.completions.create.await_args.kwargs


@pytest.mark.asyncio
async def test_unset_leaves_the_provider_default_untouched():
    request = await _stream_full_request()
    assert "reasoning_effort" not in request
    assert "extra_body" not in request


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["off", "disabled", "OFF"])
async def test_off_disables_thinking_in_the_streaming_agent_path(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    request = await _stream_full_request()
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in request
    assert request["max_tokens"] == 4096


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["low", "high", "max"])
async def test_effort_is_forwarded_in_the_streaming_agent_path(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    request = await _stream_full_request()
    assert request["reasoning_effort"] == value
    assert "extra_body" not in request


@pytest.mark.asyncio
async def test_caller_supplied_effort_wins_over_the_deployment_setting(monkeypatch):
    monkeypatch.setenv(ENV, "off")
    request = await _stream_full_request(reasoning_effort="max")
    assert request["reasoning_effort"] == "max"
    assert "extra_body" not in request


@pytest.mark.asyncio
async def test_unknown_value_is_refused_not_silently_ignored(monkeypatch):
    monkeypatch.setenv(ENV, "fast")
    with pytest.raises(ValueError, match=ENV):
        await _stream_full_request()


def test_the_blocking_agent_path_honors_the_setting(monkeypatch):
    monkeypatch.setenv(ENV, "off")
    request = _generate_request()
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_the_plain_text_stream_honors_the_setting(monkeypatch):
    monkeypatch.setenv(ENV, "low")
    request = await _stream_request()
    assert request["reasoning_effort"] == "low"


@pytest.mark.asyncio
async def test_other_providers_never_receive_the_deepseek_setting(monkeypatch):
    monkeypatch.setenv(ENV, "off")
    client = _async_openai_client([_openai_chunk(finish_reason="stop")])
    engine = _make_cloud_engine(openai_async_client=client)
    async for _ in engine._stream_full_openai(
        MESSAGES, model="gpt-4o", temperature=0.2, max_tokens=100
    ):
        pass
    request = client.chat.completions.create.await_args.kwargs
    assert "extra_body" not in request and "reasoning_effort" not in request
