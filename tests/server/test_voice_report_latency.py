"""Live: the fast model phrases a late result quickly and never claims a cancel.

Spec 2026-10-17 barge-in §5.6: first audio within 1.5 s of the idle moment at
p50. TTS starts about 0.2 s after its text (live 2026-10-17 logs), so the
complete line must arrive within 1.3 s at p50. Costs real API calls: run only
when asked, with the kiosk preset's keys in the environment.
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from openjarvis.agents.action_ledger import LedgerEntry
from openjarvis.server.voice.background import report_text
from openjarvis.server.voice.responder import VoiceResponder, load_responder_settings

PRESET = "configs/openjarvis/examples/ordering-kiosk-mcp.toml"
LINE_P50_SECONDS = 1.3
CLAIMS = ("đã hủy", "hủy rồi", "đã dừng", "cancelled", "canceled")

CASES = [
    (
        "vi",
        "Customer: Cho mình một cà phê sữa mang đi.\nAssistant: Mình xem nhé.\n"
        "Customer: Hủy đơn.\nAssistant: Đơn đang được hoàn tất ạ.",
    ),
    ("vi", "Customer: Một bạc xỉu tại bàn 5, thanh toán luôn.\nCustomer: Dừng lại."),
    ("vi", "Customer: Cho mình hai trà đào.\nCustomer: Thôi khỏi, hủy đi."),
    ("vi", "Customer: Thanh toán giúp mình.\nCustomer: Alo, bạn nghe không?"),
    ("vi", "Customer: Một matcha mang đi.\nCustomer: Đổi ý rồi, hủy đơn nhé."),
    ("en", "Customer: One latte to go, please.\nCustomer: Cancel it."),
    ("en", "Customer: Two espressos at table 5, pay now.\nCustomer: Stop."),
    ("en", "Customer: I'd like a mocha.\nCustomer: Actually, cancel the order."),
    ("en", "Customer: Checkout please.\nCustomer: Can you hear me?"),
    ("en", "Customer: One iced tea to go.\nCustomer: Never mind, cancel."),
]


def _entry(language: str) -> LedgerEntry:
    message = (
        "Đơn đã tạo; vui lòng quét mã QR để thanh toán."
        if language == "vi"
        else "Order created; please scan the QR code to pay."
    )
    return LedgerEntry(
        key="run:call",
        call_id="call",
        tool="skill_trendcoffee-checkout",
        run_id="run",
        status="succeeded",
        customer_message=message,
        interrupted=True,
    )


@pytest.mark.live
@pytest.mark.anyio
async def test_live_phrasing_is_fast_and_never_claims_a_cancel(monkeypatch):
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("DEEPSEEK_API_KEY not set")
    monkeypatch.setenv("OPENJARVIS_CONFIG", PRESET)
    settings = load_responder_settings()
    from openjarvis.engine.cloud import CloudEngine

    responder = VoiceResponder(
        CloudEngine(), settings.model, settings.prompt, settings.report_prompt
    )
    seconds, lines = [], []
    for language, conversation in CASES:
        started = time.monotonic()
        line = await responder.report(report_text(_entry(language)), conversation)
        seconds.append(time.monotonic() - started)
        lines.append(line)

    p50 = statistics.median(seconds)
    print(f"\nphrase p50={p50:.2f}s max={max(seconds):.2f}s")
    for line in lines:
        print(f"  {line}")
    assert all(lines)
    assert not [line for line in lines if any(c in line.lower() for c in CLAIMS)]
    assert p50 <= LINE_P50_SECONDS
