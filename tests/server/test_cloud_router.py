"""Regression tests for OpenRouter model ID normalization."""

from __future__ import annotations

import pytest

from openjarvis.core.types import Message
from openjarvis.server import cloud_router


def test_local_deepseek_model_is_not_routed_to_cloud():
    assert cloud_router.get_provider("deepseek-r1:7b") is None


@pytest.mark.asyncio
async def test_v41_flash_uses_the_direct_deepseek_endpoint(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    assert cloud_router.get_cloud_key_status()["DEEPSEEK_API_KEY"] is True
    assert cloud_router.get_provider("deepseek-flash") == "deepseek"
    captured = {}

    async def fake_stream_openai(model, messages, temperature, max_tokens, **kwargs):
        captured.update(model=model, **kwargs)
        yield "ok"

    monkeypatch.setattr(cloud_router, "_stream_openai", fake_stream_openai)
    tokens = [
        token
        async for token in cloud_router.stream_cloud(
            "deepseek-flash", [Message(role="user", content="hi")]
        )
    ]
    assert tokens == ["ok"]
    assert captured == {
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_name": "DEEPSEEK_API_KEY",
    }


def test_get_provider_detects_bare_openrouter_id():
    assert cloud_router.get_provider("anthropic/claude-haiku-4.5") == "openrouter"


def test_get_provider_detects_litellm_prefixed_openrouter_id():
    model = "openrouter/anthropic/claude-haiku-4.5"
    assert cloud_router.get_provider(model) == "openrouter"


@pytest.mark.parametrize(
    "requested_model,expected_forwarded_model",
    [
        ("anthropic/claude-haiku-4.5", "anthropic/claude-haiku-4.5"),
        ("openrouter/anthropic/claude-haiku-4.5", "anthropic/claude-haiku-4.5"),
        ("openrouter/auto", "openrouter/auto"),
    ],
)
@pytest.mark.asyncio
async def test_stream_cloud_normalizes_openrouter_model_before_forwarding(
    monkeypatch, requested_model, expected_forwarded_model
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    captured: dict[str, str] = {}

    async def fake_stream_openai(model, messages, temperature, max_tokens, **kwargs):
        captured["model"] = model
        yield "ok"

    monkeypatch.setattr(cloud_router, "_stream_openai", fake_stream_openai)

    tokens = [
        token
        async for token in cloud_router.stream_cloud(
            requested_model, [Message(role="user", content="hi")]
        )
    ]

    assert tokens == ["ok"]
    assert captured["model"] == expected_forwarded_model
