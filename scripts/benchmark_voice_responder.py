#!/usr/bin/env python3
"""Offline eval of the voice Responder (chat-supervisor routing).

Runs the preset's Responder model and prompt with its one tool,
getNextResponseFromSupervisor, on synthetic Vietnamese and English utterances,
and checks the rollout gate of the 2026-10-14 conformance spec:

- 0 business utterances without a tool call (a dropped request);
- 0 delegations without a filler;
- 0 replies in the wrong language;
- repeat and explain requests must delegate (the Agent has the history);
- fillers flagged for agreement words or repeated quantities, for human review.

Synthetic utterances only; never customer data. Uses paid inference: run it
only with the operator's approval. Needs the provider key in the environment
(``uv run --env-file .env``). Prints shapes, timings and model text, never keys.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import statistics
import time
from pathlib import Path

from openjarvis.engine.cloud import CloudEngine
from openjarvis.server.voice.responder import VoiceResponder, load_responder_settings

DEFAULT_CONFIG = "configs/openjarvis/examples/ordering-kiosk-mcp.toml"

H = "Assistant: Chào bạn, bạn muốn dùng gì hôm nay ạ?"
H_EN = "Assistant: Hi, what would you like today?"
H_Q = "Customer: Cho mình một cà phê sữa\nAssistant: Bạn dùng tại quán hay mang đi ạ?"
H_PAY = "Customer: Mình thanh toán\nAssistant: Bạn thanh toán bằng QR phải không ạ?"
H_MORE = "Customer: Một bạc xỉu\nAssistant: Bạn muốn thêm món gì nữa không ạ?"
H_SIZE = "Customer: Cho mình trà đào\nAssistant: Bạn lấy size nào ạ?"
H_DONE = "Assistant: Hóa đơn của bạn đã sẵn sàng, bạn nhận đồ ở quầy nhé."
H_EN_Q = "Customer: I'd like to pay\nAssistant: Would you like to pay by QR code?"
H_EN_MORE = "Customer: One latte\nAssistant: Would you like anything else?"
H_EN_SIZE = "Customer: A peach tea\nAssistant: Which size would you like?"
H_EN_TAKE = "Customer: One cappuccino\nAssistant: Dine in or take away?"
H_EN_DONE = "Assistant: Your receipt is ready, please collect it at the counter."

D, A = "delegate", "answer"
# (utterance, conversation, expected route, expected language)
REPEAT_CASES = [
    ("Em nói lại giúp mình được không?", H_Q, D, "vi"),
    ("Sorry, what did you say?", H_EN_Q, D, "en"),
    ("Nhắc lại giúp mình nhé", H_DONE, D, "vi"),
    ("Please repeat that", H_EN_DONE, D, "en"),
]
CASES = [
    ("Cho mình xem menu với", H, D, "vi"),
    ("Có trà đào không em?", H, D, "vi"),
    ("Thêm hai ly bạc xỉu size lớn", H, D, "vi"),
    ("Mang đi nhé", H_Q, D, "vi"),
    ("Tại bàn số 5", H_Q, D, "vi"),
    ("Thanh toán đi", H_Q, D, "vi"),
    ("Bỏ ly cà phê sữa ra giúp mình", H, D, "vi"),
    ("Giá bánh croissant bao nhiêu?", H, D, "vi"),
    ("Phô mai nào vậy em?", H, D, "vi"),
    ("Chào em, cho chị một ly cà phê sữa đá", "", D, "vi"),
    ("Alo, cho mình một cà phê sữa", H, D, "vi"),
    ("Size lớn", H_SIZE, D, "vi"),
    ("ừ đúng rồi", H_PAY, D, "vi"),
    ("Ừ, lấy vậy đi", H_PAY, D, "vi"),
    ("thôi khỏi", H_MORE, D, "vi"),
    ("Quay lại menu giúp mình", H, D, "vi"),
    ("Cho mình xem giỏ hàng", H, D, "vi"),
    ("Cho mình một cappuccino", H, D, "vi"),
    ("Can I see the menu?", H_EN, D, "en"),
    ("How much is a cappuccino?", H_EN, D, "en"),
    ("Hi, one iced latte please", "", D, "en"),
    ("Can you hear me? One latte please.", H_EN, D, "en"),
    ("Large please", H_EN_SIZE, D, "en"),
    ("yes please", H_EN_Q, D, "en"),
    ("No thanks, that's all", H_EN_MORE, D, "en"),
    ("Take away", H_EN_TAKE, D, "en"),
    ("I want a bạc xỉu", H_EN, D, "en"),
    ("Chào em", "", A, "vi"),
    ("Em nghe mình nói không?", H, A, "vi"),
    ("Cảm ơn em nhé, tạm biệt", H_DONE, A, "vi"),
    ("Alo alo", H, A, "vi"),
    ("Alo", H_Q, A, "vi"),
    ("Nghe không?", H_PAY, A, "vi"),
    ("Hello there", "", A, "en"),
    ("Can you hear me?", H_EN, A, "en"),
    ("Thank you, bye", H_EN_DONE, A, "en"),
] + REPEAT_CASES

# Eval-only flags for human review; the server never matches spoken text.
AGREEMENT = re.compile(
    r"\b(vâng|được rồi|xong|đã thêm|okay|ok|yes|sure|done|added|available)\b",
    re.IGNORECASE,
)
# An answer that narrates the routing instead of speaking to the customer.
META = re.compile(
    r"answer (directly|myself)|so i('ll| will)? answer|the customer|the assistant"
    r"|that's a greeting|you're asking me|bạn muốn mình nhắc lại|khách hàng",
    re.IGNORECASE,
)
DETAIL_WORDS = {
    "một",
    "hai",
    "ba",
    "bốn",
    "size",
    "lớn",
    "nhỏ",
    "vừa",
    "large",
    "small",
}
VI_WORDS = {"mình", "bạn", "nhé", "ạ", "đang", "cho", "của", "em", "giúp", "món"}
EN_WORDS = {"i", "i'm", "you", "the", "for", "now", "let", "me", "checking", "opening"}


# Vietnamese letters with diacritics: almost every Vietnamese sentence has one.
VI_LETTERS = re.compile(
    "[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]"
)


def language_of(text: str) -> str:
    lowered = text.lower()
    words = re.findall(r"[\wÀ-ỹ']+", lowered)
    marked = bool(VI_LETTERS.search(lowered))
    vi = sum(word in VI_WORDS for word in words) + marked
    en = sum(word in EN_WORDS for word in words) + (not marked)
    return "vi" if vi > en else "en" if en > vi else "unknown"


def repeated_details(utterance: str, filler: str) -> set[str]:
    said = set(re.findall(r"[\wÀ-ỹ]+", utterance.lower())) & DETAIL_WORDS
    said |= set(re.findall(r"\d+", utterance))
    spoken = filler.lower()
    return {word for word in said if re.search(rf"\b{re.escape(word)}\b", spoken)}


class _RecordingEngine:
    """Passes through to the real engine, counting tool-argument characters."""

    def __init__(self, engine: CloudEngine) -> None:
        self._engine = engine
        self.argument_chars = 0

    def reasoning_off_options(self, model: str):
        return self._engine.reasoning_off_options(model)

    async def stream_full(self, messages, **kwargs):
        self.argument_chars = 0
        async for chunk in self._engine.stream_full(messages, **kwargs):
            for call in chunk.tool_calls or []:
                self.argument_chars += len(call["function"].get("arguments") or "")
            yield chunk


async def run_case(responder: VoiceResponder, engine: _RecordingEngine, case):
    utterance, conversation, expected, language = case
    started = time.monotonic()
    delegated_at: list[float] = []
    reply = await responder.respond(
        utterance,
        conversation,
        lambda _filler: delegated_at.append(time.monotonic() - started),
    )
    ended = time.monotonic() - started
    route = reply.kind if reply is not None else "empty"
    text = reply.text if reply is not None else ""
    return {
        "utterance": utterance,
        "expected": expected,
        "route": route,
        "text": text,
        "decision": delegated_at[0] if delegated_at else ended,
        "argument_chars": engine.argument_chars,
        "language_ok": not text or language_of(text) in (language, "unknown"),
        "agreement": bool(AGREEMENT.search(text)) and route == "delegate",
        "meta": bool(META.search(text)) and route == "answer",
        "details": (
            repeated_details(utterance, text) if route == "delegate" else set()
        ),
    }


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()
    os.environ["OPENJARVIS_CONFIG"] = str(Path(args.config))
    settings = load_responder_settings()
    if not settings.model:
        print("No [voice.responder] model in the preset.")
        return 1
    engine = _RecordingEngine(CloudEngine())
    responder = VoiceResponder(engine, settings.model, settings.prompt)
    await run_case(responder, engine, CASES[0])  # warm-up, not counted
    rows = []
    for _ in range(args.runs):
        for case in CASES:
            rows.append(await run_case(responder, engine, case))
    for row in rows:
        marks = []
        if row["route"] != row["expected"]:
            marks.append("ROUTE")
        if not row["language_ok"]:
            marks.append("LANG")
        if row["agreement"]:
            marks.append("AGREE?")
        if row["meta"]:
            marks.append("META?")
        if row["details"]:
            marks.append(f"DETAIL?{sorted(row['details'])}")
        print(
            f"{' '.join(marks) or 'ok':<12} {row['expected']:>8}->{row['route']:<8} "
            f"{row['decision']:.2f}s args={row['argument_chars']:<3} "
            f"| {row['utterance']} | {row['text']}"
        )
    business = [r for r in rows if r["expected"] == D]
    dropped = sum(r["route"] != D for r in business)
    no_filler = sum(r["route"] == D and not r["text"] for r in rows)
    wrong_language = sum(not r["language_ok"] for r in rows)
    flagged = sum(r["agreement"] or bool(r["details"]) or r["meta"] for r in rows)
    over = sum(r["expected"] == A and r["route"] == D for r in rows)
    delegate_times = [r["decision"] for r in rows if r["route"] == D]
    answer_times = [r["decision"] for r in rows if r["route"] == A]
    print(f"\nmodel={settings.model} runs={args.runs} calls={len(rows)}")
    print(f"business without a tool call: {dropped}")
    print(f"delegations without a filler: {no_filler}")
    print(f"wrong language: {wrong_language}")
    print(f"fillers flagged for review: {flagged}")
    empty_argument = sum(r["route"] == D and not r["argument_chars"] for r in rows)
    print(f"allow-list delegated anyway (reported, not gated): {over}")
    print(f"delegations with an empty argument: {empty_argument}")
    if delegate_times:
        print(
            f"tool call p50={statistics.median(delegate_times):.2f}s "
            f"p90={pct(delegate_times, 0.9):.2f}s max={max(delegate_times):.2f}s"
        )
    if answer_times:
        print(
            f"answer p50={statistics.median(answer_times):.2f}s "
            f"p90={pct(answer_times, 0.9):.2f}s"
        )
    passed = dropped == 0 and no_filler == 0 and wrong_language == 0
    print(
        "GATE "
        + ("PASS" if passed else "FAIL")
        + (" (review the flagged fillers by hand)" if flagged else "")
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
