"""Official invoice, payment gating, durable dedup and nonblocking display events."""

from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest

from openjarvis.core.events import EventBus, EventType

PDF = b"%PDF-1.4\nmerchant original document\n"


class Merchant:
    def __init__(self):
        self.order = {
            "slug": "order-1",
            "status": "paid",
            "payment": {
                "slug": "payment-1",
                "paymentMethod": "bank-transfer",
                "statusCode": "completed",
                "statusMessage": "completed",
            },
            "invoice": {"slug": "invoice-1", "status": "paid"},
        }
        self.requests = []
        self.pdf = PDF
        self.pdf_type = "application/pdf"
        self.entered = None
        self.resume = None

    def handle(self, request):
        self.requests.append((request.method, str(request.url), request.content))
        if self.entered is not None:
            self.entered.set()
            assert self.resume.wait(3)
        if request.method == "GET":
            return httpx.Response(200, json={"statusCode": 200, "result": self.order})
        assert str(request.url) == "https://merchant.example/invoice/export/public"
        assert json.loads(request.content) == {"order": self.order["slug"]}
        return httpx.Response(
            200, content=self.pdf, headers={"Content-Type": self.pdf_type}
        )


class Printer:
    """Only physical-printer operations are replaced; SQLite/HTTP worker are real."""

    def __init__(self):
        self.documents = []
        self.held = []
        self.released = []
        self.fail_hold = False
        self.fail_release = False
        self.fail_verify = False
        self.cups_state = "pending"

    def prepare(self, pdf, directory):
        self.documents.append(pdf.read_bytes())
        return pdf

    def hold(self, payload, title):
        self.held.append((payload.read_bytes(), title))
        if self.fail_hold:
            raise TimeoutError("spool acknowledgment lost")
        return "GP-C180I-17"

    def release(self, job_id):
        self.released.append(job_id)
        if self.fail_release:
            raise TimeoutError("release acknowledgment lost")

    def job_state(self, job_id, title):
        if isinstance(self.cups_state, Exception):
            raise self.cups_state
        return self.cups_state

    def verify_held(self, job_id, title):
        if self.fail_verify:
            raise ValueError("cups_job_identity_mismatch")


@pytest.fixture
def harness(tmp_path):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    merchant, printer, bus = Merchant(), Printer(), EventBus()
    checkout = SimpleNamespace(
        order_url="https://merchant.example/orders/{order_id}",
        invoice_url="https://merchant.example/invoice/export/public",
        print_queue="GP-C180I",
        print_width_dots=576,
    )
    client = httpx.Client(transport=httpx.MockTransport(merchant.handle))
    worker = InvoiceAutoPrinter(
        checkout,
        bus,
        tmp_path / "printing.db",
        printer=printer,
        client=client,
        poll_seconds=0,
    )
    yield worker, merchant, printer, bus, checkout, client
    merchant.resume and merchant.resume.set()
    worker.stop()
    client.close()


def publish(bus, **changes):
    bus.publish(
        EventType.DISPLAY_UPDATE,
        {
            "view": "payment_qr",
            "order_id": "order-1",
            "payment_slug": "payment-1",
            "status": "pending",
            **changes,
        },
    )


def test_paid_existing_invoice_downloads_original_and_spools_once(harness):
    worker, merchant, printer, bus, _, _ = harness
    publish(bus)
    publish(bus)
    worker.process_once()
    publish(bus)
    worker.process_once()

    assert printer.documents == [PDF]
    assert len(printer.held) == 1
    assert printer.released == ["GP-C180I-17"]
    assert worker.jobs()[0]["state"] == "submitted"
    assert worker.jobs()[0]["invoice_id"] == "invoice-1"
    assert [method for method, _, _ in merchant.requests] == ["GET", "POST"]
    assert [url for _, url, _ in merchant.requests] == [
        "https://merchant.example/orders/order-1",
        "https://merchant.example/invoice/export/public",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "pending"},
        {"invoice": None},
        {
            "payment": {
                "slug": "payment-1",
                "statusCode": "pending",
                "statusMessage": "pending",
            }
        },
        {
            "payment": {
                "slug": "payment-1",
                "statusCode": "completed",
                "statusMessage": "pending",
            }
        },
    ],
)
def test_unconfirmed_or_missing_invoice_never_exports_or_prints(harness, change):
    worker, merchant, printer, bus, _, _ = harness
    merchant.order.update(change)
    publish(bus)
    worker.process_once()
    assert printer.held == []
    assert [method for method, _, _ in merchant.requests] == ["GET"]
    assert worker.jobs()[0]["state"] == "waiting"


@pytest.mark.parametrize(
    "change",
    [
        {"slug": "another-order"},
        {
            "payment": {
                "slug": "another-payment",
                "statusCode": "completed",
                "statusMessage": "completed",
            }
        },
        {"invoice": {"slug": "", "status": "paid"}},
    ],
)
def test_wrong_merchant_identity_requires_review_without_printing(harness, change):
    worker, merchant, printer, bus, _, _ = harness
    merchant.order.update(change)
    publish(bus)
    worker.process_once()
    assert printer.held == []
    assert worker.jobs()[0]["state"] == "needs_review"


def test_invoice_can_arrive_after_paid_and_voice_display_reset(harness):
    worker, merchant, printer, bus, _, _ = harness
    merchant.order["invoice"] = None
    publish(bus)
    worker.process_once()
    bus.publish(EventType.DISPLAY_UPDATE, {"view": "none"})
    merchant.order["invoice"] = {"slug": "invoice-1", "status": "paid"}
    worker.process_once()
    assert printer.released == ["GP-C180I-17"]


@pytest.mark.parametrize("failure", ["fail_hold", "fail_release"])
def test_ambiguous_print_is_persistent_and_never_retried(harness, failure):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, _, printer, bus, checkout, client = harness
    setattr(printer, failure, True)
    publish(bus)
    worker.process_once()
    assert worker.jobs()[0]["state"] == "needs_review"
    replacement = InvoiceAutoPrinter(
        checkout,
        bus,
        worker.db_path,
        printer=printer,
        client=client,
        poll_seconds=0,
    )
    publish(bus)
    replacement.process_once()
    assert len(printer.held) == 1
    assert len(printer.released) == (1 if failure == "fail_release" else 0)
    replacement.stop()


def test_submitted_job_survives_restart_without_second_copy(harness):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, _, printer, bus, checkout, client = harness
    publish(bus)
    worker.process_once()
    replacement = InvoiceAutoPrinter(
        checkout,
        bus,
        worker.db_path,
        printer=printer,
        client=client,
        poll_seconds=0,
    )
    publish(bus)
    replacement.process_once()
    assert len(printer.held) == 1
    assert replacement.jobs()[0]["job_id"] == "GP-C180I-17"
    replacement.stop()


@pytest.mark.parametrize("state", ["submitting", "releasing"])
def test_restart_during_irreversible_step_never_resubmits(harness, state):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, _, printer, bus, checkout, client = harness
    publish(bus)
    merchant = harness[1]
    merchant.order["invoice"] = None
    worker.process_once()
    with sqlite3.connect(worker.db_path) as db:
        db.execute("UPDATE print_jobs SET state=?", (state,))
    replacement = InvoiceAutoPrinter(
        checkout,
        bus,
        worker.db_path,
        printer=printer,
        client=client,
        poll_seconds=0,
    )
    replacement.process_once()
    assert replacement.jobs()[0]["state"] == "needs_review"
    assert printer.held == []
    assert printer.released == []
    replacement.stop()


def test_restart_resumes_held_job_without_new_spool(harness):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, merchant, printer, bus, checkout, client = harness
    publish(bus)
    merchant.order["invoice"] = None
    worker.process_once()
    with sqlite3.connect(worker.db_path) as db:
        db.execute(
            "UPDATE print_jobs SET state='held', job_id='GP-C180I-17', "
            "invoice_id='invoice-1'"
        )
    merchant.order["invoice"] = {"slug": "invoice-1", "status": "paid"}
    replacement = InvoiceAutoPrinter(
        checkout,
        bus,
        worker.db_path,
        printer=printer,
        client=client,
        poll_seconds=0,
    )
    replacement.process_once()
    assert printer.held == []
    assert printer.released == ["GP-C180I-17"]
    replacement.stop()


@pytest.mark.parametrize(
    "pdf,pdf_type",
    [
        (b"<html>merchant error</html>", "application/pdf"),
        (PDF, "text/html"),
    ],
)
def test_invalid_invoice_response_never_reaches_printer(harness, pdf, pdf_type):
    worker, merchant, printer, bus, _, _ = harness
    merchant.pdf, merchant.pdf_type = pdf, pdf_type
    publish(bus)
    worker.process_once()
    assert printer.documents == []
    assert printer.held == []
    assert worker.jobs()[0]["state"] == "waiting"


def test_publisher_stays_responsive_while_merchant_is_blocked(harness):
    worker, merchant, _, bus, _, _ = harness
    merchant.entered, merchant.resume = threading.Event(), threading.Event()
    worker.start()
    publish(bus)
    assert merchant.entered.wait(1)
    returned = threading.Event()

    def another_event():
        publish(bus)
        returned.set()

    publisher = threading.Thread(target=another_event)
    publisher.start()
    try:
        assert returned.wait(1), "Display publication waited on merchant I/O"
    finally:
        merchant.resume.set()
        publisher.join(2)


def test_ui_bill_or_malformed_qr_cannot_trigger_print(harness):
    worker, merchant, printer, bus, _, _ = harness
    publish(bus, view="bill", status="paid")
    publish(bus, order_id="")
    publish(bus, payment_slug=None)
    worker.process_once()
    assert merchant.requests == []
    assert printer.held == []
    assert worker.jobs() == []


def test_stale_preflight_error_cannot_reopen_another_workers_submitted_job(harness):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, merchant, printer, bus, checkout, client = harness
    merchant.order["invoice"] = None
    publish(bus)
    worker.process_once()
    other_printer = Printer()
    other = InvoiceAutoPrinter(
        checkout,
        bus,
        worker.db_path,
        printer=other_printer,
        client=client,
        poll_seconds=0,
    )
    other.process_once()  # Both instances have initialized the same journal.
    merchant.order["invoice"] = {"slug": "invoice-1", "status": "paid"}
    entered, resume = threading.Event(), threading.Event()

    def fail_late(pdf, directory):
        entered.set()
        assert resume.wait(2)
        raise OSError("conversion failed in another worker")

    other_printer.prepare = fail_late
    concurrent = threading.Thread(target=other.process_once)
    concurrent.start()
    assert entered.wait(1)
    try:
        worker.process_once()
        assert printer.released == ["GP-C180I-17"]
    finally:
        resume.set()
        concurrent.join(2)
        other.stop()
    assert worker.jobs()[0]["state"] == "submitted"


def test_foreign_held_job_is_not_released_as_an_invoice(harness):
    worker, _, printer, bus, _, _ = harness
    printer.fail_verify = True
    publish(bus)
    worker.process_once()
    assert printer.released == []
    assert worker.jobs()[0]["state"] == "needs_review"
    assert worker.jobs()[0]["job_id"] == "GP-C180I-17"


def test_stale_identity_error_cannot_overwrite_a_submitted_job(harness):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, merchant, printer, bus, checkout, _ = harness
    merchant.order["invoice"] = None
    publish(bus)
    worker.process_once()
    entered, resume = threading.Event(), threading.Event()
    delayed = [False]

    def handler(request):
        if delayed[0]:
            entered.set()
            assert resume.wait(2)
            return httpx.Response(
                200, json={"statusCode": 200, "result": {"slug": "wrong-order"}}
            )
        return merchant.handle(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        other = InvoiceAutoPrinter(
            checkout,
            bus,
            worker.db_path,
            printer=printer,
            client=client,
            poll_seconds=0,
        )
        other.process_once()
        merchant.order["invoice"] = {"slug": "invoice-1", "status": "paid"}
        delayed[0] = True
        concurrent = threading.Thread(target=other.process_once)
        concurrent.start()
        assert entered.wait(1)
        try:
            worker.process_once()
        finally:
            resume.set()
            concurrent.join(2)
            other.stop()
    assert worker.jobs()[0]["state"] == "submitted"
    assert printer.released == ["GP-C180I-17"]


def test_clean_shutdown_persists_events_queued_during_slow_http(harness):
    worker, merchant, printer, bus, _, _ = harness
    merchant.entered, merchant.resume = threading.Event(), threading.Event()
    worker.start()
    publish(bus)
    assert merchant.entered.wait(1)
    publish(bus, order_id="order-2", payment_slug="payment-2")
    stopper = threading.Thread(target=worker.stop)
    stopper.start()
    assert worker._stop.wait(1)
    merchant.resume.set()
    stopper.join(3)
    assert not stopper.is_alive()
    assert {row["order_id"] for row in worker.jobs()} == {"order-1", "order-2"}
    assert printer.held == []


def test_same_invoice_cannot_be_printed_for_a_second_order(harness):
    worker, merchant, printer, bus, _, _ = harness
    publish(bus)
    worker.process_once()
    merchant.order["slug"] = "order-2"
    merchant.order["payment"]["slug"] = "payment-2"
    publish(bus, order_id="order-2", payment_slug="payment-2")
    worker.process_once()
    assert len(printer.held) == 1
    states = {row["order_id"]: row["state"] for row in worker.jobs()}
    assert states == {"order-1": "submitted", "order-2": "needs_review"}


def test_two_successful_workers_claim_one_original_print(harness):
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, merchant, printer, bus, checkout, client = harness
    merchant.order["invoice"] = None
    publish(bus)
    worker.process_once()
    other_printer = Printer()
    other = InvoiceAutoPrinter(
        checkout,
        bus,
        worker.db_path,
        printer=other_printer,
        client=client,
        poll_seconds=0,
    )
    other.process_once()
    merchant.order["invoice"] = {"slug": "invoice-1", "status": "paid"}
    barrier = threading.Barrier(2)

    def prepare(pdf, directory):
        barrier.wait(timeout=2)
        return pdf

    printer.prepare = other_printer.prepare = prepare
    threads = [threading.Thread(target=w.process_once) for w in (worker, other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive()
    other.stop()
    assert len(printer.held) + len(other_printer.held) == 1
    assert len(printer.released) + len(other_printer.released) == 1
    assert worker.jobs()[0]["state"] == "submitted"


def test_verified_completion_emits_once_without_resubmission(harness):
    from openjarvis.core.conversation import conversation_scope

    worker, _, printer, bus, *_ = harness
    events = []
    bus.subscribe(EventType.PRINT_COMPLETED, events.append)
    with conversation_scope("original-customer"):
        publish(bus, presentation_session_id="original-display")
    worker.process_once()
    assert worker.jobs()[0]["state"] == "submitted"
    assert events == []
    printer.cups_state = "processing"
    worker.process_once()
    assert worker.jobs()[0]["state"] == "processing"
    assert events == []
    printer.cups_state = "completed"
    worker.process_once()
    worker.process_once()
    assert worker.jobs()[0]["state"] == "completed"
    assert len(events) == 1
    assert events[0].data["conversation_id"] == "original-customer"
    assert events[0].data["presentation_session_id"] == "original-display"
    assert events[0].data["job_id"] == "GP-C180I-17"
    assert len(printer.held) == len(printer.released) == 1


@pytest.mark.parametrize(
    "state", ["pending", "unknown", "failed", TimeoutError("CUPS unavailable")]
)
def test_unverified_completion_never_emits_success(harness, state):
    worker, _, printer, bus, *_ = harness
    events = []
    bus.subscribe(EventType.PRINT_COMPLETED, events.append)
    publish(bus)
    worker.process_once()
    printer.cups_state = state
    worker.process_once()
    assert events == []
    assert worker.jobs()[0]["state"] != "completed"
    assert len(printer.held) == len(printer.released) == 1


def test_submitted_job_completion_resumes_after_restart(harness):
    from openjarvis.core.conversation import conversation_scope
    from openjarvis.kiosk.printing import InvoiceAutoPrinter

    worker, _, printer, bus, checkout, client = harness
    events = []
    bus.subscribe(EventType.PRINT_COMPLETED, events.append)
    with conversation_scope("before-restart"):
        publish(bus, presentation_session_id="before-display")
    worker.process_once()
    worker.stop()
    restarted = InvoiceAutoPrinter(
        checkout, bus, worker.db_path, printer=printer, client=client, poll_seconds=0
    )
    try:
        printer.cups_state = "completed"
        restarted.process_once()
        restarted.process_once()
        assert len(events) == 1
        assert events[0].data["conversation_id"] == "before-restart"
        assert restarted.jobs()[0]["state"] == "completed"
        assert "conversation_id" not in restarted.jobs()[0]
        assert "presentation_session_id" not in restarted.jobs()[0]
        assert len(printer.held) == len(printer.released) == 1
    finally:
        restarted.stop()


def test_completion_delivery_retries_without_reprinting(harness):
    worker, _, printer, bus, *_ = harness
    deliveries = []

    def receive(event):
        deliveries.append(event)
        if len(deliveries) == 1:
            raise RuntimeError("temporary subscriber failure")

    bus.subscribe(EventType.PRINT_COMPLETED, receive)
    publish(bus)
    worker.process_once()
    printer.cups_state = "completed"
    with pytest.raises(RuntimeError, match="subscriber failure"):
        worker.process_once()
    assert worker.jobs()[0]["state"] == "completed"
    assert worker.jobs()[0]["completion_published"] == 0
    worker.process_once()
    worker.process_once()
    assert len(deliveries) == 2
    assert worker.jobs()[0]["completion_published"] == 1
    assert len(printer.held) == len(printer.released) == 1
