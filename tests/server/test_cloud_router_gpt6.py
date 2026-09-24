from __future__ import annotations

from typing import Any

import pytest

from openjarvis.core.types import Message, Role
from openjarvis.server import cloud_router


@pytest.mark.asyncio
async def test_direct_gpt_6_stream_omits_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: dict[str, Any] = {}

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"OK"}}]}'
            yield "data: [DONE]"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object) -> None:
            pass

        def stream(self, method: str, url: str, **kwargs: Any):
            sent.update(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(cloud_router, "_load_keys", lambda: {"OPENAI_API_KEY": "test"})
    monkeypatch.setattr(cloud_router.httpx, "AsyncClient", lambda **_: FakeClient())

    tokens = [
        token
        async for token in cloud_router.stream_cloud(
            "gpt-6-luna", [Message(role=Role.USER, content="Hi")]
        )
    ]

    assert tokens == ["OK"]
    assert sent["model"] == "gpt-6-luna"
    assert sent["reasoning_effort"] == "none"
    assert sent["max_completion_tokens"] == 1024
    assert "temperature" not in sent
    assert "max_tokens" not in sent
