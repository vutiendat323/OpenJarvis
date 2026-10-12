"""Protect the raw-printer boundary; no test sends a physical print job."""

from __future__ import annotations

import subprocess

import pytest


def test_prepare_keeps_all_pdf_pages_as_native_raster(tmp_path):
    from openjarvis.kiosk.cups import CupsPrinter

    pdf = tmp_path / "merchant.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture")

    def run(args, **kwargs):
        # Poppler is the external boundary: these are its binary PBM outputs.
        (tmp_path / "raster-1.pbm").write_bytes(b"P4\n8 2\n\x80\x01")
        (tmp_path / "raster-2.pbm").write_bytes(b"P4\n8 1\n\x55")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    printer = CupsPrinter("GP-C180I", width_dots=8, run=run)
    payload = printer.prepare(pdf, tmp_path)

    assert payload.read_bytes() == (
        b"\x1b@"
        b"\x1dv0\x00\x01\x00\x02\x00\x80\x01"
        b"\x1dv0\x00\x01\x00\x01\x00\x55"
        b"\n\n\n\x1dV\x00"
    )


@pytest.mark.parametrize(
    "bitmap,height,expected_raster",
    [
        (
            b"\x00\x80\x00\x01" + b"\x00" * 600,
            604,
            b"\x1dv0\x00\x01\x00\x04\x00\x00\x80\x00\x01",
        ),
        (
            b"\x00" * 600,
            600,
            b"\x1dv0\x00\x01\x00\x01\x00\x00",
        ),
        (
            b"\x80" * 300 + b"\x00" * 600,
            900,
            b"\x1dv0\x00\x01\x00\x00\x01"
            + b"\x80" * 256
            + b"\x1dv0\x00\x01\x00\x2c\x00"
            + b"\x80" * 44,
        ),
    ],
    ids=["footer-after-internal-whitespace", "blank-page", "multiple-raster-strips"],
)
def test_prepare_drops_only_blank_rows_after_last_ink(
    tmp_path, bitmap, height, expected_raster
):
    from openjarvis.kiosk.cups import CupsPrinter

    pdf = tmp_path / "merchant.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture")

    def run(args, **kwargs):
        (tmp_path / "raster-1.pbm").write_bytes(f"P4\n8 {height}\n".encode() + bitmap)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    payload = CupsPrinter("GP-C180I", width_dots=8, run=run).prepare(pdf, tmp_path)

    assert payload.read_bytes() == b"\x1b@" + expected_raster + b"\n\n\n\x1dV\x00"


def test_truncated_raster_never_reaches_spool(tmp_path):
    from openjarvis.kiosk.cups import CupsPrinter

    pdf = tmp_path / "merchant.pdf"
    pdf.write_bytes(b"%PDF-1.4 fixture")

    def run(args, **kwargs):
        (tmp_path / "raster-1.pbm").write_bytes(b"P4\n8 2\n\x80")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    with pytest.raises(ValueError, match="raster"):
        CupsPrinter("GP-C180I", width_dots=8, run=run).prepare(pdf, tmp_path)


def test_hold_and_release_use_one_named_local_raw_job(tmp_path):
    from openjarvis.kiosk.cups import CupsPrinter

    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 0, b"request id is GP-C180I-17 (1 file(s))\n", b""
        )

    printer = CupsPrinter("GP-C180I", run=run)
    payload = tmp_path / "invoice.escpos"
    payload.write_bytes(b"raster")
    job = printer.hold(payload, "invoice-original-order-1")
    printer.release(job)

    assert job == "GP-C180I-17"
    assert calls[0][0] == [
        "lp",
        "-h",
        "localhost:631",
        "-d",
        "GP-C180I",
        "-n",
        "1",
        "-o",
        "raw",
        "-H",
        "hold",
        "-t",
        "invoice-original-order-1",
        "--",
        str(payload),
    ]
    assert calls[1][0] == [
        "lp",
        "-h",
        "localhost:631",
        "-i",
        "GP-C180I-17",
        "-H",
        "resume",
    ]
    assert calls[0][1]["env"]["LC_ALL"] == "C"
    assert calls[0][1]["timeout"] > 0


@pytest.mark.parametrize("stdout", [b"", b"request id is OTHER-17 (1 file(s))"])
def test_missing_or_wrong_spool_acknowledgment_is_not_success(tmp_path, stdout):
    from openjarvis.kiosk.cups import CupsPrinter

    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout, b"")

    with pytest.raises(ValueError, match="acknowledgment"):
        CupsPrinter("GP-C180I", run=run).hold(tmp_path / "payload", "job")


def test_spool_timeout_is_not_retried(tmp_path):
    from openjarvis.kiosk.cups import CupsPrinter

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, 15)

    with pytest.raises(subprocess.TimeoutExpired):
        CupsPrinter("GP-C180I", run=run).hold(tmp_path / "payload", "job")
    assert len(calls) == 1


@pytest.mark.parametrize("job", ["OTHER-17", "GP-C180I-17 -H restart", "17"])
def test_release_rejects_job_ids_outside_this_queue(job):
    from openjarvis.kiosk.cups import CupsPrinter

    with pytest.raises(ValueError, match="job"):
        CupsPrinter("GP-C180I").release(job)


@pytest.mark.parametrize(
    "title,state,uri",
    [
        ("another-document", "pending-held", "ipp://localhost:631/printers/GP-C180I"),
        ("invoice-order-1", "completed", "ipp://localhost:631/printers/GP-C180I"),
        ("invoice-order-1", "pending-held", "ipp://localhost:631/printers/OTHER"),
    ],
)
def test_held_job_verification_rejects_reused_or_released_job(title, state, uri):
    from openjarvis.kiosk.cups import CupsPrinter

    def run(args, **kwargs):
        output = (
            f"job-id (integer) = 17\njob-name (nameWithoutLanguage) = {title}\n"
            f"job-state (enum) = {state}\njob-printer-uri (uri) = {uri}\n"
        ).encode()
        return subprocess.CompletedProcess(args, 0, output, b"")

    with pytest.raises(ValueError, match="identity"):
        CupsPrinter("GP-C180I", run=run).verify_held("GP-C180I-17", "invoice-order-1")


def test_held_job_verification_accepts_exact_cups_identity():
    from openjarvis.kiosk.cups import CupsPrinter

    def run(args, **kwargs):
        assert args[:3] == ["ipptool", "-tv", "-T"]
        assert args[-2] == "ipp://localhost:631/jobs/17"
        output = (
            b"job-id (integer) = 17\n"
            b"job-name (nameWithoutLanguage) = invoice-order-1\n"
            b"job-state (enum) = pending-held\n"
            b"job-printer-uri (uri) = ipp://localhost:631/printers/GP-C180I\n"
        )
        return subprocess.CompletedProcess(args, 0, output, b"")

    CupsPrinter("GP-C180I", run=run).verify_held("GP-C180I-17", "invoice-order-1")
