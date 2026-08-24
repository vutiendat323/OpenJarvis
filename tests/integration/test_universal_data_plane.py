"""The whole Data Plane path, exercised through the real preset and executor.

Every tool call here goes through ``ToolExecutor.execute`` rather than a tool
object's ``execute()``. The executor owns the Agent identity, the capability
policy and the approval context, so calling it directly is what pins the
authority path these tests are about.

Nothing in here reaches the network: one fixture provider stands in for
trendcoffee.net across discovery, execution and the (still unimplemented)
browser observation port, and ``OPENJARVIS_HOME`` is redirected at ``tmp_path``
so no store touches the operator's real data directory.
"""

from __future__ import annotations

import json
import sqlite3
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import httpx
import pytest

from openjarvis.core.config import load_config
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.data_plane.types import (
    NormalizedBatch,
    ResourceRecord,
    TransportKind,
)
from openjarvis.system.builder import SystemBuilder

pytestmark = pytest.mark.integration

PRESET = Path("configs/openjarvis/examples/ordering-kiosk.toml")
TREND_FIXTURES = (
    Path(__file__).parent.parent / "data_plane" / "fixtures" / "trendcoffee"
)

HOME_HTML = "<html><body>Trend Coffee</body></html>"
ORDER_ID = "order-1"
ORDER_RESPONSE: dict[str, object] = {
    "statusCode": 200,
    "result": {"slug": ORDER_ID, "status": "pending", "items": []},
}
PAYMENT_RESPONSE: dict[str, object] = {
    "statusCode": 200,
    "result": {
        "qrCode": "trend-merchant-qr-1",
        "slug": "payment-1",
        "status": "pending",
        "order": ORDER_ID,
    },
}

# The preset ships without the fixture MCP tool, which only exists to prove
# that an arbitrary MCP result cannot become Data Plane trust.
FIXTURE_MCP_TOOL = "fixture_menu_mcp"


class FakeTrendProvider:
    """One deterministic stand-in for trendcoffee.net across three seams.

    ``fetch`` is the discovery HTTP seam, ``request`` the direct-execution
    transport, and ``observe`` the browser observation port that Task 10 left
    unimplemented. Wiring ``observe`` here is what makes "a warm sync performs
    zero browser actions" an observation instead of an assumption.
    """

    origin = "https://trendcoffee.net"

    def __init__(self) -> None:
        self.http_calls: list[tuple[str, str]] = []
        self.execute_calls: list[tuple[str, str]] = []
        self.browser_calls: list[str] = []
        self.payment_response = dict(PAYMENT_RESPONSE["result"])  # type: ignore[arg-type]
        self._bodies = {
            "/": ("text/html", HOME_HTML),
            "/api/latest/branch": (
                "application/json",
                (TREND_FIXTURES / "branch.json").read_text(),
            ),
            "/api/latest/products": (
                "application/json",
                (TREND_FIXTURES / "products.json").read_text(),
            ),
            f"/api/latest/orders/{ORDER_ID}": (
                "application/json",
                json.dumps(ORDER_RESPONSE),
            ),
            "/api/latest/payment/initiate/public": (
                "application/json",
                json.dumps(PAYMENT_RESPONSE),
            ),
        }

    # -- discovery seam ----------------------------------------------------

    def set_deadline(self, deadline: float | None) -> None:
        return None

    def set_require_https(self, require_https: bool) -> None:
        return None

    def fetch(self, url: str, method: str = "GET") -> httpx.Response:
        self.http_calls.append((method, url))
        return self._respond(method, url)

    # -- browser observation port (never expected to fire) -----------------

    def observe(self, origin: str, timeout_seconds: float) -> tuple[object, ...]:
        self.browser_calls.append(origin)
        return ()

    # -- direct-execution transport ----------------------------------------

    def request(self, **kwargs: object) -> httpx.Response:
        operation = kwargs["operation"]
        url = str(kwargs["url"])
        method = str(getattr(operation, "method", "GET"))
        self.execute_calls.append((method, url))
        return self._respond(method, url)

    def csrf_token(self, url: str, name: str) -> str | None:
        return None

    def close(self) -> None:
        return None

    def _respond(self, method: str, url: str) -> httpx.Response:
        path = urlsplit(url).path or "/"
        body = self._bodies.get(path)
        if body is None:
            return httpx.Response(
                404,
                headers={"content-type": "text/plain"},
                content=b"not found",
                request=httpx.Request(method, url),
            )
        content_type, text = body
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            content=text.encode(),
            request=httpx.Request(method, url),
        )


class FakeMcpClient:
    """An external MCP server that answers with structured menu data."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def call_tool(self, name: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append(name)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "source_id": "trend-coffee",
                            "provider": "trendcoffee",
                            "items": [{"slug": "mcp-item", "price": 1}],
                        }
                    ),
                }
            ],
            "isError": False,
        }


class RecordingPresentation:
    """The customer-display publication boundary, without Browser I/O."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def publish(self, payload: dict[str, object]) -> ToolResult:
        self.payloads.append(payload)
        return ToolResult(tool_name="presentation", content="presentation_published")


def _register_preset_tools() -> None:
    """Put the preset's tools back into the registry.

    ``tests/conftest.py::_clean_registries`` empties ``ToolRegistry`` for every
    test, and the ``@ToolRegistry.register`` decorators only fire on a module's
    first import — so by the time this module runs the registry is empty and
    ``SystemBuilder`` would resolve none of the preset's tools.
    """
    from openjarvis.tools import data_plane as data_plane_tools
    from openjarvis.tools import display, http_request, ordering, shell_exec
    from openjarvis.tools._stubs import ToolSpec
    from openjarvis.tools.mcp_adapter import MCPToolAdapter

    modules = (data_plane_tools, ordering, display, http_request, shell_exec)
    for module in modules:
        for name in dir(module):
            candidate = getattr(module, name)
            if not isinstance(candidate, type) or not hasattr(candidate, "spec"):
                continue
            try:
                tool_name = candidate().spec.name
            except Exception:
                continue
            if not ToolRegistry.contains(tool_name):
                ToolRegistry.register_value(tool_name, candidate)

    if not ToolRegistry.contains(FIXTURE_MCP_TOOL):
        ToolRegistry.register_value(
            FIXTURE_MCP_TOOL,
            partial(
                MCPToolAdapter,
                FakeMcpClient(),
                ToolSpec(
                    name=FIXTURE_MCP_TOOL,
                    description="Fixture external MCP menu source.",
                    parameters={"type": "object", "properties": {}},
                    category="mcp",
                ),
            ),
        )


def build_system(
    tmp_path: Path,
    provider: FakeTrendProvider,
    *,
    trusted_write_operations: str = "",
):
    """Build the real Kiosk preset over the fixture provider."""
    _register_preset_tools()
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = load_config(PRESET)
    config.data_plane.db_path = str(tmp_path / "structured.db")
    config.data_plane.trusted_write_operations = trusted_write_operations
    # `shell_exec` is deliberately absent from the shipped kiosk preset (public
    # terminal, untrusted provider data); the trust-boundary test below needs a
    # raw shell result, so it is widened here, test-locally, and never shipped.
    config.tools.enabled = (
        f"{config.tools.enabled},display_payment_qr,shell_exec,{FIXTURE_MCP_TOOL}"
    )

    engine = MagicMock()
    engine.health.return_value = True
    engine.list_models.return_value = [config.intelligence.default_model]
    system = SystemBuilder(config).engine_instance(engine, key="ollama").build()
    _install_provider(system, provider)
    return system


def _install_provider(system, provider: FakeTrendProvider) -> None:
    runtime = system.data_plane
    assert runtime is not None, "the preset must build a Data Plane runtime"
    runtime.discovery.http = provider
    # Task 10 left `BrowserObservationPort` unimplemented and the engine keeps
    # `browser_observer=None`; installing the fixture observer is the only way
    # a stray browser call could ever be counted.
    runtime.discovery._browser_observer = provider
    runtime.direct._transports[TransportKind.REST] = provider


def call_tool(system, name: str, **arguments: object) -> ToolResult:
    """Invoke a tool the way the Agent does — through the executor."""
    return system.tool_executor.execute(
        ToolCall(id=f"call-{name}", name=name, arguments=json.dumps(arguments))
    )


def payload(result: ToolResult) -> dict[str, object]:
    return json.loads(result.content)


def snapshot_qr(result: ToolResult) -> str:
    items = payload(result)["result"]["items"]  # type: ignore[index]
    return str(items[0]["payload"]["qrCode"])


@pytest.fixture(autouse=True)
def _private_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path / "home"))


@pytest.fixture
def provider() -> FakeTrendProvider:
    return FakeTrendProvider()


@pytest.fixture
def system(tmp_path, provider):
    built = build_system(tmp_path, provider)
    yield built
    built.close()


@pytest.fixture
def runtime(tmp_path, provider):
    """A system whose operator has trusted this exact payment fingerprint.

    The fingerprint is not knowable before discovery, so this mirrors the
    runbook: discover once, read the fingerprint off the capability, then bring
    the system up again with that one exact trust entry configured.
    """
    probe = build_system(tmp_path / "probe", provider)
    discovered = call_tool(probe, "source_discover", source_ref=provider.origin)
    capability = payload(discovered)["discovery"]["capability"]  # type: ignore[index]
    fingerprint = capability["fingerprint"]
    probe.close()

    system = build_system(
        tmp_path / "trusted",
        provider,
        trusted_write_operations=f"trendcoffee:payment.initiate:{fingerprint}",
    )
    call_tool(system, "source_discover", source_ref=provider.origin)
    # payment.initiate may only be proposed for an order the snapshot already
    # holds; the order-placement half of the flow is covered by the runbook.
    system.data_plane.snapshots.upsert(
        NormalizedBatch(
            source_id="trend-coffee",
            resource_type="order",
            records=(
                ResourceRecord(ORDER_ID, {"slug": ORDER_ID, "status": "pending"}),
            ),
            synced_at="2026-08-20T00:00:00+00:00",
        )
    )

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from openjarvis.server import approval_routes

    original_store = approval_routes._store
    approval_routes._store = system.data_plane.approval_gate._store
    app = FastAPI()
    app.include_router(approval_routes.router)
    yield SimpleNamespace(system=system, client=TestClient(app), provider=provider)
    approval_routes._store = original_store
    system.close()


def test_data_plane_gate_shares_the_process_approval_store(system):
    """One store per process, so the approval UI and the gate cannot diverge.

    `ApprovalStore` takes a `db_path`; a second `ApprovalStore()` built for the
    gate only coincidentally resolves the same file as the one the approval UI
    and the proactive agent use.
    """
    from openjarvis.tools.proactive_tools import get_store

    assert system.data_plane.approval_gate._store is get_store()
    # A shared store must not be closed out from under the UI.
    assert system.data_plane.approval_gate._owns_store is False


def test_cold_then_warm_path_persists_and_uses_no_browser(tmp_path, provider):
    first = build_system(tmp_path, provider)
    cold = call_tool(first, "source_discover", source_ref=provider.origin)
    assert payload(cold)["discovery"]["capability"]["provider"] == "trendcoffee"
    first.close()

    cold_http = list(provider.http_calls)
    second = build_system(tmp_path, provider)
    warm = call_tool(
        second,
        "source_sync",
        source_id="trend-coffee",
        resources=["branch", "menu_item"],
    )

    assert warm.success is True
    assert payload(warm)["sync"]["resource_count"] == 2
    # The warm path answered from the persisted capability: no new discovery
    # fetch, and no browser action at any point.
    assert provider.http_calls == cold_http
    assert provider.browser_calls == []
    second.close()


def test_payment_qr_reaches_a_snapshot_only_after_verification(runtime):
    display_qr = next(
        tool
        for tool in runtime.system.tools
        if tool.spec.name == "display_payment_qr"
    )
    presentation = RecordingPresentation()
    display_qr._presentation = presentation
    arguments = {"order": ORDER_ID, "paymentMethod": "bank-transfer"}
    pending = call_tool(
        runtime.system,
        "source_execute",
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments=arguments,
    )
    assert pending.metadata["pending_approval"] is True
    assert presentation.payloads == []

    approved = runtime.client.post(
        f"/v1/approvals/{pending.metadata['approval_id']}/approve"
    )
    assert approved.status_code == 200

    payment = call_tool(
        runtime.system,
        "source_execute",
        source_id="trend-coffee",
        operation="payment.initiate",
        arguments=arguments,
        approval_id=pending.metadata["approval_id"],
    )
    assert payment.success is True
    # the unverified receipt must not carry QR data
    assert "qrCode" not in json.dumps([payment.content, payment.metadata])
    assert presentation.payloads == []

    before = call_tool(
        runtime.system,
        "structured_query",
        source_id="trend-coffee",
        resource_type="payment",
        consistency="cached",
    )
    assert payload(before)["result"]["items"] == []

    verified = call_tool(
        runtime.system,
        "source_verify",
        receipt_id=payload(payment)["receipt"]["receipt_id"],
    )
    assert payload(verified)["verification"]["observed"] is True

    snapshot = call_tool(
        runtime.system,
        "structured_query",
        source_id="trend-coffee",
        resource_type="payment",
        consistency="cached",
    )
    assert snapshot_qr(snapshot) == runtime.provider.payment_response["qrCode"]
    assert presentation.payloads == []

    payment_snapshot = payload(snapshot)["result"]["items"][0]["payload"]
    displayed = call_tool(
        runtime.system,
        "display_payment_qr",
        order_id=payment_snapshot["order"],
        payment_slug=payment_snapshot["slug"],
        status=payment_snapshot["status"],
        qr_code=payment_snapshot["qrCode"],
    )
    assert displayed.success is True
    assert presentation.payloads == [
        {
            "view": "payment_qr",
            "order_id": ORDER_ID,
            "payment_slug": "payment-1",
            "status": "pending",
            "qr_code": "trend-merchant-qr-1",
        }
    ]
    assert runtime.provider.browser_calls == []
    # one mutation, dispatched exactly once and never retried
    assert [call for call in runtime.provider.execute_calls if call[0] == "POST"] == [
        ("POST", "https://trendcoffee.net/api/latest/payment/initiate/public")
    ]


def test_raw_http_shell_and_mcp_results_do_not_gain_data_plane_trust(
    system, provider, monkeypatch
):
    before = _source_ids(system)

    # `shell_exec` needs confirmation; standing in for the operator who granted
    # it keeps this a test about a raw tool that really ran, not one that was
    # refused before it could produce anything.
    monkeypatch.setattr(system.tool_executor, "_interactive", True)
    monkeypatch.setattr(system.tool_executor, "_confirm_callback", lambda prompt: True)
    monkeypatch.setattr(
        httpx,
        "request",
        lambda method, url, **kwargs: provider.fetch(url, method=method),
    )

    fetched = call_tool(
        system,
        "http_request",
        method="GET",
        url="https://trendcoffee.net/api/latest/products",
        # `HttpRequestTool` short-circuits to the compiled Rust client only when
        # no headers are given; a header keeps it on the httpx seam it documents
        # as patchable, so this stays a fixture transport rather than real I/O.
        headers={"Accept": "application/json"},
    )
    ran = call_tool(system, "shell_exec", command="printf structured-candidate")
    mcp = call_tool(system, FIXTURE_MCP_TOOL)

    # each raw tool really did return structured provider-shaped data ...
    assert fetched.success is True and "23f99adf51" in fetched.content
    assert ran.success is True and "structured-candidate" in ran.content
    assert mcp.success is True and "trend-coffee" in mcp.content
    # ... and none of it became a trusted capability.
    assert _source_ids(system) == before


def _source_ids(system) -> list[str]:
    """Every source the capability store trusts, read straight out of it."""
    with sqlite3.connect(system.data_plane.capabilities.db_path) as conn:
        rows = conn.execute("SELECT source_id FROM source_capabilities").fetchall()
    return sorted(row[0] for row in rows)
