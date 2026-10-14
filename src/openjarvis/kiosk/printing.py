"""Durable official-invoice printing, independent of voice and browser lifetime."""

from __future__ import annotations

import hashlib
import logging
import queue
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from openjarvis.core.conversation import current_conversation_id
from openjarvis.core.events import Event, EventBus, EventType
from openjarvis.kiosk.cups import CupsPrinter

logger = logging.getLogger(__name__)


class InvoiceIdentityError(ValueError):
    """An official response does not belong to the tracked checkout."""


class InvoiceAutoPrinter:
    def __init__(
        self,
        checkout,
        bus: EventBus,
        db_path: Path,
        *,
        printer=None,
        client: httpx.Client | None = None,
        poll_seconds: float = 2,
    ) -> None:
        origin = urlsplit(checkout.order_url)
        export = urlsplit(checkout.invoice_url)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or export.username
            or export.password
            or (origin.scheme, origin.netloc) != (export.scheme, export.netloc)
            or "{order_id}" not in checkout.order_url
            or export.fragment
            or export.query
        ):
            raise ValueError("unsafe_invoice_merchant_urls")
        self.checkout = checkout
        self.bus = bus
        self.db_path = Path(db_path)
        self.printer = printer or CupsPrinter(
            checkout.print_queue, checkout.print_width_dots
        )
        self._merchant = f"{origin.scheme}://{origin.netloc}"
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=5, follow_redirects=False)
        self._poll_seconds = poll_seconds
        self._pending: queue.SimpleQueue[tuple[str, str, str, str]] = (
            queue.SimpleQueue()
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._initialized = False
        bus.subscribe(EventType.DISPLAY_UPDATE, self._observe)

    @staticmethod
    def _valid_id(value) -> bool:
        return isinstance(value, str) and bool(
            re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value)
        )

    def _observe(self, event: Event) -> None:
        """Called synchronously by the bus: identifiers only, never I/O or locks."""
        data = event.data
        if self._stop.is_set() or data.get("view") != "payment_qr":
            return
        order, payment = data.get("order_id"), data.get("payment_slug")
        if self._valid_id(order) and self._valid_id(payment):
            self._pending.put(
                (
                    order,
                    payment,
                    current_conversation_id(),
                    str(data.get("presentation_session_id", "")),
                )
            )
            self._wake.set()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name="kiosk-invoice-printer", daemon=True
            )
            self._thread.start()

    @property
    def running(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop.is_set()
        )

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self.bus.unsubscribe(EventType.DISPLAY_UPDATE, self._observe)
        if self._thread is not None:
            self._thread.join(timeout=30)
        elif self._owns_client:
            self._client.close()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    self.process_once()
                except Exception:
                    # Exception text can contain merchant/customer data.
                    logger.warning("auto_print_worker_error")
                self._wake.wait(max(0.05, self._poll_seconds))
        finally:
            try:
                self.process_once()  # Persist queued IDs; stop prevents HTTP/printing.
            except Exception:
                logger.warning("auto_print_shutdown_journal_error")
            if self._owns_client:
                self._client.close()

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, isolation_level=None, timeout=3)
        db.row_factory = sqlite3.Row
        if not self._initialized:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS print_jobs ("
                "merchant TEXT NOT NULL, order_id TEXT NOT NULL, "
                "payment_id TEXT NOT NULL, invoice_id TEXT, "
                "conversation_id TEXT NOT NULL DEFAULT '', "
                "presentation_session_id TEXT NOT NULL DEFAULT '', "
                "completion_published INTEGER NOT NULL DEFAULT 0, "
                "state TEXT NOT NULL DEFAULT 'waiting', job_id TEXT, "
                "title TEXT NOT NULL, pdf_sha256 TEXT, "
                "error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0, "
                "created_at REAL NOT NULL, next_check REAL NOT NULL DEFAULT 0, "
                "PRIMARY KEY (merchant, order_id), UNIQUE (merchant, invoice_id))"
            )
            for name, definition in (
                ("conversation_id", "TEXT NOT NULL DEFAULT ''"),
                ("presentation_session_id", "TEXT NOT NULL DEFAULT ''"),
                ("completion_published", "INTEGER NOT NULL DEFAULT 0"),
            ):
                columns = {r[1] for r in db.execute("PRAGMA table_info(print_jobs)")}
                if name not in columns:
                    try:
                        db.execute(
                            f"ALTER TABLE print_jobs ADD COLUMN {name} {definition}"
                        )
                    except sqlite3.OperationalError:
                        if name not in {
                            r[1] for r in db.execute("PRAGMA table_info(print_jobs)")
                        }:
                            raise
            # Either operation could have reached CUPS before the process died.
            db.execute(
                "UPDATE print_jobs SET state='needs_review', error='restart_uncertain' "
                "WHERE merchant=? AND state IN ('submitting','releasing')",
                (self._merchant,),
            )
            self._initialized = True
        return db

    def process_once(self) -> None:
        """One background pass; all network, SQLite and printer work happens here."""
        db = self._connect()
        try:
            while not self._pending.empty():
                order, payment, conversation, session = self._pending.get_nowait()
                title = (
                    "openjarvis-invoice-"
                    + hashlib.sha256(f"{self._merchant}/{order}".encode()).hexdigest()[
                        :24
                    ]
                )
                db.execute(
                    "INSERT OR IGNORE INTO print_jobs "
                    "(merchant,order_id,payment_id,title,created_at,"
                    "conversation_id,presentation_session_id) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        self._merchant,
                        order,
                        payment,
                        title,
                        time.time(),
                        conversation,
                        session,
                    ),
                )
            rows = db.execute(
                "SELECT * FROM print_jobs WHERE merchant=? "
                "AND ((state IN ('waiting','held','submitted','processing') "
                "AND next_check<=?) "
                "OR (state='completed' AND completion_published=0)) "
                "ORDER BY created_at DESC",
                (self._merchant, time.time()),
            ).fetchall()
            # ponytail: one worker per kiosk; parallelize reads only if queue latency
            # is measured to matter. CUPS already serializes the physical printer.
            for row in rows:
                if self._stop.is_set():
                    break
                if row["state"] == "completed":
                    self._publish_completion(db, row)
                elif row["state"] in {"submitted", "processing"}:
                    self._check_completion(db, row)
                else:
                    self._process(db, row)
        finally:
            db.close()

    def _update(self, db, row, state: str, *, error: str = "") -> None:
        db.execute(
            "UPDATE print_jobs SET state=?,error=? WHERE merchant=? AND order_id=?",
            (state, error, self._merchant, row["order_id"]),
        )
        if state == "submitted":
            current = db.execute(
                "SELECT * FROM print_jobs WHERE merchant=? AND order_id=?",
                (self._merchant, row["order_id"]),
            ).fetchone()
            self._emit_status(current, "submitted")
        elif state == "needs_review":
            self._emit_status(row, "unknown")

    def _emit_status(self, row, state: str) -> None:
        self.bus.publish(
            EventType.PRINT_COMPLETED
            if state == "completed"
            else EventType.PRINT_STATUS_CHANGED,
            {
                "event": "PRINT_COMPLETED"
                if state == "completed"
                else "PRINT_STATUS_CHANGED",
                "merchant": self._merchant,
                "order_id": row["order_id"],
                "invoice_id": row["invoice_id"],
                "job_id": row["job_id"],
                "printing_state": state,
                "conversation_id": row["conversation_id"],
                "presentation_session_id": row["presentation_session_id"],
                **(
                    {"verification": "cups_job_completed_successfully"}
                    if state == "completed"
                    else {}
                ),
            },
        )

    def _publish_completion(self, db, row) -> None:
        # Retry delivery after subscriber failure; runtime dedupes the stable
        # job event ID. Claiming before publish could silently lose the event.
        self._emit_status(row, "completed")
        db.execute(
            "UPDATE print_jobs SET completion_published=1 "
            "WHERE merchant=? AND order_id=? AND state='completed'",
            (self._merchant, row["order_id"]),
        )

    def _check_completion(self, db, row) -> None:
        try:
            observed = self.printer.job_state(row["job_id"], row["title"])
        except Exception as exc:
            db.execute(
                "UPDATE print_jobs SET error=?,next_check=? "
                "WHERE merchant=? AND order_id=? "
                "AND state IN ('submitted','processing')",
                (
                    type(exc).__name__,
                    time.time() + max(self._poll_seconds, 2),
                    self._merchant,
                    row["order_id"],
                ),
            )
            self._emit_status(row, "unknown")
            return
        state = {
            "completed": "completed",
            "processing": "processing",
            "failed": "failed",
        }.get(observed, "submitted")
        claimed = db.execute(
            "UPDATE print_jobs SET state=?,error='',next_check=? "
            "WHERE merchant=? AND order_id=? "
            "AND state IN ('submitted','processing')",
            (state, time.time() + self._poll_seconds, self._merchant, row["order_id"]),
        ).rowcount
        if not claimed:
            return
        if state == "completed":
            self._publish_completion(db, row)
        else:
            self._emit_status(row, "unknown" if observed == "unknown" else state)

    def _process(self, db, row) -> None:
        try:
            response = self._client.get(
                self.checkout.order_url.replace(
                    "{order_id}", quote(row["order_id"], safe="")
                ),
                headers={"Accept": "application/json"},
                timeout=5,
                follow_redirects=False,
            )
            response.raise_for_status()
            envelope = response.json()
            if not 200 <= envelope["statusCode"] < 300:
                raise ValueError("merchant_read_rejected")
            order = envelope["result"]
            if not isinstance(order, dict) or order.get("slug") != row["order_id"]:
                raise InvoiceIdentityError("order_mismatch")
            payment = order.get("payment") or {}
            if (
                not isinstance(payment, dict)
                or payment.get("slug") != row["payment_id"]
            ):
                raise InvoiceIdentityError("payment_mismatch")
            invoice = order.get("invoice")
            if (
                order.get("status") != "paid"
                or payment.get("statusCode") != "completed"
                or payment.get("statusMessage") != "completed"
                or invoice is None
            ):
                db.execute(
                    "UPDATE print_jobs SET next_check=?,error='' "
                    "WHERE merchant=? AND order_id=? AND state=?",
                    (
                        time.time() + self._poll_seconds,
                        self._merchant,
                        row["order_id"],
                        row["state"],
                    ),
                )
                return
            if not isinstance(invoice, dict) or not self._valid_id(invoice.get("slug")):
                raise InvoiceIdentityError("invoice_identity_missing")
            invoice_id = invoice["slug"]
            if row["state"] == "waiting":
                with tempfile.TemporaryDirectory(prefix="openjarvis-invoice-") as path:
                    directory = Path(path)
                    pdf = self._download(row["order_id"], directory)
                    payload = self.printer.prepare(pdf, directory)
                    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
                    if self._stop.is_set():
                        return
                    claimed = db.execute(
                        "UPDATE print_jobs SET state='submitting',invoice_id=?, "
                        "pdf_sha256=?,error='' WHERE merchant=? AND order_id=? "
                        "AND state='waiting'",
                        (invoice_id, digest, self._merchant, row["order_id"]),
                    ).rowcount
                    if not claimed:
                        return
                    # From this point, any exception is an ambiguous side effect.
                    try:
                        job_id = self.printer.hold(payload, row["title"])
                        db.execute(
                            "UPDATE print_jobs SET state='held',job_id=? "
                            "WHERE merchant=? AND order_id=?",
                            (job_id, self._merchant, row["order_id"]),
                        )
                    except Exception:
                        self._update(db, row, "needs_review", error="spool_uncertain")
                        logger.warning(
                            "auto_print_spool_uncertain order=%s", row["order_id"]
                        )
                        return
            else:
                if row["invoice_id"] != invoice_id or not row["job_id"]:
                    raise InvoiceIdentityError("held_invoice_mismatch")
                job_id = row["job_id"]
            if self._stop.is_set():
                return  # Held and durable; the next backend can resume this job.
            try:
                self.printer.verify_held(job_id, row["title"])
            except Exception:
                db.execute(
                    "UPDATE print_jobs SET state='needs_review',"
                    "error='cups_job_unverified' "
                    "WHERE merchant=? AND order_id=? AND state='held'",
                    (self._merchant, row["order_id"]),
                )
                logger.warning("auto_print_job_unverified order=%s", row["order_id"])
                return
            claimed = db.execute(
                "UPDATE print_jobs SET state='releasing' "
                "WHERE merchant=? AND order_id=? AND state='held'",
                (self._merchant, row["order_id"]),
            ).rowcount
            if not claimed:
                return
            try:
                self.printer.release(job_id)
                self._update(db, row, "submitted")
                logger.info(
                    "auto_print_submitted order=%s job=%s", row["order_id"], job_id
                )
            except Exception:
                self._update(db, row, "needs_review", error="release_uncertain")
                logger.warning("auto_print_release_uncertain order=%s", row["order_id"])
        except (InvoiceIdentityError, sqlite3.IntegrityError) as error:
            code = (
                str(error)
                if isinstance(error, InvoiceIdentityError)
                else "invoice_conflict"
            )
            db.execute(
                "UPDATE print_jobs SET state='needs_review',error=? "
                "WHERE merchant=? AND order_id=? AND state=?",
                (code, self._merchant, row["order_id"], row["state"]),
            )
            logger.warning(
                "auto_print_identity_error order=%s code=%s", row["order_id"], code
            )
        except Exception as error:
            attempts = row["attempts"] + 1
            state = "needs_review" if attempts >= 5 else row["state"]
            db.execute(
                "UPDATE print_jobs SET state=?,error=?,attempts=?,next_check=? "
                "WHERE merchant=? AND order_id=? AND state=?",
                (
                    state,
                    type(error).__name__,
                    attempts,
                    time.time() + min(30, 2**attempts),
                    self._merchant,
                    row["order_id"],
                    row["state"],
                ),
            )
            logger.warning("auto_print_preflight_failed order=%s", row["order_id"])

    def _download(self, order_id: str, directory: Path) -> Path:
        pdf = directory / "merchant.pdf"
        with self._client.stream(
            "POST",
            self.checkout.invoice_url,
            json={"order": order_id},
            headers={"Accept": "application/pdf"},
            timeout=10,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            if (
                response.headers.get("content-type", "").split(";")[0]
                != "application/pdf"
            ):
                raise ValueError("invoice_pdf_required")
            size = 0
            with pdf.open("wb") as target:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5 * 1024 * 1024:
                        raise ValueError("invoice_pdf_too_large")
                    target.write(chunk)
        with pdf.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ValueError("invoice_pdf_invalid")
        return pdf

    def jobs(self) -> list[dict]:
        """Operator diagnostics: identifiers/states only, never invoice contents."""
        if not self._initialized or not self.db_path.exists():
            return []
        db = sqlite3.connect(
            self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1
        )
        db.row_factory = sqlite3.Row
        try:
            return [
                {
                    key: row[key]
                    for key in row.keys()
                    if key not in {"conversation_id", "presentation_session_id"}
                }
                for row in db.execute(
                    "SELECT * FROM print_jobs WHERE merchant=? "
                    "ORDER BY created_at DESC LIMIT 50",
                    (self._merchant,),
                )
            ]
        finally:
            db.close()


__all__ = ["InvoiceAutoPrinter"]
