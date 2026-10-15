"""The offline eval treats repeat and explain requests as delegations."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from openjarvis.engine._stubs import StreamChunk
from openjarvis.server.voice.responder import ResponderSettings

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/benchmark_voice_responder.py"
HISTORY = "Customer: I'd like to pay\nAssistant: Would you like to pay by QR code?"


def test_repeat_requests_are_expected_to_delegate():
    # User decision 2026-10-14: the Agent has the whole history and repeats;
    # the Responder answered repeats wrongly in runs 5 and 7.
    namespace = runpy.run_path(str(SCRIPT))

    assert namespace["REPEAT_CASES"]
    assert all(case[2] == "delegate" for case in namespace["REPEAT_CASES"])
    assert all(case in namespace["CASES"] for case in namespace["REPEAT_CASES"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("chunks", "expected_exit"),
    [
        # The Responder answers the repeat request itself: a dropped request.
        ([StreamChunk(content="Would you like to pay by QR code?")], 1),
        # It delegates after a filler: the Agent repeats.
        (
            [
                StreamChunk(
                    content="I'm looking at what was just said.",
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {
                                "name": "getNextResponseFromSupervisor",
                                "arguments": "{}",
                            },
                        }
                    ],
                )
            ],
            0,
        ),
    ],
)
async def test_eval_gate_requires_a_repeat_request_to_delegate(
    monkeypatch, capsys, chunks, expected_exit
):
    main = runpy.run_path(str(SCRIPT))["main"]

    class _Engine:
        def reasoning_off_options(self, model):
            return {}

        async def stream_full(self, messages, **kwargs):
            for chunk in chunks:
                yield chunk

    case = ("Sorry, what did you say?", HISTORY, "delegate", "en")
    monkeypatch.setitem(main.__globals__, "CASES", [case])
    monkeypatch.setitem(main.__globals__, "CloudEngine", _Engine)
    monkeypatch.setitem(
        main.__globals__, "load_responder_settings", lambda: ResponderSettings("test")
    )
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--runs", "1"])
    monkeypatch.setenv("OPENJARVIS_CONFIG", "")

    assert await main() == expected_exit
    assert f"GATE {'PASS' if expected_exit == 0 else 'FAIL'}" in capsys.readouterr().out
