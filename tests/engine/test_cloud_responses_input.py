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
    # After the conversation: text that changes each question must not sit
    # between the fixed prompt and the history, or no run reuses the history.
    assert items == [
        {"role": "user", "content": "Hi"},
        {"role": "developer", "content": "Evidence: menu"},
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
        {"role": "user", "content": "Hi"},
        {"role": "developer", "content": "Evidence"},
    ]


@pytest.mark.asyncio
async def test_openrouter_keeps_plain_instructions():
    sent = await _send("openrouter/openai/gpt-6-luna", [_system("Rules."), _user("Hi")])

    assert sent["instructions"] == "Rules."
    assert sent["input"] == [{"role": "user", "content": "Hi"}]


def _prefix_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("responses_prefix")
    ]


@pytest.mark.asyncio
async def test_prefix_log_shows_which_part_of_the_cached_prefix_changed(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="openjarvis.engine.cloud")
    tools_a = [{"type": "function", "function": {"name": "menu"}}]
    tools_b = [{"type": "function", "function": {"name": "cart"}}]

    async def send(tools, rules):
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
        engine._openai_async_client = client
        messages = [_system(rules), _user("secret customer words")]
        async for _ in engine.stream_full(messages, model="gpt-6-luna", tools=tools):
            pass

    await send(tools_a, "Kiosk rules.")
    await send(tools_a, "Kiosk rules.")
    await send(tools_b, "Kiosk rules.")
    await send(tools_a, "Other rules.")

    first, same, other_tools, other_rules = (
        dict(part.split("=", 1) for part in line.split()[1:])
        for line in _prefix_lines(caplog)
    )
    assert first == same
    assert first["stable_chars"] == "12" and first["tools_count"] == "1"
    assert other_tools["tools"] != first["tools"]
    assert other_tools["stable"] == first["stable"]
    assert other_rules["stable"] != first["stable"]
    assert other_rules["tools"] == first["tools"]
    assert not any("secret customer" in line for line in _prefix_lines(caplog))


@pytest.mark.asyncio
async def test_prefix_log_lists_each_stable_part(caplog):
    import logging
    from types import SimpleNamespace
    from unittest import mock

    caplog.set_level(logging.WARNING, logger="openjarvis.engine.cloud")

    async def events():
        yield SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(output=[], usage=None, status="completed"),
        )

    client = mock.MagicMock()
    client.responses.create = mock.AsyncMock(return_value=events())
    engine = CloudEngine()
    engine._openai_async_client = client
    messages = [
        _system("abc"),
        _system("Evidence", volatile=True),
        _system("hello"),
        _user("Hi"),
    ]
    async for _ in engine.stream_full(messages, model="gpt-6-luna"):
        pass

    [line] = _prefix_lines(caplog)
    fields = dict(part.split("=", 1) for part in line.split()[1:])
    first, second = fields["stable_parts"].split(",")
    assert first.startswith("3:") and second.startswith("5:")


@pytest.mark.asyncio
async def test_final_answer_schema_is_sent_as_strict_text_format():
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
    engine._openai_async_client = client
    schema = {"type": "object", "properties": {}, "required": []}
    assert engine.supports_final_answer_schema("gpt-6-luna")
    assert not engine.supports_final_answer_schema("openrouter/openai/gpt-6-luna")
    async for _ in engine.stream_full(
        [_user("Hi")], model="gpt-6-luna", final_answer_schema=schema
    ):
        pass

    sent = client.responses.create.call_args.kwargs
    assert sent["text"] == {
        "format": {
            "type": "json_schema",
            "name": "final_answer",
            "strict": True,
            "schema": schema,
        }
    }
    assert "final_answer_schema" not in sent


def test_history_prefix_is_the_same_whatever_the_leading_volatile_text():
    def items(note: str):
        _, built = _build(
            [
                _system("Kiosk rules."),
                _system(note, volatile=True),
                _user("Mở menu"),
                Message(role=Role.ASSISTANT, content="Menu đây."),
                _user("Thêm bánh"),
            ]
        )
        return built

    first, second = items("FAQ giờ mở cửa"), items("FAQ wifi")
    assert first[:3] == second[:3]
    assert first[-1]["content"] == "FAQ giờ mở cửa"


@pytest.mark.asyncio
async def test_prefix_log_lists_each_input_item_without_its_text(caplog):
    """Comparing two runs' lists shows the first item where the history differs."""
    import logging

    caplog.set_level(logging.WARNING, logger="openjarvis.engine.cloud")
    calls = [ToolCall(id="c1", name="lookup", arguments="{}")]
    await _send(
        "gpt-6-luna",
        [
            _system("Rules."),
            _user("Mở menu"),
            Message(role=Role.ASSISTANT, content="", tool_calls=calls),
            Message(role=Role.TOOL, content="ok", tool_call_id="c1", name="lookup"),
        ],
    )

    [line] = _prefix_lines(caplog)
    fields = dict(part.split("=", 1) for part in line.split()[1:])
    kinds = [item.split(":")[0] for item in fields["items"].split(",")]
    assert kinds == ["user", "function_call", "function_call_output"]
    assert "Mở menu" not in line
