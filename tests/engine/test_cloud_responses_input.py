"""Responses input keeps `instructions` stable so the prompt cache can match it.

OpenAI caches the longest unchanged prefix of a request. Text that changes from
one round to the next must therefore not sit in `instructions`, which leads the
prefix: it would make every round a cache miss.
"""

from __future__ import annotations

import pytest

from openjarvis.core.types import Message, Role, ToolCall
from openjarvis.engine.cloud import CloudEngine

_build = CloudEngine._openai_responses_input


def _system(text: str, **metadata: object) -> Message:
    return Message(role=Role.SYSTEM, content=text, metadata=dict(metadata))


def test_leading_system_messages_become_the_instructions():
    instructions, items = _build(
        [_system("Kiosk rules."), _system("Voice rules."), _user("Hi")]
    )

    assert instructions == "Kiosk rules.\n\nVoice rules."
    assert items == [{"role": "user", "content": "Hi"}]


def test_volatile_system_message_stays_out_of_the_instructions():
    instructions, items = _build(
        [
            _system("Kiosk rules."),
            _system("Evidence: menu", volatile=True),
            _system("Voice rules."),
            _user("Hi"),
        ]
    )

    assert instructions == "Kiosk rules.\n\nVoice rules."
    assert items == [
        {"role": "developer", "content": "Evidence: menu"},
        {"role": "user", "content": "Hi"},
    ]


def test_system_message_inside_the_conversation_keeps_its_place():
    calls = [ToolCall(id="c1", name="lookup", arguments="{}")]
    messages = [
        _system("Kiosk rules."),
        _user("Open the menu"),
        Message(role=Role.ASSISTANT, content="", tool_calls=calls),
        Message(role=Role.TOOL, content="ok", tool_call_id="c1", name="lookup"),
        _system("Correction: answer in JSON."),
    ]

    instructions, items = _build(messages)

    assert instructions == "Kiosk rules."
    assert [item.get("role") or item["type"] for item in items] == [
        "user",
        "function_call",
        "function_call_output",
        "developer",
    ]
    assert items[-1]["content"] == "Correction: answer in JSON."


def test_changing_text_does_not_change_the_instructions():
    def request(evidence: str, runtime: str, correction: str):
        return _build(
            [
                _system("Kiosk rules."),
                _system(evidence, volatile=True),
                _system("Voice rules."),
                _system(runtime, volatile=True),
                _user("Hi"),
                _system(correction),
            ]
        )

    first, _ = request("evidence 1", "turn_nonce=a", "")
    second, _ = request("evidence 2", "turn_nonce=b", "fix it")

    assert first == second == "Kiosk rules.\n\nVoice rules."


def _user(text: str) -> Message:
    return Message(role=Role.USER, content=text)


async def _send(model: str, messages: list[Message]) -> dict:
    from types import SimpleNamespace
    from unittest import mock

    async def events():
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[], usage=None, status="completed"),
        )

    client = mock.MagicMock()
    client.responses.create = mock.AsyncMock(return_value=events())
    engine = CloudEngine()
    if model.startswith("openrouter/"):
        engine._openrouter_async_client = client
    else:
        engine._openai_async_client = client
    async for _ in engine.stream_full(messages, model=model):
        pass
    return client.responses.create.call_args.kwargs


@pytest.mark.asyncio
async def test_stable_prompt_ends_in_an_explicit_cache_breakpoint():
    sent = await _send(
        "gpt-6-luna",
        [_system("Kiosk rules."), _system("Evidence", volatile=True), _user("Hi")],
    )

    assert "instructions" not in sent
    assert sent["input"][0] == {
        "role": "developer",
        "content": [
            {
                "type": "input_text",
                "text": "Kiosk rules.",
                "prompt_cache_breakpoint": {"mode": "explicit"},
            }
        ],
    }
    assert sent["input"][1:] == [
        {"role": "developer", "content": "Evidence"},
        {"role": "user", "content": "Hi"},
    ]


@pytest.mark.asyncio
async def test_openrouter_keeps_plain_instructions():
    sent = await _send("openrouter/openai/gpt-6-luna", [_system("Rules."), _user("Hi")])

    assert sent["instructions"] == "Rules."
    assert sent["input"] == [{"role": "user", "content": "Hi"}]
