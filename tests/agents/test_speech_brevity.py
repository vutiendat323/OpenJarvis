"""Spoken replies are as short as the customer needs.

Measured on the live kiosk, replies such as "Đã cập nhật giỏ với hai cà phê muối,
tổng cộng 104.000 đồng và chọn bàn 52. Bạn muốn thêm món khác hay thanh toán giỏ
hàng?" kept the customer listening for ~8 s after the work was done. These checks
keep the written budget from drifting back, since the wording itself is the model's.
"""

from __future__ import annotations

import re
from pathlib import Path

from openjarvis.server.voice.runtime import VOICE_SYSTEM_PROMPT

KIOSK_PROMPT = Path("configs/openjarvis/prompts/ordering-kiosk.md")


def _word_limits(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"at most (\d+) (?:Vietnamese )?words", text)]


def test_generic_voice_prompt_asks_for_one_short_sentence():
    assert "two or three sentences" not in VOICE_SYSTEM_PROMPT
    assert "one short sentence" in VOICE_SYSTEM_PROMPT


def test_kiosk_prompt_no_longer_budgets_fifteen_to_twenty_five_words():
    prompt = KIOSK_PROMPT.read_text()
    assert "15–25" not in prompt


def test_kiosk_prompt_caps_every_spoken_piece_at_a_small_word_limit():
    prompt = KIOSK_PROMPT.read_text()
    limits = _word_limits(prompt)
    # clause alongside a tool call, customer_message / reply, and JSON replies
    assert len(limits) >= 3
    assert max(limits) <= 12


def test_kiosk_prompt_asks_for_a_clause_on_the_first_tool_call_of_every_turn():
    # Live 2026-10-10 12:09: with the clause limited to checkout, a four-round
    # turn was silent for 18.8 s and the only speech came after the last round.
    prompt = KIOSK_PROMPT.read_text()
    assert "first tool call of every turn" in prompt
    assert "Fast tools (menu, tables, cart edits) get no" not in prompt
