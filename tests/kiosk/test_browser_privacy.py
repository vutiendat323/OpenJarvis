from unittest.mock import Mock

from openjarvis.kiosk.browser_privacy import browser_log_arguments, redact_browser_text
from openjarvis.tools._stubs import ToolSpec
from openjarvis.tools.mcp_adapter import MCPToolAdapter


def test_browser_observations_redact_credentials_and_card_fields():
    raw = (
        "https://site.test/?token=secret-token password=hunter2 "
        "Authorization: Bearer abc.def\n"
        '- textbox "Card number": 4111 1111 1111 1111'
    )
    result = redact_browser_text(raw)
    for secret in ("secret-token", "hunter2", "abc.def", "4111"):
        assert secret not in result
    assert "https://site.test/" in result


def test_typed_value_reaches_browser_but_not_tool_observations():
    client = Mock()
    client.call_tool.return_value = {
        "content": [{"text": "await locator.fill('private-value')"}]
    }
    adapter = MCPToolAdapter(
        client, ToolSpec(name="browser_type", description="type", parameters={})
    )
    adapter.bind_shared_browser(Mock())
    params = {"ref": "e5", "text": "private-value"}
    result = adapter.execute(**params)
    client.call_tool.assert_called_once_with("browser_type", params)
    assert "private-value" not in result.content
    assert browser_log_arguments("browser_type", params) == {
        "ref": "e5",
        "text": "[REDACTED]",
    }


def test_agent_reads_cached_page_without_a_browser_tool_roundtrip():
    bridge = Mock()
    bridge.redact.side_effect = redact_browser_text
    bridge.state.return_value = {
        "url": "https://site.test/?token=secret",
        "title": "Menu",
    }
    client = Mock()
    adapter = MCPToolAdapter(
        client, ToolSpec(name="browser_snapshot", description="snapshot", parameters={})
    )
    adapter.bind_shared_browser(bridge)
    context = adapter.agent_context()
    assert context["shared_browser"]["title"] == "Menu"
    assert "secret" not in context["shared_browser"]["url"]
    client.call_tool.assert_not_called()


def test_executor_event_and_persisted_metadata_do_not_contain_typed_values():
    import json

    from openjarvis.core.events import EventBus, EventType
    from openjarvis.core.types import ToolCall
    from openjarvis.tools._stubs import ToolExecutor

    client = Mock()
    client.call_tool.return_value = {"content": [{"text": "typed private-value"}]}
    adapter = MCPToolAdapter(
        client, ToolSpec(name="browser_type", description="type", parameters={})
    )
    adapter.bind_shared_browser(Mock())
    bus = EventBus()
    events = []
    bus.subscribe(EventType.TOOL_CALL_START, events.append)
    bus.subscribe(EventType.TOOL_CALL_END, events.append)
    result = ToolExecutor([adapter], bus).execute(
        ToolCall(
            id="one",
            name="browser_type",
            arguments=json.dumps(
                {
                    "ref": "e1",
                    "text": "private-value",
                }
            ),
        )
    )
    assert result.success
    assert "private-value" not in json.dumps(result.metadata)
    assert len(events) == 2
    assert "private-value" not in str(events)


def test_redaction_masks_longest_typed_secret_before_its_prefixes():
    from openjarvis.kiosk.browser_bridge import BrowserBridge
    from openjarvis.kiosk.shared_browser import BrowserEndpoint

    bridge = BrowserBridge(
        BrowserEndpoint("http://127.0.0.1:1", "ws://127.0.0.1:1", "one")
    )
    bridge._sensitive_values = ["tes", "test", "test-secret-only"]
    assert bridge.redact("test-secret-only") == "[REDACTED]"
