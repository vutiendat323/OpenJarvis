import pytest

from openjarvis.core.types import Message, Role
from openjarvis.server import cloud_router


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected_model", "provider_model"),
    [
        ("openrouter/openai/gpt-5.6-luna", "openai/gpt-5.6-luna"),
        ("openai/gpt-5.6-luna", "openai/gpt-5.6-luna"),
        ("openrouter/auto", "openrouter/auto"),
    ],
)
async def test_openrouter_stream_sends_provider_model_once(
    monkeypatch: pytest.MonkeyPatch,
    selected_model: str,
    provider_model: str,
) -> None:
    sent_models: list[str] = []

    async def fake_stream(model: str, *args: object, **kwargs: object):
        sent_models.append(model)
        yield "ok"

    monkeypatch.setattr(
        cloud_router, "_load_keys", lambda: {"OPENROUTER_API_KEY": "test"}
    )
    monkeypatch.setattr(cloud_router, "_stream_openai", fake_stream)

    tokens = [
        token
        async for token in cloud_router.stream_cloud(
            selected_model,
            [Message(role=Role.USER, content="hello")],
        )
    ]

    assert tokens == ["ok"]
    assert sent_models == [provider_model]
