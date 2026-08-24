"""Display tools publish structured data. They never emit markup."""

from __future__ import annotations

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult
from openjarvis.kiosk.presentation import PresentationSessionManager
from openjarvis.tools.display import DisplayCartTool, DisplayClearTool, DisplayMenuTool


class _Recorder:
    def __init__(self, bus):
        self.events = []
        bus.subscribe(EventType.DISPLAY_UPDATE, self.events.append)


class _FakePresentationManager:
    def __init__(self) -> None:
        self.payloads = []

    def publish(self, payload):
        self.payloads.append(payload)
        return ToolResult(tool_name="presentation", content="presentation_published")


def _wired(cls):
    bus = EventBus()
    recorder = _Recorder(bus)
    tool = cls()
    tool._bus = bus
    return tool, recorder


def test_display_tools_are_neither_mutations_nor_observations():
    """They draw; they do not touch merchant state, so the doctrine test
    must not classify them."""
    for cls in (DisplayMenuTool, DisplayCartTool, DisplayClearTool):
        metadata = cls().spec.metadata
        assert metadata == {"displays": True}
        assert cls().spec.category == "display"


def test_display_menu_publishes_the_items_it_was_given():
    tool, recorder = _wired(DisplayMenuTool)
    result = tool.execute(
        items=[
            {"id": "latte", "name": "Latte", "price": 45000, "available": True},
        ]
    )

    assert result.success
    assert len(recorder.events) == 1
    data = recorder.events[0].data
    assert data["view"] == "menu"
    assert data["items"][0]["name"] == "Latte"


def test_display_menu_keeps_only_the_fields_the_page_renders():
    """Anything else the model invents must not reach the page."""
    tool, recorder = _wired(DisplayMenuTool)
    tool.execute(
        items=[
            {
                "id": "latte",
                "name": "Latte",
                "price": 45000,
                "available": True,
                "html": "<script>alert(1)</script>",
                "onclick": "steal()",
            }
        ]
    )

    item = recorder.events[0].data["items"][0]
    assert set(item) <= {"id", "name", "price", "available", "image_url", "note"}


def test_display_cart_publishes_lines_and_total():
    tool, recorder = _wired(DisplayCartTool)
    tool.execute(
        lines=[{"name": "Latte", "quantity": 2, "line_total": 102000}],
        total=102000,
    )

    data = recorder.events[0].data
    assert data["view"] == "cart"
    assert data["total"] == 102000
    assert data["lines"][0]["quantity"] == 2


def test_display_cart_keeps_size_and_note_but_drops_invented_fields():
    """The screen is what a person at the shop reads to make the drink --
    size and note must survive, and a model-invented key must not."""
    tool, recorder = _wired(DisplayCartTool)
    tool.execute(
        lines=[
            {
                "name": "Latte",
                "size": "Lớn",
                "note": "ít đường",
                "quantity": 2,
                "line_total": 122000,
                "html": "<b>x</b>",
            }
        ],
        total=122000,
    )

    line = recorder.events[0].data["lines"][0]
    assert line["size"] == "Lớn"
    assert line["note"] == "ít đường"
    assert set(line) <= {"name", "size", "note", "quantity", "line_total"}
    assert "html" not in line


def test_display_clear_publishes_an_empty_view():
    tool, recorder = _wired(DisplayClearTool)
    tool.execute()
    assert recorder.events[0].data == {"view": "none"}


def test_a_display_tool_without_a_bus_fails_rather_than_silently_doing_nothing():
    result = DisplayMenuTool().execute(items=[])
    assert result.success is False
    assert "display_unavailable" in result.content


def test_display_menu_publishes_through_the_presentation_manager():
    presentation = _FakePresentationManager()
    tool = DisplayMenuTool()
    tool._presentation = presentation

    result = tool.execute(items=[{"id": "latte", "name": "Latte"}])

    assert result.success is True
    assert presentation.payloads == [
        {"view": "menu", "items": [{"id": "latte", "name": "Latte"}]}
    ]


def test_display_menu_with_a_manager_fails_before_a_session_is_active():
    client = type("PlaywrightClient", (), {"_server_name": "playwright"})()
    tool = DisplayMenuTool()
    tool._presentation = PresentationSessionManager(EventBus(), client)

    result = tool.execute(items=[])

    assert result.success is False
    assert "presentation_unavailable" in result.content


def test_display_update_is_forwarded_to_websocket_clients():
    from openjarvis.server.ws_bridge import _AGENT_EVENTS

    assert EventType.DISPLAY_UPDATE in _AGENT_EVENTS
