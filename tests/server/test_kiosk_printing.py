"""Auto-print lifecycle/configuration without merchant I/O or physical jobs."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.core.events import EventBus
from openjarvis.kiosk.presentation import TouchCheckout


def test_app_owns_print_worker_and_operator_status(tmp_path, monkeypatch):
    from openjarvis.kiosk.routes import router
    from openjarvis.server.app import _setup_invoice_printing

    monkeypatch.setattr("openjarvis.core.config.get_config_dir", lambda: tmp_path)
    checkout = TouchCheckout(
        run=lambda **_: None,
        tables_url="https://merchant.example/tables",
        order_url="https://merchant.example/orders/{order_id}",
        invoice_url="https://merchant.example/invoice/export/public",
        print_queue="GP-C180I",
    )
    app, bus = FastAPI(), EventBus()
    app.state.presentation_session_manager = SimpleNamespace(touch_checkout=checkout)
    _setup_invoice_printing(app, bus)
    app.include_router(router)

    with TestClient(app) as client:
        worker = app.state.invoice_printer
        assert worker.running
        response = client.get("/api/kiosk/print-jobs")
        assert response.status_code == 200
        assert response.json() == {"enabled": True, "jobs": []}
    assert not worker.running
    assert not worker._thread.is_alive()
    assert worker._client.is_closed


def test_queue_override_can_disable_printing_without_disabling_kiosk(monkeypatch):
    from openjarvis.server.app import _setup_invoice_printing

    monkeypatch.setenv("KIOSK_PRINT_QUEUE", "")
    checkout = TouchCheckout(
        run=lambda **_: None,
        tables_url="https://merchant.example/tables",
        order_url="https://merchant.example/orders/{order_id}",
        invoice_url="https://merchant.example/invoice/export/public",
        print_queue="GP-C180I",
    )
    app = FastAPI()
    app.state.presentation_session_manager = SimpleNamespace(touch_checkout=checkout)
    _setup_invoice_printing(app, EventBus())
    assert app.state.invoice_printer is None


def test_checkout_without_invoice_metadata_does_not_enable_printing():
    from openjarvis.server.app import _setup_invoice_printing

    app = FastAPI()
    app.state.presentation_session_manager = SimpleNamespace(
        touch_checkout=TouchCheckout(
            run=lambda **_: None,
            tables_url="https://merchant.example/tables",
            order_url="https://merchant.example/orders/{order_id}",
        )
    )
    _setup_invoice_printing(app, EventBus())
    assert app.state.invoice_printer is None
