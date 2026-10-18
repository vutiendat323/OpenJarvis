"""Receipt completion through worker -> bus -> runtime -> voice and display.

Merchant, CUPS, browser and model boundaries are fixtures; native primitives
and the Pipecat event turn are real. No physical printing or payment occurs.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

pytest.importorskip("pipecat")

from pipecat.frames.frames import EndFrame, LLMTextFrame, StartFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.workers.runner import WorkerRunner

from openjarvis.agents.action_ledger import ACTION_LEDGER
from openjarvis.agents.runtime import NativeAgentRuntime
from openjarvis.core.conversation import conversation_scope
from openjarvis.core.events import EventType
from openjarvis.kiosk.presentation import PresentationSessionManager, TouchCheckout
from openjarvis.server.app import _setup_invoice_printing
from openjarvis.server.voice.background import BackgroundReporter
from openjarvis.server.voice.llm import OpenJarvisLLMService
from tests.agents.test_agent_runtime import GeneratingAgent
from tests.kiosk.test_presentation import _FakeMCPClient

pytest_plugins = ("tests.kiosk.test_printing",)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reply",
    [
        "Hóa đơn của bạn đã in xong, bạn nhận tại quầy nhé.",
        "Your receipt has finished printing; please collect it at the counter.",
    ],
)
async def test_print_completion_reaches_agent_and_order_status(
    harness, monkeypatch, reply
):
    invoice, merchant, printer, bus, _, _ = harness
    conversation = uuid.uuid4().hex
    model_inputs = []
    models = []

    class Model:
        def generate(self, messages, **kwargs):
            model_inputs.extend(m.text for m in messages)
            models.append(kwargs["model"])
            return {"content": reply}

    runtime = NativeAgentRuntime(GeneratingAgent(Model(), "fixture"))
    presentation = PresentationSessionManager(
        bus, _FakeMCPClient(server_name="playwright")
    )
    session = presentation.ensure("http://127.0.0.1:5173")
    presentation.configure_touch_checkout(
        TouchCheckout(
            run=lambda **_: None,
            tables_url="https://merchant.example/tables",
            order_url="https://merchant.example/orders/{order_id}",
            invoice_url="https://merchant.example/invoice/export/public",
            print_queue="GP-C180I",
            fetch_json=lambda _: {"result": merchant.order},
        )
    )
    app = FastAPI()
    app.state.presentation_session_manager = presentation
    app.state.native_agent_runtime = runtime
    monkeypatch.setattr(
        "openjarvis.kiosk.printing.InvoiceAutoPrinter", lambda *a, **kw: invoice
    )
    _setup_invoice_printing(app, bus)
    binding = runtime.bind(model="fixture")
    service = OpenJarvisLLMService(
        binding, responder=SimpleNamespace(model="fast-fixture")
    )
    # Stands in for a customer run that is speaking (a property of its runs).
    answering = {"now": False}
    monkeypatch.setattr(
        OpenJarvisLLMService, "_answering", property(lambda _: answering["now"])
    )
    ready, spoken = asyncio.Event(), asyncio.Event()
    texts = []

    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, StartFrame):
                ready.set()
            if isinstance(frame, LLMTextFrame):
                texts.append(frame.text)
                spoken.set()
            await self.push_frame(frame, direction)

    pipeline = PipelineWorker(
        Pipeline([service, Sink()]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)

    async def unused_speak(text):
        pytest.fail("Completion must use model event turn, not hardcoded speech")

    async def flush():
        await reporter.on_idle()

    reporter = BackgroundReporter(
        ACTION_LEDGER,
        conversation,
        speak=unused_speak,
        flush=flush,
        run_event_turn=service.run_event_turn,
    )

    async def drive():
        await asyncio.wait_for(ready.wait(), 2)
        with conversation_scope(conversation):
            presentation.publish(
                {
                    "view": "payment_qr",
                    "order_id": "order-1",
                    "payment_slug": "payment-1",
                    "status": "pending",
                    "lines": [],
                    "total": 35000,
                }
            )
        merchant.order["status"] = "pending"
        invoice.process_once()
        assert printer.held == []
        merchant.order["status"] = "paid"
        presentation.check_payment(session.session_id)
        # The existing payment notice is a distinct earlier outcome.
        for entry in ACTION_LEDGER.entries(conversation):
            ACTION_LEDGER.mark_reported(conversation, entry.key, via="payment-notice")
        with conversation_scope(conversation):
            reporter.start()
        try:
            invoice.process_once()
            assert (
                presentation.replay(session.session_id)["printing_state"] == "submitted"
            )
            assert not ACTION_LEDGER.unreported_finished(conversation)
            answering["now"] = True
            printer.cups_state = "completed"

            def fail_after_runtime_delivery(event):
                raise RuntimeError("later subscriber failure")

            bus.subscribe(EventType.PRINT_COMPLETED, fail_after_runtime_delivery)
            with pytest.raises(RuntimeError, match="later subscriber failure"):
                invoice.process_once()
            bus.unsubscribe(EventType.PRINT_COMPLETED, fail_after_runtime_delivery)
            invoice.process_once()  # Durable retry must not duplicate Agent outcomes.
            assert len(ACTION_LEDGER.unreported_finished(conversation)) == 1
            await asyncio.sleep(0)
            await reporter.on_idle()
            if reporter._event_task is not None:
                await reporter._event_task
            assert texts == []  # Never interrupt the active customer turn.
            assert ACTION_LEDGER.unreported_finished(conversation)
            answering["now"] = False
            await reporter.on_idle()
            await asyncio.wait_for(spoken.wait(), 2)
            if reporter._event_task is not None:
                await reporter._event_task
            invoice.process_once()
            await reporter.on_idle()
            assert not ACTION_LEDGER.unreported_finished(conversation)
            payload = presentation.replay(session.session_id)
            assert payload["status"] == "paid"
            assert payload["printing_state"] == "completed"
            assert payload["view"] == "bill"
            assert len(printer.held) == len(printer.released) == 1
        finally:
            reporter.stop()
            await pipeline.queue_frame(EndFrame())

    await runner.add_workers(pipeline)
    await asyncio.wait_for(asyncio.gather(runner.run(), drive()), 10)
    assert "".join(texts) == reply
    assert models == ["fast-fixture"]
    assert binding.model == "fixture"
    assert any("PRINT_COMPLETED" in text for text in model_inputs)
    assert any("cups_job_completed_successfully" in text for text in model_inputs)
    assert not ACTION_LEDGER.entries("another-customer")
