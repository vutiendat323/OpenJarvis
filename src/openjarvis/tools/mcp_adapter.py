"""MCP tool adapter — wraps external MCP server tools as native BaseTool instances."""

from __future__ import annotations

from typing import Any, List

from openjarvis.core.types import ToolResult
from openjarvis.mcp.client import MCPClient
from openjarvis.tools._stubs import BaseTool, ToolSpec


class MCPToolAdapter(BaseTool):
    """Wraps a single MCP-hosted tool as a native BaseTool.

    This adapter enables tools discovered from external MCP servers to
    be used seamlessly within OpenJarvis agents via the ``ToolExecutor``.

    Parameters
    ----------
    client:
        The ``MCPClient`` connected to the external MCP server.
    tool_spec:
        The ``ToolSpec`` describing this tool (from ``MCPClient.list_tools()``).
    """

    tool_id = "mcp_adapter"

    def __init__(self, client: MCPClient, tool_spec: ToolSpec) -> None:
        self._client = client
        self._spec = tool_spec
        self._shared_browser = None

    def bind_shared_browser(self, bridge: Any) -> None:
        self._shared_browser = bridge

    def agent_context(self) -> dict[str, Any]:
        if self._shared_browser is None or self._spec.name != "browser_snapshot":
            return {}
        from openjarvis.kiosk.browser_privacy import redact_browser_value

        return {
            "shared_browser": redact_browser_value(
                self._shared_browser.state(), self._shared_browser.redact
            )
        }

    def observation_arguments(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._shared_browser is None:
            return params
        from openjarvis.kiosk.browser_privacy import browser_log_arguments

        return browser_log_arguments(self._spec.name, params)

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, **params: Any) -> ToolResult:
        """Execute the remote MCP tool and return a ToolResult."""
        try:
            if self._shared_browser is not None:
                self._shared_browser.activity(self._spec.name)
            result = self._client.call_tool(self._spec.name, params)
            content_parts = result.get("content", [])
            text = "\n".join(
                p.get("text", "") for p in content_parts if isinstance(p, dict)
            )
            if self._shared_browser is not None:
                from openjarvis.kiosk.browser_privacy import redact_browser_text

                # MCP echoes typed values in its generated Playwright snippet.
                if self._spec.name == "browser_type" and params.get("text"):
                    text = text.replace(str(params["text"]), "[REDACTED]")
                for field in params.get("fields", []):
                    value = field.get("value") if isinstance(field, dict) else None
                    if value:
                        text = text.replace(str(value), "[REDACTED]")
                text = redact_browser_text(text)
                redact = getattr(self._shared_browser, "redact", None)
                if callable(redact):
                    filtered = redact(text)
                    if isinstance(filtered, str):
                        text = filtered
            return ToolResult(
                tool_name=self._spec.name,
                content=text,
                success=not result.get("isError", False),
            )
        except Exception as exc:
            message = str(exc)
            if self._shared_browser is not None:
                message = "Shared browser tool failed"
            return ToolResult(
                tool_name=self._spec.name,
                content=f"MCP tool error: {message}",
                success=False,
            )
        finally:
            if self._shared_browser is not None:
                self._shared_browser.activity(None)


class MCPToolProvider:
    """Discovers tools from an MCP server and returns BaseTool adapters.

    Parameters
    ----------
    client:
        The ``MCPClient`` connected to the MCP server.
    """

    def __init__(self, client: MCPClient) -> None:
        self._client = client

    def discover(self) -> List[BaseTool]:
        """Discover available tools and return them as BaseTool adapters."""
        specs = self._client.list_tools()
        return [MCPToolAdapter(self._client, s) for s in specs]


__all__ = ["MCPToolAdapter", "MCPToolProvider"]
