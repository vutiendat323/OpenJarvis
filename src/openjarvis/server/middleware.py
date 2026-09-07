"""Security middleware -- HTTP security headers and request guards."""

from __future__ import annotations

from typing import Any

__all__ = [
    "SECURITY_HEADERS",
    "create_conversation_scope_middleware",
    "create_security_middleware",
]


def create_conversation_scope_middleware(max_conversations: int = 512) -> Any:
    """Give every text conversation one stable, server-issued working set.

    Pure ASGI rather than ``BaseHTTPMiddleware``: the streaming chat path
    returns a ``StreamingResponse`` and produces its body -- running the agent
    and every tool call -- after the endpoint coroutine returned. Only a
    middleware that awaits the whole ASGI cycle keeps the scope open that long.
    ``BaseHTTPMiddleware`` runs the app in a separate task and pipes the body
    through a queue, which makes ``ContextVar`` visibility depend on
    task-inheritance details rather than on anything written here.

    The id is issued here and echoed back in ``X-OpenJarvis-Conversation`` so a
    later turn can rejoin the same working set. Only an id this process actually
    issued is honoured; anything else -- caller-invented text, a well-formed id
    from before a restart -- earns a fresh scope, because honouring caller text
    would let one client read another client's tool evidence. The registry is
    bounded and least-recently-used, so a terminal that runs for weeks does not
    accumulate ids forever.

    A route that needs a longer-lived id -- the voice pipeline, which outlives
    its offer request -- opens its own scope nested inside this one.
    """
    import threading
    import uuid
    from collections import OrderedDict

    from openjarvis.core.conversation import conversation_scope

    header_name = b"x-openjarvis-conversation"

    class ConversationScopeMiddleware:
        def __init__(self, app: Any) -> None:
            self.app = app
            self._issued: OrderedDict[str, None] = OrderedDict()
            self._lock = threading.Lock()

        def _resolve(self, supplied: str) -> str:
            """Return the supplied id if we issued it, otherwise a fresh one."""
            with self._lock:
                if supplied in self._issued:
                    self._issued.move_to_end(supplied)
                    return supplied
                issued = uuid.uuid4().hex
                self._issued[issued] = None
                while len(self._issued) > max_conversations:
                    self._issued.popitem(last=False)
                return issued

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return

            supplied = ""
            for raw_name, raw_value in scope.get("headers") or ():
                if raw_name.lower() == header_name:
                    supplied = raw_value.decode("latin-1")
                    break
            conversation_id = self._resolve(supplied)

            async def send_with_conversation(message: Any) -> None:
                if message.get("type") == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.append((header_name, conversation_id.encode("ascii")))
                    message = {**message, "headers": headers}
                await send(message)

            with conversation_scope(conversation_id):
                await self.app(scope, receive, send_with_conversation)

    return ConversationScopeMiddleware


def create_security_middleware() -> Any:
    """Create a FastAPI middleware that adds security headers.

    Returns a middleware class/callable, or None if FastAPI is not available.

    Headers added:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Strict-Transport-Security: max-age=31536000; includeSubDomains
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: camera=(), microphone=(), geolocation=()

    OPTIONS requests are passed through without headers so that
    CORS preflight is not blocked.
    """
    try:
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.requests import Request
        from starlette.responses import Response
    except ImportError:
        return None

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Any) -> Response:
            # Let CORS preflight requests pass through without
            # security headers that would conflict with CORS.
            if request.method == "OPTIONS":
                return await call_next(request)

            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["X-XSS-Protection"] = "1; mode=block"
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = (
                "camera=(), microphone=(), geolocation=()"
            )
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' 'unsafe-inline' 'unsafe-eval'"
            )
            return response

    return SecurityHeadersMiddleware


# Also export the header values as constants for testing
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": "default-src 'self' 'unsafe-inline' 'unsafe-eval'",
}
