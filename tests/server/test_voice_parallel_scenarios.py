"""Replay the 2026-10-17 18:14-18:16 kiosk session against parallel Agent runs.

Each test drives the real voice service over the real ``NativeAgentRuntime``
(two concurrent runs), the real ``OrchestratorAgent``, the real ledger, run
tracker, write gate and cart. Only the edges are scripted: a streaming engine
that answers the way the model did (or should have) at that moment, a fake
Responder, and a sink in place of TTS. Delays are controlled with events, so no
test depends on the wall clock; the latency targets of the spec are checked
live, not here.

The log of that session (``.stack-logs/backend.log`` lines 17721-18087):

* 18:14:50 "Cho mình một nước ép các loại mang đi" is delegated; 18:15:00
  "Alo, bạn có nghe không?" cut the Agent run, the juice was never added and
  the customer was asked again at 18:15:10 and 18:15:20.
* 18:15:35 "thêm tất cả các món sinh tố" ran a menu read; 18:15:44 and 18:15:53
  two "Alo" cut it; 18:16:00 "Bạn thêm chưa?" started a new run that added the
  smoothies and the cart already held them.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.agents.orchestrator import OrchestratorAgent
from openjarvis.agents.runtime import NativeAgentRuntime
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.core.runs import RUN_TRACKER
from openjarvis.core.types import Role
from openjarvis.engine._stubs import StreamChunk
from openjarvis.server.voice.llm import OpenJarvisLLMService, inflight_prompt
from openjarvis.server.voice.responder import ResponderReply
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.skills.types import SkillManifest
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.await_requests import AwaitEarlierRequestsTool
from openjarvis.tools.display import DisplayCartTool
from openjarvis.tools.http_request import HttpRequestTool
from tests.skills.test_checkout_barge_in import (
    MENU,
    ORDERS,
    PAYMENT,
    _Display,
    _step,
)

STEP = 5.0

JUICE = "Cho mình một nước ép các loại mang đi."
ALO = "Alo, bạn có nghe không?"
SMOOTHIES = "Ở phần sinh tố, bạn thêm tất cả các món sinh tố vào giỏ cho mình."
DID_YOU = "Bạn thêm chưa?"
COFFEE = "Thêm cho mình một cà phê sữa."
TAKE_OUT = "Mình mang đi nhé."
NO_SMOOTHIES = "Thôi mình không lấy sinh tố nữa."
PAY = "Thanh toán cho mình."

HEARD = "Mình nghe rõ bạn."
CHECKING = "Để mình xem qua nhé."


def _user(text):
    return {"role": "user", "content": text}


def _bot(text="Dạ."):
    return {"role": "assistant", "content": text}


def _ctx(*messages):
    return LLMContextFrame(LLMContext(messages=[dict(m) for m in messages]))


def _add(variant):
    return {"action": "add", "item": _item(variant)}


def _item(variant, price=35000):
    return {
        "variant_id": variant,
        "name": variant.title(),
        "size": "M",
        "note": "",
        "unit_price": price,
        "quantity": 1,
    }


# -- scripted model ---------------------------------------------------------


def _call(name, arguments):
    return [
        StreamChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": f"call-{name}-{time.monotonic_ns()}",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
        ),
        StreamChunk(finish_reason="tool_calls"),
    ]


def _say(text):
    return [StreamChunk(content=text), StreamChunk(finish_reason="stop")]


def _prompt(messages) -> str:
    return next(m.content for m in reversed(messages) if m.role == Role.USER)


def _asked(messages) -> str:
    """The customer's latest words, with or without the in-progress block."""
    prompt = _prompt(messages)
    if "<latest_request>" in prompt:
        return prompt.split("<latest_request>")[1].split("</latest_request>")[0]
    return prompt


def _tool_results(messages) -> list[str]:
    return [m.content for m in messages if m.role == Role.TOOL]


def _runtime_context(messages) -> dict:
    for message in reversed(messages):
        if message.role == Role.SYSTEM and "<runtime_context>" in message.content:
            body = message.content.split("<runtime_context>")[1]
            return json.loads(body.split("</runtime_context>")[0])
    return {}


class _Engine:
    """Streams what ``script`` returns; a request can be held until released."""

    def __init__(self, script):
        self._script = script
        self.rounds: list[tuple[str, int]] = []
        self.holds: dict[str, asyncio.Event] = {}

    def hold(self, request) -> asyncio.Event:
        return self.holds.setdefault(request, asyncio.Event())

    def asked(self, request) -> int:
        return sum(1 for ask, _ in self.rounds if ask == request)

    def generate(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("streaming only")

    def supports_semantic_reasoning_stream(self, model):
        del model
        return True

    async def stream_full(self, messages, **kwargs):
        del kwargs
        ask = _asked(messages)
        self.rounds.append((ask, len(_tool_results(messages))))
        if ask in self.holds:
            await self.holds[ask].wait()
        for chunk in await self._script(list(messages)):
            yield chunk


# -- scripted edges ---------------------------------------------------------


class _Responder:
    """Answers the hear check itself; delegates every request, with a filler."""

    model = "fast-model"

    def __init__(self, plan):
        self._plan = plan
        self.calls: list[str] = []
        self.in_progress: list[list[str]] = []

    async def respond(
        self, utterance, conversation="", on_delegate=None, in_progress=()
    ):
        del conversation
        self.calls.append(utterance)
        self.in_progress.append(list(in_progress))
        kind, text = self._plan(utterance)
        if kind == "answer":
            return ResponderReply("answer", text)
        if on_delegate is not None:
            on_delegate(text)
        return ResponderReply("delegate", text)


def _plan(utterance):
    return ("answer", HEARD) if utterance == ALO else ("delegate", CHECKING)


class _Sink(FrameProcessor):
    """Stands in for TTS: records what would be spoken, in order."""

    def __init__(self, log):
        super().__init__()
        self.texts: list[str] = []
        self.ends = 0
        self._log = log

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self.texts.append(frame.text)
            self._log.append(f"said:{frame.text.strip()}")
        elif isinstance(frame, LLMFullResponseEndFrame):
            self.ends += 1
        await self.push_frame(frame, direction)

    def spoke(self, text):
        return text in "".join(self.texts)


class _Hold:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()


class _Cart(DisplayCartTool):
    """The real draft cart; an add of a held variant waits for its release."""

    def __init__(self, log):
        super().__init__()
        self._bus = EventBus()
        self.log = log
        self.holds: dict[str, _Hold] = {}
        self.writes: list[tuple[str, tuple[str, ...]]] = []

    def hold_adds_of(self, variant) -> _Hold:
        return self.holds.setdefault(variant, _Hold())

    def execute(self, **params):
        action = params.get("action")
        variants: tuple[str, ...] = ()
        if action == "add":
            items = params.get("items") or [params.get("item")]
            variants = tuple(item["variant_id"] for item in items)
            for variant in variants:
                if variant in self.holds:
                    self.holds[variant].started.set()
                    assert self.holds[variant].release.wait(STEP * 2)
        result = super().execute(**params)
        if action not in (None, "view"):
            self.writes.append((action, variants))
            self.log.append(f"{action}:{','.join(variants)}")
        return result

    def state(self, conversation):
        snapshot = self.current_snapshot_for(conversation)
        return (
            [line["variant_id"] for line in snapshot["lines"]],
            snapshot["order_type"],
        )

    def current_snapshot_for(self, conversation):
        with conversation_scope(conversation):
            return self.current_snapshot()

    def seed(self, conversation, variant="coffee"):
        with conversation_scope(conversation):
            result = super().execute(action="add", item=_item(variant))
        assert result.success
        return result.metadata["cart_revision"]


# -- rig --------------------------------------------------------------------


class _Rig:
    def __init__(self, conversation, script, tools, *, plan=_plan, slots=2):
        self.conversation = conversation
        self.log: list[str] = []
        self.engine = _Engine(script)
        self.runtime = NativeAgentRuntime(
            OrchestratorAgent(self.engine, "fake", tools=tools),
            max_concurrent_runs=slots,
        )
        self.responder = _Responder(plan)
        self.service = OpenJarvisLLMService(
            self.runtime.bind(model="fake"), responder=self.responder
        )
        self.sink = _Sink(self.log)

    async def drive(self, steps):
        worker = PipelineWorker(
            Pipeline([self.service, self.sink]),
            cancel_on_idle_timeout=False,
            enable_rtvi=False,
            enable_turn_tracking=False,
        )
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

        async def drive():
            await asyncio.sleep(0.01)
            try:
                await steps(worker)
            except BaseException:
                # A failed step cancels the session, so no blocked run hangs it.
                await worker.queue_frame(CancelFrame())
                raise
            await worker.queue_frame(EndFrame())

        await runner.add_workers(worker)
        with conversation_scope(self.conversation):
            results = await asyncio.wait_for(
                asyncio.gather(runner.run(), drive(), return_exceptions=True),
                timeout=30,
            )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    def cleanup(self):
        ACTION_LEDGER.clear(self.conversation)
        RUN_TRACKER.close(self.conversation)


async def _eventually(read, timeout=STEP):
    deadline = asyncio.get_running_loop().time() + timeout
    while not (value := read()):
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)
    return value


async def _wait_set(event: threading.Event, timeout=STEP):
    assert await asyncio.to_thread(event.wait, timeout), "event not set"


async def _barge_in(worker):
    await worker.queue_frame(InterruptionFrame())
    await asyncio.sleep(0.05)


async def _settled(rig):
    """Every run has ended and the tracker is empty."""
    await _eventually(lambda: not RUN_TRACKER.in_flight(rig.conversation))
    await asyncio.sleep(0.05)


@pytest.fixture
def conversation():
    name = f"parallel-scenario-{time.monotonic_ns()}"
    yield name
    ACTION_LEDGER.clear(name)
    RUN_TRACKER.close(name)


# -- scenarios --------------------------------------------------------------


@pytest.mark.anyio
async def test_juice_then_alo_adds_the_juice_without_asking_again(conversation):
    log: list[str] = []
    cart = _Cart(log)
    cart.hold_adds_of("juice")

    async def script(messages):
        ask = _asked(messages)
        assert ask == JUICE, f"the Agent was asked something else: {ask}"
        if not _tool_results(messages):
            return _call("display_cart", {"action": "add", "item": _item("juice")})
        return _say("Đã thêm nước ép.")

    rig = _Rig(conversation, script, [cart])
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(JUICE)))
        await _wait_set(cart.holds["juice"].started)
        # 18:15:00: "Alo" while the add is still being written.
        await _barge_in(worker)
        await worker.queue_frame(_ctx(_user(JUICE), _bot(), _user(ALO)))
        await _eventually(lambda: rig.sink.spoke(HEARD))
        seen["lines_before"] = cart.state(conversation)[0]
        seen["in_flight"] = len(RUN_TRACKER.in_flight(conversation))
        cart.holds["juice"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        # The juice is in the cart once, and "Alo" never reached the Agent.
        assert seen["lines_before"] == []
        assert seen["in_flight"] == 1
        assert cart.state(conversation)[0] == ["juice"]
        assert cart.writes == [("add", ("juice",))]
        assert {ask for ask, _ in rig.engine.rounds} == {JUICE}
        assert rig.responder.calls == [JUICE, ALO]
        assert rig.sink.spoke(HEARD)
        # The barge-in fenced the run: it did not speak over the answer, and
        # its outcome is left unreported for the reporter.
        assert not rig.sink.spoke("Đã thêm nước ép.")
        entries = ACTION_LEDGER.entries(conversation)
        assert entries and not any(entry.reported for entry in entries)
        assert any(entry.kind == "run" and entry.request == JUICE for entry in entries)
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_smoothies_then_alo_then_did_you_add_does_not_add_twice(conversation):
    log: list[str] = []
    cart = _Cart(log)
    cart.hold_adds_of("sinh-to-xoai")
    items = [_item("sinh-to-xoai"), _item("sinh-to-dau")]
    prompts: list[str] = []

    async def script(messages):
        ask = _asked(messages)
        prompts.append(_prompt(messages))
        if ask == SMOOTHIES:
            if not _tool_results(messages):
                return _call("display_cart", {"action": "add", "items": items})
            return _say("Đã thêm các món sinh tố.")
        assert ask == DID_YOU
        if "<requests_in_progress>" in _prompt(messages):
            return _say("Mình đang thêm sinh tố, chưa xong ạ.")
        # What the model did live at 18:16:00 without being told: add again.
        return _call("display_cart", {"action": "add", "items": items})

    rig = _Rig(conversation, script, [cart])
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to-xoai"].started)
        # 18:15:44 and 18:15:53: "Alo" twice.
        await _barge_in(worker)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(ALO)))
        await _eventually(lambda: rig.sink.spoke(HEARD))
        await worker.queue_frame(
            _ctx(_user(SMOOTHIES), _bot(), _user(ALO), _bot(HEARD), _user(DID_YOU))
        )
        await _eventually(lambda: rig.engine.asked(DID_YOU) >= 1)
        await _eventually(lambda: rig.sink.spoke("chưa xong"))
        seen["adds_while_waiting"] = list(cart.writes)
        cart.holds["sinh-to-xoai"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["adds_while_waiting"] == []
        # One add tool call in the whole session.
        assert cart.writes == [("add", ("sinh-to-xoai", "sinh-to-dau"))]
        lines = cart.current_snapshot_for(conversation)["lines"]
        assert [(line["variant_id"], line["quantity"]) for line in lines] == [
            ("sinh-to-xoai", 1),
            ("sinh-to-dau", 1),
        ]
        # The second run's prompt holds the in-progress block.
        second = next(p for p in prompts if DID_YOU in p)
        assert second == inflight_prompt([SMOOTHIES], DID_YOU)
        assert "<requests_in_progress>" in second and SMOOTHIES in second
        assert rig.responder.in_progress[-1] == [SMOOTHIES]
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_smoothies_then_add_coffee_runs_in_parallel_and_keeps_both(
    conversation,
):
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")

    async def script(messages):
        ask = _asked(messages)
        if _tool_results(messages):
            return _say("Đã thêm.")
        variant = "sinh-to" if ask == SMOOTHIES else "ca-phe-sua"
        return _call("display_cart", {"action": "add", "item": _item(variant)})

    rig = _Rig(conversation, script, [cart])
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to"].started)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(COFFEE)))
        # The second run starts its model while the first run's write is held.
        await _eventually(lambda: rig.engine.asked(COFFEE) >= 1)
        seen["in_flight"] = len(RUN_TRACKER.in_flight(conversation))
        seen["written_early"] = list(cart.writes)
        await asyncio.sleep(0.2)
        seen["coffee_waits"] = list(cart.writes)
        cart.holds["sinh-to"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["in_flight"] == 2
        assert seen["written_early"] == [] and seen["coffee_waits"] == []
        # Writes of one conversation run one after the other, in order.
        assert cart.writes == [("add", ("sinh-to",)), ("add", ("ca-phe-sua",))]
        assert cart.state(conversation)[0] == ["sinh-to", "ca-phe-sua"]
        # The newest request speaks; the older one was fenced.
        assert rig.sink.spoke("Đã thêm.")
        assert rig.engine.asked(SMOOTHIES) == 2 and rig.engine.asked(COFFEE) == 2
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_smoothies_then_take_out_keeps_the_items_and_changes_the_type(
    conversation,
):
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")

    async def script(messages):
        ask = _asked(messages)
        if _tool_results(messages):
            return _say("Đã ghi mang đi.")
        if ask == SMOOTHIES:
            return _call("display_cart", {"action": "add", "item": _item("sinh-to")})
        assert ask == TAKE_OUT
        return _call(
            "display_cart", {"action": "set_order_type", "order_type": "take-out"}
        )

    rig = _Rig(conversation, script, [cart])
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to"].started)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(TAKE_OUT)))
        await _eventually(lambda: rig.engine.asked(TAKE_OUT) >= 1)
        await asyncio.sleep(0.2)
        seen["before_release"] = (cart.state(conversation), list(cart.writes))
        cart.holds["sinh-to"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["before_release"] == (([], ""), [])
        # The type is saved after the add, on top of the items.
        assert [w[0] for w in cart.writes] == ["add", "set_order_type"]
        assert cart.state(conversation) == (["sinh-to"], "take-out")
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_smoothies_then_no_smoothies_waits_and_ends_without_them(conversation):
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")
    smoothies = {"sinh-to"}

    async def script(messages):
        ask = _asked(messages)
        results = _tool_results(messages)
        if ask == SMOOTHIES:
            if not results:
                return _call(
                    "display_cart", {"action": "add", "item": _item("sinh-to")}
                )
            return _say("Đã thêm sinh tố.")
        assert ask == NO_SMOOTHIES
        if not results:
            return _call("await_earlier_requests", {})
        if len(results) == 1:
            # After the wait the model reads the real cart, then removes.
            assert "earlier_requests_finished" in results[0], results[0]
            lines = _runtime_context(messages)["draft_cart"]["lines"]
            ids = [line["line_id"] for line in lines if line["variant_id"] in smoothies]
            assert ids, "the smoothies were not in the cart after the wait"
            return _call("display_cart", {"action": "remove", "line_ids": ids})
        return _say("Đã bỏ sinh tố.")

    rig = _Rig(conversation, script, [cart, AwaitEarlierRequestsTool()], plan=_plan)
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to"].started)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(NO_SMOOTHIES)))
        await _eventually(lambda: rig.engine.asked(NO_SMOOTHIES) >= 1)
        # The second run called await_earlier_requests and is blocked in it.
        await asyncio.sleep(0.2)
        seen["still_waiting"] = rig.engine.asked(NO_SMOOTHIES) == 1
        seen["writes_before"] = list(cart.writes)
        cart.holds["sinh-to"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["still_waiting"] and seen["writes_before"] == []
        # Added by the first run, removed by the second, in that order.
        assert [w[0] for w in cart.writes] == ["add", "remove"]
        assert cart.state(conversation)[0] == []
        assert rig.sink.spoke("Đã bỏ sinh tố.")
    finally:
        rig.cleanup()


def _checkout(monkeypatch, cart):
    http = HttpRequestTool()
    bill, qr = _Display("display_bill"), _Display("display_payment_qr")
    sent: list[tuple[str, str, str]] = []

    def send(method, url, **kwargs):
        sent.append((method, url, str(kwargs.get("content"))))
        return httpx.Response(
            201, json={"ok": True}, request=httpx.Request(method, url)
        )

    monkeypatch.setattr(http, "_request_following_redirects", send)
    monkeypatch.setattr("openjarvis.tools.http_request.check_ssrf", lambda _url: None)
    manifest = SkillManifest(
        name="checkout",
        checkout=True,
        steps=[
            _step("http_request", {"url": MENU, "method": "GET"}),
            _step(
                "http_request",
                {"url": ORDERS, "method": "POST", "body": "total={draft_cart.total}"},
            ),
            _step("display_bill"),
            _step("http_request", {"url": PAYMENT, "method": "POST", "body": ""}),
            _step("display_payment_qr"),
        ],
    )
    inner = [cart, http, bill, qr]
    skill = SkillTool(manifest, SkillExecutor(ToolExecutor(inner)))
    SystemBuilder._inject_checkout_guard(inner + [skill])
    return skill, sent


@pytest.mark.anyio
async def test_pay_while_smoothies_are_added_orders_them(conversation, monkeypatch):
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")
    skill, sent = _checkout(monkeypatch, cart)
    cart.seed(conversation)

    async def script(messages):
        ask = _asked(messages)
        results = _tool_results(messages)
        if ask == SMOOTHIES:
            if not results:
                return _call(
                    "display_cart", {"action": "add", "item": _item("sinh-to")}
                )
            return _say("Đã thêm sinh tố.")
        assert ask == PAY
        if not results:
            # The cart the model saw: the coffee alone; the smoothies are
            # being added by the other run.
            context = _runtime_context(messages)
            return _call(
                "skill_checkout",
                {
                    "turn_nonce": context["turn_nonce"],
                    "cart_revision": context["draft_cart"]["revision"],
                },
            )
        raise AssertionError("a finished checkout ends the run")

    rig = _Rig(conversation, script, [cart, skill])
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to"].started)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(PAY)))
        await _eventually(lambda: rig.engine.asked(PAY) >= 1)
        # The checkout waits: nothing is posted while the smoothies are added.
        await asyncio.sleep(0.4)
        seen["posted_early"] = [s for s in sent if s[0] == "POST"]
        cart.holds["sinh-to"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["posted_early"] == []
        orders = [
            body for method, url, body in sent if method == "POST" and url == ORDERS
        ]
        # Coffee (35000) and the smoothie (35000), once.
        assert orders == ["total=70000"]
        assert cart.state(conversation)[0] == ["coffee", "sinh-to"]
        # One checkout, finished: the order and payment steps all ran once.
        checkouts = [
            entry
            for entry in ACTION_LEDGER.entries(conversation)
            if entry.tool == "skill_checkout"
        ]
        assert [entry.status for entry in checkouts] == ["succeeded"]
        assert len([s for s in sent if s[1] == PAYMENT and s[0] == "POST"]) == 1
    finally:
        rig.cleanup()


CAKE = "Thêm cho mình một bánh mì."
CHANGED_REPLY = "Giỏ vừa thay đổi, bạn muốn thanh toán luôn không?"


def _stale_checkout_script(seen, *, change):
    """Smoothies (A), then pay (B) with the cart A has not changed yet, then
    ``change`` (C) while B waits for a stable cart."""

    async def script(messages):
        ask = _asked(messages)
        results = _tool_results(messages)
        if ask == SMOOTHIES:
            if not results:
                return _call(
                    "display_cart", {"action": "add", "item": _item("sinh-to")}
                )
            return _say("Đã thêm sinh tố.")
        if ask == PAY:
            if not results:
                context = _runtime_context(messages)
                return _call(
                    "skill_checkout",
                    {
                        "turn_nonce": context["turn_nonce"],
                        "cart_revision": context["draft_cart"]["revision"],
                    },
                )
            seen["checkout_result"] = results[0]
            return _say(CHANGED_REPLY)
        return await change(messages, ask, results)

    return script


async def _drive_stale_checkout(rig, cart, sent, change_words, seen):
    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to"].started)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(PAY)))
        await _eventually(lambda: rig.engine.asked(PAY) >= 1)
        await asyncio.sleep(0.3)  # B now waits for a stable cart
        await worker.queue_frame(
            _ctx(_user(SMOOTHIES), _bot(), _user(PAY), _bot(), _user(change_words))
        )
        # Both slots are held (A's held write, B's waiting checkout): C waits.
        await asyncio.sleep(0.3)
        seen["change_waited_for_a_slot"] = rig.engine.asked(change_words) == 0
        seen["posted_early"] = [s for s in sent if s[0] == "POST"]
        cart.holds["sinh-to"].release.set()
        await _settled(rig)

    await rig.drive(steps)


@pytest.mark.anyio
async def test_an_add_asked_while_checkout_waits_drops_the_stale_checkout(
    conversation, monkeypatch
):
    """C1 / ruling 16: A ends before C can start; B must not order without C."""
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")
    skill, sent = _checkout(monkeypatch, cart)
    cart.seed(conversation)
    seen = {}

    async def add_cake(messages, ask, results):
        assert ask == CAKE, ask
        if not results:
            return _call("display_cart", {"action": "add", "item": _item("cake")})
        return _say("Đã thêm bánh mì.")

    script = _stale_checkout_script(seen, change=add_cake)
    rig = _Rig(conversation, script, [cart, skill])
    cart.log = rig.log
    try:
        await _drive_stale_checkout(rig, cart, sent, CAKE, seen)
        assert seen["change_waited_for_a_slot"] is True
        assert seen["posted_early"] == []
        # No order with the stale cart, and none at all: the customer is asked.
        assert [s for s in sent if s[0] == "POST"] == []
        assert "cart_changed_during_checkout" in seen["checkout_result"]
        assert cart.state(conversation)[0] == ["coffee", "sinh-to", "cake"]
        assert rig.engine.asked(CAKE) == 2
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_no_smoothies_asked_while_checkout_waits_drops_the_stale_checkout(
    conversation, monkeypatch
):
    """C1 / I4: the correction does not wait for the waiting checkout."""
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")
    skill, sent = _checkout(monkeypatch, cart)
    cart.seed(conversation)
    seen = {}

    async def remove_smoothies(messages, ask, results):
        assert ask == NO_SMOOTHIES, ask
        if not results:
            seen["await_started"] = time.monotonic()
            return _call("await_earlier_requests", {})
        if len(results) == 1:
            seen["await_took"] = time.monotonic() - seen["await_started"]
            assert "earlier_requests_finished" in results[0], results[0]
            lines = _runtime_context(messages)["draft_cart"]["lines"]
            ids = [line["line_id"] for line in lines if line["variant_id"] == "sinh-to"]
            assert ids, "the smoothies were not in the cart after the wait"
            return _call("display_cart", {"action": "remove", "line_ids": ids})
        return _say("Đã bỏ sinh tố.")

    rig = _Rig(
        conversation,
        _stale_checkout_script(seen, change=remove_smoothies),
        [cart, skill, AwaitEarlierRequestsTool()],
    )
    cart.log = rig.log
    try:
        await _drive_stale_checkout(rig, cart, sent, NO_SMOOTHIES, seen)
        assert seen["change_waited_for_a_slot"] is True
        assert seen["await_took"] < 2.0  # not the 15 s mutual wait
        assert [s for s in sent if s[0] == "POST"] == []
        assert "cart_changed_during_checkout" in seen["checkout_result"]
        assert cart.state(conversation)[0] == ["coffee"]
        assert [w[0] for w in cart.writes] == ["add", "remove"]
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_a_run_that_waited_for_a_slot_sees_the_named_request_was_told(
    conversation,
):
    """I5: "Bạn thêm chưa?" waits for the one slot while the smoothies run ends
    and is told; it must not answer "still adding" after "đã thêm"."""
    cart = _Cart([])
    cart.hold_adds_of("sinh-to")
    seen = {}

    async def script(messages):
        ask = _asked(messages)
        results = _tool_results(messages)
        if ask == SMOOTHIES:
            if not results:
                return _call(
                    "display_cart", {"action": "add", "item": _item("sinh-to")}
                )
            return _say("Đã thêm sinh tố.")
        assert ask == DID_YOU
        background = _runtime_context(messages).get("background_actions", [])
        seen["background"] = background
        done = [
            item
            for item in background
            if item.get("request") == SMOOTHIES and item.get("status") == "succeeded"
        ]
        if done:
            return _say("Sinh tố đã có trong giỏ rồi ạ.")
        return _say("Mình đang thêm sinh tố, chưa xong ạ.")

    rig = _Rig(conversation, script, [cart], slots=1)
    cart.log = rig.log

    def reporter_tells(entry):
        # Stands in for the reporter: the smoothies run is told as it ends,
        # before the waiting run gets the slot.
        if entry.kind == "run" and entry.request == SMOOTHIES:
            ACTION_LEDGER.mark_run_reported(conversation, entry.run_id, via="phrased")

    unsubscribe = ACTION_LEDGER.subscribe(conversation, reporter_tells)

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(SMOOTHIES)))
        await _wait_set(cart.holds["sinh-to"].started)
        await worker.queue_frame(_ctx(_user(SMOOTHIES), _bot(), _user(DID_YOU)))
        await asyncio.sleep(0.2)
        seen["waited_for_the_slot"] = rig.engine.asked(DID_YOU) == 0
        cart.holds["sinh-to"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["waited_for_the_slot"] is True
        assert rig.sink.spoke("Sinh tố đã có trong giỏ rồi ạ.")
        assert not rig.sink.spoke("chưa xong")
        [told] = [i for i in seen["background"] if i.get("request") == SMOOTHIES]
        assert told["already_told"] == "true"
        assert cart.writes == [("add", ("sinh-to",))]
    finally:
        unsubscribe()
        rig.cleanup()


@pytest.mark.anyio
async def test_alo_is_answered_before_the_first_run_finishes(conversation):
    log: list[str] = []
    cart = _Cart(log)
    cart.hold_adds_of("juice")

    async def script(messages):
        if _tool_results(messages):
            return _say("Đã thêm nước ép.")
        return _call("display_cart", {"action": "add", "item": _item("juice")})

    rig = _Rig(conversation, script, [cart])
    cart.log = rig.log
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(JUICE)))
        await _wait_set(cart.holds["juice"].started)
        await _barge_in(worker)
        await worker.queue_frame(_ctx(_user(JUICE), _bot(), _user(ALO)))
        await _eventually(lambda: rig.sink.spoke(HEARD))
        seen["first_run_done"] = bool(cart.writes)
        cart.holds["juice"].release.set()
        await _settled(rig)

    try:
        await rig.drive(steps)
        # The answer to "Alo" came while the first run's write was still held:
        # the shape behind spec §6's 1.0 s target (wall clock is checked live).
        assert seen["first_run_done"] is False
        assert rig.log.index(f"said:{HEARD}") < rig.log.index("add:juice")
    finally:
        rig.cleanup()


@pytest.mark.anyio
async def test_two_fenced_draining_runs_release_their_slots_for_a_third(conversation):
    """The real two-slot runtime: runs that drain unheard free their slots."""
    first, second, third = "Một cà phê.", "Hai trà đào.", "Ba bánh."

    done: list[str] = []

    async def script(messages):
        # Reached only after the request's hold: a cancelled run never gets here.
        done.append(_asked(messages))
        return _say(f"Xong: {_asked(messages)}")

    rig = _Rig(conversation, script, [], plan=lambda _u: ("delegate", ""))
    for request in (first, second):
        rig.engine.hold(request)
    seen = {}

    async def steps(worker):
        await worker.queue_frame(_ctx(_user(first)))
        await _eventually(lambda: rig.engine.asked(first) == 1)
        await worker.queue_frame(_ctx(_user(first), _bot(), _user(second)))
        await _eventually(lambda: rig.engine.asked(second) == 1)
        await worker.queue_frame(
            _ctx(_user(first), _bot(), _user(second), _bot(), _user(third))
        )
        # Both slots are taken by runs that cannot finish: the third waits.
        await asyncio.sleep(0.3)
        seen["third_waited"] = rig.engine.asked(third) == 0
        seen["free_slots_full"] = rig.runtime._slots._value
        # A barge-in fences all three; the first two keep working unheard.
        await _barge_in(worker)
        rig.engine.holds[first].set()
        rig.engine.holds[second].set()
        await _eventually(lambda: rig.engine.asked(third) >= 1)
        await _settled(rig)

    try:
        await rig.drive(steps)
        assert seen["third_waited"] is True
        assert seen["free_slots_full"] == 0
        # Every run completed its model round after the hold, the fenced ones
        # included (drained, not cancelled), and every slot is back.
        assert sorted(done) == sorted([first, second, third])
        assert rig.runtime._slots._value == 2
        runs = {
            entry.request: entry
            for entry in ACTION_LEDGER.entries(conversation)
            if entry.kind == "run"
        }
        for request in (first, second):
            assert runs[request].status == "succeeded"
            assert not runs[request].reported
        assert not rig.sink.spoke(f"Xong: {first}")
        assert not rig.sink.spoke(f"Xong: {second}")
    finally:
        rig.cleanup()
