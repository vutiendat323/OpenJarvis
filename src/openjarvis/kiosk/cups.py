"""Print the merchant's PDF on a local ESC/POS CUPS queue, without redraws."""

from __future__ import annotations

import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


class CupsPrinter:
    def __init__(self, queue: str, width_dots: int = 576, *, run=None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,127}", queue):
            raise ValueError("invalid_print_queue")
        if type(width_dots) is not int or not 8 <= width_dots <= 2048:
            raise ValueError("invalid_print_width")
        self.queue = queue
        self.width_dots = width_dots
        self._run = run or subprocess.run

    def _command(self, args: list[str]):
        return self._run(
            args,
            check=True,
            capture_output=True,
            timeout=15,
            env={**os.environ, "LC_ALL": "C"},
        )

    def prepare(self, pdf: Path, directory: Path) -> Path:
        """Rasterize every page of the official document, preserving its content."""
        prefix = directory / "raster"
        self._command(
            [
                "pdftoppm",
                "-mono",
                "-r",
                "203",
                "-scale-to-x",
                str(self.width_dots),
                "-scale-to-y",
                "-1",
                str(pdf),
                str(prefix),
            ]
        )
        pages = sorted(directory.glob("raster-*.pbm"))
        if not pages:
            raise ValueError("invoice_raster_missing")
        payload = directory / "invoice.escpos"
        with payload.open("wb") as target:
            target.write(b"\x1b@")
            for page in pages:
                with page.open("rb") as source:
                    if source.readline().strip() != b"P4":
                        raise ValueError("invalid_invoice_raster")
                    dimensions = source.readline()
                    while dimensions.startswith(b"#"):
                        dimensions = source.readline()
                    width, height = map(int, dimensions.split())
                    if width != self.width_dots or not 0 < height <= 65535:
                        raise ValueError("invalid_invoice_raster_dimensions")
                    stride = (width + 7) // 8
                    bitmap = source.read()
                    if len(bitmap) != stride * height:
                        raise ValueError("truncated_invoice_raster")
                # Trim only the blank PDF tail, through the final black pixel's row.
                ink_bytes = len(bitmap.rstrip(b"\x00"))
                height = max(1, (ink_bytes + stride - 1) // stride)
                # Small strips fit thermal printer buffers; PBM already uses 1=black.
                for row in range(0, height, 256):
                    rows = min(256, height - row)
                    target.write(b"\x1dv0\x00" + struct.pack("<HH", stride, rows))
                    target.write(bitmap[row * stride : (row + rows) * stride])
            target.write(b"\n\n\n\x1dV\x00")
        return payload

    def hold(self, payload: Path, title: str) -> str:
        """Spool one held job. A missing acknowledgment may still have spooled it."""
        result = self._command(
            [
                "lp",
                "-h",
                "localhost:631",
                "-d",
                self.queue,
                "-n",
                "1",
                "-o",
                "raw",
                "-H",
                "hold",
                "-t",
                title,
                "--",
                str(payload),
            ]
        )
        output = result.stdout.decode("utf-8", errors="replace")
        match = re.search(rf"request id is ({re.escape(self.queue)}-\d+)\b", output)
        if match is None:
            raise ValueError("cups_acknowledgment_missing")
        return match.group(1)

    def release(self, job_id: str) -> None:
        """Release that same held job; never restart or submit a second copy."""
        if not re.fullmatch(rf"{re.escape(self.queue)}-\d+", job_id):
            raise ValueError("invalid_cups_job_id")
        self._command(["lp", "-h", "localhost:631", "-i", job_id, "-H", "resume"])

    def verify_held(self, job_id: str, title: str) -> None:
        """Reject missing/reused IDs before releasing a persisted invoice job."""
        values = self._job_attributes(job_id, title)
        if values["job-state"] != "pending-held":
            raise ValueError("cups_job_identity_mismatch")

    def job_state(self, job_id: str, title: str) -> str:
        """Read one identified job; completion requires explicit CUPS success."""
        values = self._job_attributes(job_id, title)
        state = values["job-state"]
        if state in {"canceled", "cancelled", "aborted"}:
            return "failed"
        if state == "completed":
            reasons = set(re.split(r"[,\s]+", values["job-state-reasons"]))
            if any(
                reason.endswith(("-error", "-errors", "-warning", "-warnings"))
                for reason in reasons
            ):
                return "failed"
            if "queued-in-device" not in reasons and reasons & {
                "job-completed-successfully",
                "completed-successfully",
            }:
                return "completed"
            return "unknown"
        if state in {"pending", "pending-held", "processing", "processing-stopped"}:
            return state
        return "unknown"

    def _job_attributes(self, job_id: str, title: str) -> dict[str, str]:
        if not re.fullmatch(rf"{re.escape(self.queue)}-\d+", job_id):
            raise ValueError("cups_job_identity_mismatch")
        number = job_id.rsplit("-", 1)[1]
        with tempfile.TemporaryDirectory(prefix="openjarvis-cups-info-") as path:
            test = Path(path) / "job-info.test"
            test.write_text(
                "{\nOPERATION Get-Job-Attributes\nGROUP operation\n"
                "ATTR charset attributes-charset utf-8\n"
                "ATTR language attributes-natural-language en\n"
                "ATTR uri job-uri $uri\n"
                "ATTR name requesting-user-name $user\n"
                "ATTR keyword requested-attributes "
                "job-id,job-name,job-state,job-printer-uri,job-state-reasons\n"
                "STATUS successful-ok\nEXPECT job-id\nEXPECT job-name\n"
                "EXPECT job-state\nEXPECT job-printer-uri\n}\n"
            )
            result = self._command(
                [
                    "ipptool",
                    "-tv",
                    "-T",
                    "5",
                    f"ipp://localhost:631/jobs/{number}",
                    str(test),
                ]
            )
        output = result.stdout.decode("utf-8", errors="replace")
        values = {}
        for field in (
            "job-id",
            "job-name",
            "job-state",
            "job-printer-uri",
            "job-state-reasons",
        ):
            match = re.search(rf"^\s*{field} \([^)]*\) = (.*)$", output, re.MULTILINE)
            values[field] = match.group(1).strip() if match else ""
        if (
            values["job-id"] != number
            or values["job-name"] != title
            or not values["job-state"]
            or urlsplit(values["job-printer-uri"]).path != f"/printers/{self.queue}"
        ):
            raise ValueError("cups_job_identity_mismatch")
        return values


__all__ = ["CupsPrinter"]
