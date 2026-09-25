"""Pure speaker-gate state: settings, turn verdicts, tracker."""

from __future__ import annotations

import pytest

from openjarvis.server.voice.speaker import (
    SpeakerSettings,
    SpeakerTracker,
    Verdict,
    load_speaker_settings,
    turn_verdict,
)

A, R, U = Verdict.ACCEPT, Verdict.REJECT, Verdict.UNCERTAIN


def _verdict(**counts):
    return turn_verdict(
        {Verdict[k]: v for k, v in counts.items()},
        accept_fraction=0.7,
        reject_fraction=0.7,
    )


def test_no_evidence_is_the_legacy_accept():
    assert _verdict() is A


def test_turn_verdict_thresholds():
    assert _verdict(ACCEPT=8, UNCERTAIN=2) is A
    assert _verdict(REJECT=7, ACCEPT=3) is R
    assert _verdict(ACCEPT=6, UNCERTAIN=4) is U
    # Enough accepted frames, but too many rejected ones to trust the turn.
    assert _verdict(ACCEPT=7, REJECT=3) is U


def test_tracker_counts_one_span_into_its_turn():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()
    for verdict in (A, A, A, U):
        tracker.record(verdict)

    assert tracker.has_evidence is True
    assert tracker.span_verdict() is A
    assert tracker.close_turn() is A
    assert tracker.take_turn_verdict() is A


def test_take_without_close_computes_from_counts():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()
    for _ in range(9):
        tracker.record(R)

    # Pipecat's stop watchdog can finalize a turn without our stop strategy.
    assert tracker.take_turn_verdict() is R


def test_take_consumes_the_closed_verdict_once():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()
    tracker.record(U)
    assert tracker.close_turn() is U
    assert tracker.take_turn_verdict() is U

    tracker.begin_span()
    assert tracker.take_turn_verdict() is A


def test_settings_default_off_without_a_preset(monkeypatch):
    monkeypatch.delenv("OPENJARVIS_CONFIG", raising=False)
    assert load_speaker_settings() == SpeakerSettings()


def test_settings_load_from_the_preset(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text(
        "[voice.speaker]\n"
        "enabled = true\n"
        'uncertain_allowed_tools = ["display_menu", "display_bill"]\n'
        "bargein_accept_frames = 4\n"
        "accept_turn_fraction = 0.8\n"
        "reject_turn_fraction = 0.6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    assert load_speaker_settings() == SpeakerSettings(
        enabled=True,
        uncertain_allowed_tools=("display_menu", "display_bill"),
        bargein_accept_frames=4,
        accept_turn_fraction=0.8,
        reject_turn_fraction=0.6,
    )


@pytest.mark.parametrize(
    "body",
    [
        'uncertain_allowed_tools = "display_menu"',
        "accept_turn_fraction = 0",
        "reject_turn_fraction = 1.5",
        "bargein_accept_frames = 0",
        'enabled = "yes"',
    ],
)
def test_malformed_settings_fail_loudly(tmp_path, monkeypatch, body):
    preset = tmp_path / "preset.toml"
    preset.write_text(f"[voice.speaker]\n{body}\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    with pytest.raises(ValueError):
        load_speaker_settings()
