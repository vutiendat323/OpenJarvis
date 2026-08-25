"""Each conversation owns its working set -- including across a stream."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from openjarvis.core.conversation import conversation_scope, current_conversation_id
from openjarvis.server.middleware import create_conversation_scope_middleware


def test_scope_sets_and_restores():
    assert current_conversation_id() == ""

    with conversation_scope("thread-1"):
        assert current_conversation_id() == "thread-1"

    assert current_conversation_id() == ""


def test_nested_scopes_restore_the_outer_one():
    with conversation_scope("outer"):
        with conversation_scope("inner"):
            assert current_conversation_id() == "inner"
        assert current_conversation_id() == "outer"


def _app_with_middleware() -> tuple:
    seen: list = []
    app = FastAPI()
    app.add_middleware(create_conversation_scope_middleware())

    @app.get("/plain")
    def plain():
        seen.append(current_conversation_id())
        return {"ok": True}

    @app.get("/streamed")
    def streamed():
        def body():
            # Runs after the handler has returned -- the case a
            # `with` block around the handler body would miss entirely.
            seen.append(current_conversation_id())
            yield b"chunk"

        return StreamingResponse(body())

    return app, seen


def test_a_plain_request_gets_a_conversation():
    app, seen = _app_with_middleware()

    TestClient(app).get("/plain")

    assert len(seen) == 1
    assert seen[0] != ""


def test_a_streamed_response_still_has_its_conversation():
    """The chat path streams, so the agent runs after the endpoint returned.

    Pins the property rather than trusting how FastAPI happens to copy context
    around an endpoint on any given version.
    """
    app, seen = _app_with_middleware()

    TestClient(app).get("/streamed")

    assert len(seen) == 1
    assert seen[0] != ""


def test_two_requests_do_not_share_a_conversation():
    app, seen = _app_with_middleware()
    client = TestClient(app)

    client.get("/streamed")
    client.get("/streamed")

    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_create_app_puts_conversation_scope_outside_auth():
    """Authentication rejections must still enter the HTTP conversation scope."""
    from openjarvis.server.app import create_app

    app = create_app(engine=None, model="test-model", api_key="test-key")

    assert app.user_middleware[0].cls.__name__ == "ConversationScopeMiddleware"
    assert app.user_middleware[1].cls.__name__ == "AuthMiddleware"


def test_voice_route_scopes_by_chat_thread():
    """Voice needs a stable id: its pipeline outlives the offer request."""
    import inspect

    from openjarvis.server.voice import routes

    source = inspect.getsource(routes.voice_webrtc_offer)
    assert "conversation_scope(generation)" in source
