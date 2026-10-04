"""Measure real model turns against an isolated, deterministic fake merchant.

No merchant requests, real orders, microphone, TTS or production display are used.
All prepared skills and checkout/evidence guards run through the normal executor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from openjarvis.agents._stubs import AgentTextDelta
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolResult
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.loader import load_skill
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec
from openjarvis.tools.display import (
    DisplayBillTool,
    DisplayCartMemoryTool,
    DisplayCartTool,
    DisplayClearTool,
    DisplayMenuMemoryTool,
    DisplayMenuTool,
    DisplayPaymentQrTool,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://trendcoffee.net"
ROWS = [
    {"id": "latte", "name": "Latte", "price": 51_000, "available": True},
    {"id": "matcha-latte", "name": "Matcha latte", "price": 55_000, "available": True},
    {"id": "yaourt-dau", "name": "Yaourt dâu", "price": 45_000, "available": True},
]


class FakeMerchant(BaseTool):
    """Accept only fixed fixture URLs; never open a network connection."""

    tool_id = "http_request"

    def __init__(self):
        self.calls = []
        self.fresh_price = None
        self.available = True
        self.payment_amount = None
        self.payment_status = "pending"
        self.timeout_write = False
        self.wrong_quantity = False

    @property
    def spec(self):
        return ToolSpec(name=self.tool_id, description="Isolated fake merchant")

    def execute(self, **params):
        assert params["url"].startswith(ORIGIN + "/api/latest/")
        method = params.get("method", "GET")
        if method not in {"GET", "HEAD"}:
            assert self._checkout_guard(), "checkout claim missing"
        self.calls.append(params)
        path = params["url"].split("/api/latest/", 1)[1].split("?", 1)[0]
        if method == "GET" and path == "menu/specific/public":
            result = {
                "hasNext": False,
                "items": [
                    {
                        "menuItems": [
                            {
                                "product": {
                                    "name": row["name"],
                                    "description": "",
                                    "image": "fixture",
                                    "catalog": {
                                        "name": "cà phê"
                                        if row["id"] == "latte"
                                        else "đồ uống"
                                    },
                                    "isActive": self.available,
                                    "isTopSell": False,
                                    "isNew": False,
                                    "variants": [
                                        {
                                            "slug": row["id"],
                                            "price": self.fresh_price or row["price"],
                                            "size": {"name": "Tiêu chuẩn"},
                                        }
                                    ],
                                }
                            }
                            for row in ROWS
                        ]
                    }
                ],
            }
        elif method == "GET" and path == "tables":
            result = [
                {"slug": "table-1", "name": "1", "status": "available"},
                {"slug": "table-2", "name": "2", "status": "reserved"},
            ]
        elif method == "POST" and path == "orders/public":
            if self.timeout_write:
                return ToolResult(
                    tool_name=self.tool_id, success=False, content="Request timed out"
                )
            body = json.loads(params["body"])
            rows = {row["id"]: row for row in ROWS}
            self.order = {
                "slug": "fixture-order",
                "type": body["type"],
                "description": body["description"],
                "status": "pending",
                "table": (
                    {"slug": body["table"], "name": "1"} if body["table"] else None
                ),
                "subtotal": sum(
                    rows[i["variant"]]["price"] * i["quantity"]
                    for i in body["orderItems"]
                ),
                "orderItems": [
                    {
                        "variant": {
                            "slug": i["variant"],
                            "price": rows[i["variant"]]["price"],
                            "size": {"name": "Tiêu chuẩn"},
                            "product": {"name": rows[i["variant"]]["name"]},
                        },
                        "quantity": 99 if self.wrong_quantity else i["quantity"],
                        "note": i["note"],
                        "promotion": None,
                    }
                    for i in body["orderItems"]
                ],
            }
            result = self.order
        elif method == "POST" and path == "payment/initiate/public":
            assert json.loads(params["body"])["orderSlug"] == self.order["slug"]
            result = {
                "slug": "fixture-payment",
                "amount": self.payment_amount or self.order["subtotal"],
                "paymentMethod": "bank-transfer",
                "statusCode": self.payment_status,
                "qrCode": "fixture-qr-never-pay",
            }
        else:
            raise AssertionError(f"Unrecognized fixture request: {method} {path}")
        return ToolResult(
            tool_name=self.tool_id,
            content=json.dumps({"result": result}),
            metadata={
                "status_code": 200,
                "final_url": params["url"],
                "content_type": "application/json",
                "truncated": False,
            },
        )


class OrderingFixture:
    def __init__(self, skills_dir=ROOT / "skills"):
        self.bus = EventBus()
        self.shop = FakeMerchant()
        self.cart, self.menu = DisplayCartTool(), DisplayMenuTool()
        self.qr, self.bill, self.clear = (
            DisplayPaymentQrTool(),
            DisplayBillTool(),
            DisplayClearTool(),
        )
        self.displays = []
        self.bus.subscribe(
            EventType.DISPLAY_UPDATE, lambda event: self.displays.append(event.data)
        )
        for tool in (self.cart, self.menu, self.qr, self.bill, self.clear):
            tool._bus = self.bus
        self.qr._payment_trusted_origins = SystemBuilder._parse_trusted_origins(ORIGIN)
        internal = [self.shop, self.cart, self.menu, self.bill, self.qr, self.clear]
        executor = ToolExecutor(internal, self.bus)
        self.skills = [
            SkillTool(
                load_skill(skills_dir / f"trendcoffee-{name}.toml"),
                SkillExecutor(executor, bus=self.bus),
            )
            for name in ("menu", "tables", "add-to-cart", "checkout")
        ]
        SystemBuilder._inject_checkout_guard([*internal, *self.skills])
        SystemBuilder._inject_draft_cart_settler(internal)
        self.tools = [
            DisplayMenuMemoryTool(self.menu),
            DisplayCartMemoryTool(self.cart, self.menu),
            self.clear,
            *self.skills,
        ]

    def seed(self, *, menu=False, cart=False, ambiguous=False, order_type="take-out"):
        if menu:
            assert self.menu.execute(
                items=ROWS if ambiguous else [ROWS[0]], result_complete=True
            ).success
        if cart:
            assert self.cart.edit_without_display(
                "add",
                {
                    "item": {
                        "variant_id": "latte",
                        "name": "Latte",
                        "unit_price": 51_000,
                        "quantity": 2,
                        "note": "",
                        "size": "",
                    }
                },
            ).success
            if order_type:
                assert self.cart.edit_without_display(
                    "set_order_type", {"order_type": order_type}
                ).success
        self.displays.clear()


FLOWS = [
    ("menu", "Cho xem các món latte", {}),
    ("add_shown", "Thêm hai Latte vào giỏ và mở giỏ", {"menu": True}),
    ("view_cart", "Xem giỏ hàng", {"cart": True}),
    ("checkout_draft", "Thanh toán đơn mang về này, lấy QR", {"cart": True}),
    ("checkout_named", "Mua hai Yaourt dâu mang về, thanh toán lấy QR ngay", {}),
    ("clarify", "Thêm món đó vào giỏ", {"menu": True, "ambiguous": True}),
    ("tables", "Bàn nào còn trống?", {}),
    ("missing_type", "Đặt giỏ hàng này và lấy QR", {"cart": True, "order_type": ""}),
]


class MeteredEngine:
    def __init__(self, engine):
        self.inner = engine
        self.calls = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.engine_id = engine.engine_id

    def supports_semantic_reasoning_stream(self, model):
        return self.inner.supports_semantic_reasoning_stream(model)

    async def stream_full(self, *args, **kwargs):
        self.calls += 1
        async for chunk in self.inner.stream_full(*args, **kwargs):
            for key in self.usage:
                self.usage[key] += (chunk.usage or {}).get(key, 0)
            yield chunk

    def generate(self, *args, **kwargs):
        self.calls += 1
        result = self.inner.generate(*args, **kwargs)
        for key in self.usage:
            self.usage[key] += result.get("usage", {}).get(key, 0)
        return result


async def benchmark(args):
    from dotenv import load_dotenv

    from openjarvis.engine.cloud import CloudEngine

    load_dotenv(ROOT / ".env", override=False)
    cloud = CloudEngine()
    prompt = args.prompt.read_text()
    rows = []
    try:
        for repeat in range(args.repetitions):
            for flow, query, seed in FLOWS:
                if args.flow and flow not in args.flow:
                    continue
                fixture = OrderingFixture(args.skills_dir)
                engine = MeteredEngine(cloud)
                agent = OrchestratorAgent(
                    engine,
                    args.model,
                    tools=fixture.tools,
                    bus=fixture.bus,
                    system_prompt=prompt,
                    max_tokens=4096,
                    max_turns=8,
                )
                times, speech, arguments = [], [], []
                fixture.bus.subscribe(
                    EventType.TOOL_CALL_START,
                    lambda event: times.append(
                        (time.perf_counter(), event.data["tool"])
                    ),
                )
                fixture.bus.subscribe(
                    EventType.TOOL_CALL_START,
                    lambda event: (
                        arguments.append(
                            {
                                "tool": event.data["tool"],
                                "uses_explicit_nonce": "turn_nonce"
                                in event.data["arguments"],
                                "arguments": {
                                    key: value
                                    for key, value in event.data["arguments"].items()
                                    if key
                                    in {
                                        "items",
                                        "itemTerms",
                                        "categoryTerms",
                                        "minPrice",
                                        "maxPrice",
                                        "displayMode",
                                    }
                                },
                            }
                        )
                        if event.data["tool"].startswith("skill_")
                        else None
                    ),
                )
                with conversation_scope("voice-bench-" + uuid4().hex):
                    fixture.seed(**seed)
                    started = time.perf_counter()
                    try:
                        async with asyncio.timeout(args.timeout):
                            events = []
                            async for event in agent.run_stream(query):
                                events.append(event)
                                if isinstance(event, AgentTextDelta):
                                    speech.append(
                                        (time.perf_counter() - started, event.content)
                                    )
                        result = events[-1].result
                        finished = time.perf_counter() - started
                        spoken = "".join(text for _, text in speech)
                        writes = sum(
                            p.get("method") == "POST" for p in fixture.shop.calls
                        )
                        checkout_done = bool(
                            fixture.displays
                            and fixture.displays[-1]["view"] == "payment_qr"
                        )
                        behavior_ok = all(r.success for r in result.tool_results)
                        snapshot = fixture.cart.current_snapshot()
                        cart_lines = [
                            {
                                key: row[key]
                                for key in (
                                    "variant_id",
                                    "quantity",
                                    "unit_price",
                                    "note",
                                )
                            }
                            for row in snapshot["lines"]
                        ]
                        order = getattr(fixture.shop, "order", None)
                        order_lines = (
                            [
                                {
                                    "variant_id": row["variant"]["slug"],
                                    "quantity": row["quantity"],
                                    "note": row["note"],
                                }
                                for row in order["orderItems"]
                            ]
                            if order
                            else []
                        )
                        menu_ids = [
                            item["id"]
                            for display in fixture.displays
                            if display["view"] == "menu"
                            for item in display.get("items", [])
                        ]
                        if flow.startswith("checkout_"):
                            expected_id = (
                                "latte" if flow == "checkout_draft" else "yaourt-dau"
                            )
                            behavior_ok = (
                                behavior_ok
                                and writes == 2
                                and checkout_done
                                and order is not None
                                and order["type"] == "take-out"
                                and order["description"] == ""
                                and order["table"] is None
                                and order_lines
                                == [
                                    {
                                        "variant_id": expected_id,
                                        "quantity": 2,
                                        "note": "",
                                    }
                                ]
                            )
                        elif flow in {"add_shown", "view_cart"}:
                            behavior_ok = (
                                behavior_ok
                                and not fixture.shop.calls
                                and cart_lines
                                == [
                                    {
                                        "variant_id": "latte",
                                        "quantity": 2,
                                        "unit_price": 51_000,
                                        "note": "",
                                    }
                                ]
                                and bool(fixture.displays)
                                and fixture.displays[-1]["view"] == "cart"
                            )
                        elif flow in {"clarify", "missing_type"}:
                            behavior_ok = (
                                behavior_ok
                                and not result.tool_results
                                and spoken.count("?") == 1
                            )
                        elif flow == "menu":
                            behavior_ok = behavior_ok and set(menu_ids) == {
                                "latte",
                                "matcha-latte",
                            }
                        record = {
                            "variant": args.variant,
                            "repeat": repeat,
                            "flow": flow,
                            "model": args.model,
                            "model_calls": engine.calls,
                            "tool_calls": len(result.tool_results),
                            "internal_tool_calls": len(times),
                            "tool_names": [r.tool_name for r in result.tool_results],
                            "tool_arguments": arguments,
                            "time_to_first_tool_s": round(times[0][0] - started, 4)
                            if times
                            else None,
                            "time_to_final_response_s": round(finished, 4),
                            "spoken_words": len(spoken.split()),
                            "speech": spoken,
                            "speech_before_tool": bool(
                                times
                                and speech
                                and speech[0][0] < times[0][0] - started
                            ),
                            "failed_tools": [
                                r.tool_name
                                for r in result.tool_results
                                if not r.success
                            ],
                            "failure_details": [
                                {"tool": r.tool_name, "content": r.content}
                                for r in result.tool_results
                                if not r.success
                            ],
                            "merchant_writes": writes,
                            "behavior_ok": behavior_ok,
                            "menu_ids": menu_ids,
                            "cart_lines": cart_lines,
                            "order_lines": order_lines,
                            "max_turns_exceeded": result.metadata.get(
                                "max_turns_exceeded", False
                            ),
                            **engine.usage,
                        }
                    except Exception as exc:
                        record = {
                            "variant": args.variant,
                            "repeat": repeat,
                            "flow": flow,
                            "error": type(exc).__name__,
                            "model_calls": engine.calls,
                        }
                rows.append(record)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
                )
                print(json.dumps(record, ensure_ascii=False), flush=True)
                if record.get("error"):
                    return
    finally:
        cloud.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prompt",
        type=Path,
        default=ROOT / "configs/openjarvis/prompts/ordering-kiosk.md",
    )
    parser.add_argument("--skills-dir", type=Path, default=ROOT / "skills")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--flow", action="append", choices=[f[0] for f in FLOWS])
    parser.add_argument("--variant", default="baseline")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(benchmark(parser.parse_args()))


if __name__ == "__main__":
    main()
