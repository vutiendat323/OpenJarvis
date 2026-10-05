"""SkillExecutor scheduling its steps through WorkflowEngine."""

from __future__ import annotations

import json
import threading
import time

from openjarvis.core.conversation import agent_turn_scope, conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.core.types import ToolResult
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.types import SkillManifest, SkillStep
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.display import DisplayCartTool
from openjarvis.workflow.engine import WorkflowEngine

MENU = "https://shop.example/menu"
TABLES = "https://shop.example/tables"
ORDER = "https://shop.example/orders"


def _ok(content: str) -> ToolResult:
    return ToolResult(tool_name="http_request", content=content, success=True)


class RoutedHttp(BaseTool):
    """Answers by URL and records how many requests were in flight at once."""

    tool_id = "http_request"

    def __init__(self, replies: dict[str, list[ToolResult]]):
        self.replies = replies
        self.calls: list[tuple[str, str]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    @property
    def spec(self):
        return ToolSpec(name="http_request", description="Routed HTTP request")

    def execute(self, **params):
        with self._lock:
            self.calls.append((params["method"], params["url"]))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(0.05)
        with self._lock:
            self.in_flight -= 1
            return self.replies[params["url"]].pop(0)


def _step(method: str, url: str, output_key: str) -> SkillStep:
    return SkillStep(
        tool_name="http_request",
        arguments_template=json.dumps({"url": url, "method": method}),
        output_key=output_key,
    )


CHECKOUT = SkillManifest(
    name="checkout",
    checkout=True,
    steps=[
        _step("GET", MENU, "fresh_menu"),
        _step("GET", TABLES, "fresh_tables"),
        _step("POST", ORDER, "created"),
    ],
)


def _harness(replies, *, engine: WorkflowEngine | None):
    cart = DisplayCartTool()
    cart._bus = EventBus()
    http = RoutedHttp(replies)
    return cart, http, SkillExecutor(ToolExecutor([cart, http]), workflow_engine=engine)


def _add_tea(cart) -> int:
    item = {"variant_id": "tea", "name": "Tea", "unit_price": 55_000, "quantity": 1}
    return cart.execute(action="add", item=item).metadata["cart_revision"]


def _checkout(executor, revision):
    with agent_turn_scope() as nonce:
        return executor.run(
            CHECKOUT, initial_context={"cart_revision": revision, "turn_nonce": nonce}
        )


def test_checkout_reads_overlap_and_the_write_waits_for_both():
    replies = {MENU: [_ok("menu")], TABLES: [_ok("tables")], ORDER: [_ok("order")]}
    cart, http, executor = _harness(replies, engine=WorkflowEngine())

    with conversation_scope("workflow-checkout"):
        result = _checkout(executor, _add_tea(cart))

    assert result.success, result.step_results[-1].content
    assert http.max_in_flight == 2
    assert http.calls[-1] == ("POST", ORDER)
    assert [r.content for r in result.step_results] == ["menu", "tables", "order"]
    assert result.context["fresh_tables"] == "tables"


def test_without_an_engine_steps_run_one_at_a_time():
    replies = {MENU: [_ok("menu")], TABLES: [_ok("tables")], ORDER: [_ok("order")]}
    cart, http, executor = _harness(replies, engine=None)

    with conversation_scope("sequential-checkout"):
        result = _checkout(executor, _add_tea(cart))

    assert result.success
    assert http.max_in_flight == 1
    assert http.calls == [("GET", MENU), ("GET", TABLES), ("POST", ORDER)]


def test_a_failed_read_stops_before_the_write_and_releases_the_cart():
    failed = ToolResult(tool_name="http_request", content="HTTP 503", success=False)
    replies = {
        MENU: [failed, _ok("menu")],
        TABLES: [_ok("tables"), _ok("tables")],
        ORDER: [_ok("order")],
    }
    cart, http, executor = _harness(replies, engine=WorkflowEngine())

    with conversation_scope("workflow-read-failure"):
        revision = _add_tea(cart)
        first = _checkout(executor, revision)
        retry = _checkout(executor, revision)

    assert not first.success
    assert first.step_results[-1].content == "HTTP 503"
    assert [r.content for r in first.step_results] == ["HTTP 503"]
    assert ("POST", ORDER) not in http.calls[:2]
    assert retry.success, retry.step_results[-1].content


def test_a_skill_with_nothing_to_overlap_never_enters_the_engine():
    class SpyEngine(WorkflowEngine):
        runs = 0

        def run(self, *args, **kwargs):
            SpyEngine.runs += 1
            return super().run(*args, **kwargs)

    manifest = SkillManifest(
        name="read_then_write",
        steps=[_step("GET", MENU, "menu"), _step("POST", ORDER, "created")],
    )
    http = RoutedHttp({MENU: [_ok("menu")], ORDER: [_ok("order")]})
    executor = SkillExecutor(ToolExecutor([http]), workflow_engine=SpyEngine())

    result = executor.run(manifest)

    assert result.success
    assert SpyEngine.runs == 0
    assert http.calls == [("GET", MENU), ("POST", ORDER)]


def test_skill_manager_hands_its_engine_to_every_executor():
    from openjarvis.skills.manager import SkillManager

    engine = WorkflowEngine()
    manager = SkillManager(EventBus())
    manager._skills["checkout"] = CHECKOUT
    manager.set_workflow_engine(engine)

    (tool,) = manager.get_skill_tools(tool_executor=ToolExecutor([]))

    assert tool._executor._workflow_engine is engine


class _Display(BaseTool):
    def __init__(self, name: str, metadata: dict | None = None):
        self.tool_id = name
        self.metadata = metadata or {}
        self.calls: list[dict] = []

    @property
    def spec(self):
        return ToolSpec(name=self.tool_id, description="recorded display")

    def execute(self, **params):
        self.calls.append(params)
        return ToolResult(
            tool_name=self.tool_id,
            content="shown",
            success=True,
            metadata=self.metadata,
        )


class _MerchantHttp(RoutedHttp):
    """The real checkout recipe's endpoints, answered by URL, never by order."""

    def __init__(self, *, fresh_price: int = 100_000):
        variant = {"slug": "coffee", "price": 100_000, "size": {"name": "M"}}
        replies = {
            "/menu/specific/public": {
                "result": {
                    "hasNext": False,
                    "items": [
                        {
                            "menuItems": [
                                {
                                    "product": {
                                        "name": "Coffee",
                                        "isActive": True,
                                        "variants": [{**variant, "price": fresh_price}],
                                    }
                                }
                            ]
                        }
                    ],
                }
            },
            "/tables?": {"result": [{"name": "73", "slug": "table-73"}]},
            "/orders/public": {
                "result": {
                    "slug": "order-1",
                    "status": "pending",
                    "subtotal": 200_000,
                    "type": "at-table",
                    "description": "",
                    "table": {"name": "73", "slug": "table-73"},
                    "orderItems": [
                        {
                            "variant": {**variant, "product": {"name": "Coffee"}},
                            "quantity": 2,
                            "note": "",
                            "promotion": None,
                        }
                    ],
                }
            },
            "/payment/initiate/public": {
                "result": {
                    "slug": "payment-1",
                    "amount": 200_000,
                    "paymentMethod": "bank-transfer",
                    "statusCode": "pending",
                }
            },
        }
        super().__init__({})
        self.routes = replies

    def execute(self, **params):
        with self._lock:
            self.calls.append((params["method"], params["url"]))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(0.05)
        with self._lock:
            self.in_flight -= 1
        (body,) = [b for part, b in self.routes.items() if part in params["url"]]
        return _ok(json.dumps(body))


def _real_checkout(http: _MerchantHttp):
    from pathlib import Path

    from openjarvis.skills.loader import load_skill
    from openjarvis.skills.tool_adapter import SkillTool

    skills = Path(__file__).resolve().parents[2] / "skills"
    cart = DisplayCartTool()
    bill = _Display("display_bill")
    qr = _Display("display_payment_qr", {"completed_display": True})
    tool = SkillTool(
        load_skill(skills / "trendcoffee-checkout.toml"),
        SkillExecutor(
            ToolExecutor([cart, http, bill, qr]), workflow_engine=WorkflowEngine()
        ),
    )
    line = {"variant_id": "coffee", "name": "Coffee", "unit_price": 100_000}
    with conversation_scope(f"real-checkout-{id(http)}"), agent_turn_scope() as nonce:
        result = tool.execute(
            order_type="at-table",
            order_note="",
            table="table-73",
            customer_message="Mời bạn quét mã",
            turn_nonce=nonce,
            cart_lines=[{**line, "quantity": 2, "size": "M", "note": ""}],
        )
    return result, bill, qr


def test_the_real_checkout_recipe_overlaps_its_reads_and_ends_the_turn_itself():
    http = _MerchantHttp()

    result, bill, qr = _real_checkout(http)

    assert result.success, result.content
    assert http.max_in_flight == 2
    assert [method for method, _ in http.calls] == ["GET", "GET", "POST", "POST"]
    assert "/orders/public" in http.calls[2][1]
    assert "/payment/initiate/public" in http.calls[3][1]
    assert len(bill.calls) == len(qr.calls) == 1
    # The terminal fast path: no second model round is needed after this.
    assert result.metadata["completed_display"] is True
    assert result.metadata["customer_message"] == "Mời bạn quét mã"


def test_the_real_checkout_recipe_writes_nothing_after_a_stale_menu():
    http = _MerchantHttp(fresh_price=101_000)

    result, bill, qr = _real_checkout(http)

    assert not result.success
    assert "assertion failed at step 0" in result.content
    assert [method for method, _ in http.calls] == ["GET", "GET"]
    assert bill.calls == qr.calls == []
