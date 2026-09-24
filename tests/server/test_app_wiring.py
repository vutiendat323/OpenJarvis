"""Voice and kiosk hang off the same system, not a second one."""

from __future__ import annotations

import asyncio
import inspect


def test_voice_router_is_mounted():
    import openjarvis.server.app as app_module

    source = inspect.getsource(app_module)
    assert "voice_router" in source
    assert "include_router(voice_router" in source


def test_kiosk_is_set_up():
    import openjarvis.server.app as app_module

    source = inspect.getsource(app_module)
    assert "_setup_kiosk" in source
    assert "kiosk_router" in source


def test_native_agent_runtime_wraps_the_serving_agent():
    """The realtime binding must come from the agent the system serves."""
    import openjarvis.server.app as app_module

    source = inspect.getsource(app_module)
    assert "NativeAgentRuntime(" in source
    assert "app.state.native_agent_runtime" in source


def test_server_runtime_records_to_the_app_shared_trace_store(tmp_path):
    """Server construction must inject, not recreate, the trace dependencies."""
    from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
    from openjarvis.core.config import JarvisConfig
    from openjarvis.core.events import EventBus, EventType
    from openjarvis.server.app import create_app

    class _LocalAgent(BaseAgent):
        agent_id = "local"

        def __init__(self) -> None:
            pass

        def run(self, input, context=None, **kwargs):
            return AgentResult(content="done")

    config = JarvisConfig()
    config.analytics.enabled = False
    config.traces.enabled = True
    config.traces.db_path = str(tmp_path / "traces.db")
    bus = EventBus(record_history=True)
    app = create_app(
        object(), "test-model", agent=_LocalAgent(), bus=bus, config=config
    )
    store = app.state.trace_store

    try:
        result = asyncio.run(
            app.state.native_agent_runtime.bind(model="test-model").run(
                "hello", AgentContext()
            )
        )

        assert result.content == "done"
        assert store.count() == 1
        assert [event.event_type for event in bus.history].count(
            EventType.TRACE_COMPLETE
        ) == 1
    finally:
        store.close()


def test_server_runtime_uses_the_borrowed_trace_store(tmp_path):
    """A composition root must not open a second trace database connection."""
    from openjarvis.agents._stubs import AgentContext, AgentResult, BaseAgent
    from openjarvis.core.config import JarvisConfig
    from openjarvis.core.events import EventBus
    from openjarvis.server.app import create_app
    from openjarvis.traces.store import TraceStore

    class _LocalAgent(BaseAgent):
        agent_id = "local"

        def __init__(self) -> None:
            pass

        def run(self, input, context=None, **kwargs):
            return AgentResult(content="done")

    borrowed_store = TraceStore(tmp_path / "borrowed.db")
    config = JarvisConfig()
    config.analytics.enabled = False
    config.traces.enabled = True
    config.traces.db_path = str(tmp_path / "unused.db")
    app = create_app(
        object(),
        "test-model",
        agent=_LocalAgent(),
        bus=EventBus(),
        config=config,
        trace_store=borrowed_store,
    )

    try:
        assert app.state.trace_store is borrowed_store
        result = asyncio.run(
            app.state.native_agent_runtime.bind(model="test-model").run(
                "hello", AgentContext()
            )
        )
        assert result.content == "done"
        assert borrowed_store.count() == 1
        for shutdown_handler in app.router.on_shutdown:
            asyncio.run(shutdown_handler())
        assert borrowed_store.count() == 1
    finally:
        borrowed_store.close()


def test_voice_session_service_is_exposed():
    import openjarvis.server.app as app_module

    assert "voice_session_service" in inspect.getsource(app_module)


def test_availability_router_is_mounted_even_when_the_gate_is_closed():
    """The UI must be able to ask *why* voice is off, so this one is ungated."""
    from openjarvis.server.app import create_app

    app = create_app(engine=None, model="m", bind_host="0.0.0.0")

    assert app.state.gemini_live_poc_enabled is False
    paths = {route.path for route in app.routes}
    assert "/api/voice/availability" in paths
    assert "/api/voice/webrtc/offer" not in paths


def test_gate_closes_when_the_pipecat_runtime_is_absent(monkeypatch):
    """An open Gemini gate still must not mount a router that failed to import."""
    import openjarvis.server.app as app_module

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(app_module, "voice_router", None)

    app = app_module.create_app(engine=None, model="m", bind_host="127.0.0.1")

    assert app.state.gemini_live_poc_enabled is False
    assert app.state.gemini_live_poc_unavailable_reason == "voice_runtime_missing"
