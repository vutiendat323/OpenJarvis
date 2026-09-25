"""Pure speaker-gate state: settings, turn verdicts, tracker."""

from __future__ import annotations

import pytest

from openjarvis.server.voice.speaker import (
    AudioOnlyGate,
    OverlapDetector,
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


def test_taking_a_closed_verdict_keeps_the_next_span_evidence():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()
    tracker.record(A)
    tracker.close_turn()
    # A bystander starts talking before the LLM service takes the verdict.
    tracker.begin_span()
    for _ in range(5):
        tracker.record(R)

    assert tracker.take_turn_verdict() is A
    assert tracker.span_verdict() is R


def test_a_new_turn_drops_a_closed_verdict_nobody_took():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()
    tracker.record(A)
    # Closed ACCEPT with no transcript: no LLM frame ever takes it.
    tracker.close_turn()
    tracker.begin_span()
    tracker.begin_turn()
    for _ in range(9):
        tracker.record(R)

    # This turn is finalized by Pipecat's watchdog, not through close_turn.
    assert tracker.take_turn_verdict() is R


def test_diarizer_settings_load_from_the_preset(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text(
        "[voice.speaker]\n"
        "enabled = true\n"
        'diarizer = "sortformer"\n'
        'diarizer_latency = "low"\n'
        "speaker_active_prob = 0.6\n"
        "overlap_on_frames = 2\n"
        "overlap_off_frames = 5\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    settings = load_speaker_settings()

    assert (settings.diarizer, settings.diarizer_latency) == ("sortformer", "low")
    assert settings.speaker_active_prob == 0.6
    assert (settings.overlap_on_frames, settings.overlap_off_frames) == (2, 5)


@pytest.mark.parametrize(
    "body",
    [
        'diarizer = "pyannote"',
        'diarizer_latency = "instant"',
        "speaker_active_prob = 0",
        "overlap_on_frames = 0",
        'overlap_off_frames = "4"',
    ],
)
def test_malformed_diarizer_settings_fail_loudly(tmp_path, monkeypatch, body):
    preset = tmp_path / "preset.toml"
    preset.write_text(f"[voice.speaker]\n{body}\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    with pytest.raises(ValueError):
        load_speaker_settings()


def test_overlap_needs_sustained_dual_activity():
    detector = OverlapDetector(on_frames=3, off_frames=4)

    assert [detector.update(n) for n in (2, 2, 1)] == [False, False, False]
    assert [detector.update(n) for n in (2, 2, 2)] == [False, False, True]
    assert [detector.update(n) for n in (1, 1, 1)] == [True, True, True]
    assert detector.update(0) is False


def _gate(**overrides):
    return AudioOnlyGate(SpeakerSettings(enabled=True, **overrides))


SILENT = (0.0, 0.0, 0.0, 0.0)
SLOT0 = (0.9, 0.0, 0.0, 0.0)
SLOT1 = (0.0, 0.9, 0.0, 0.0)
BOTH = (0.9, 0.9, 0.0, 0.0)


def test_silence_has_no_verdict():
    assert _gate().frame(SILENT, bot_speaking=False) is None


def test_first_voice_while_bot_silent_is_the_target():
    gate = _gate()

    assert gate.frame(SLOT1, bot_speaking=False) is Verdict.ACCEPT
    assert gate.target == 1
    assert gate.frame(SLOT0, bot_speaking=False) is Verdict.UNCERTAIN


def test_a_voice_heard_only_during_playback_never_becomes_the_target():
    gate = _gate()

    assert gate.frame(SLOT0, bot_speaking=True) is Verdict.UNCERTAIN
    assert gate.target is None


def test_a_slot_living_inside_bot_playback_is_echo_and_rejected():
    gate = _gate()
    gate.frame(SLOT1, bot_speaking=False)  # customer bound first
    verdicts = [gate.frame(SLOT0, bot_speaking=True) for _ in range(30)]

    assert gate.is_echo(0) is True
    assert Verdict.ACCEPT not in verdicts
    assert verdicts[-1] is Verdict.REJECT


def test_customer_over_echo_is_still_accepted():
    gate = _gate()
    gate.frame(SLOT1, bot_speaking=False)
    for _ in range(30):
        gate.frame(SLOT0, bot_speaking=True)

    # Echo slot 0 plus customer slot 1: echo is not a voice, no overlap.
    assert gate.frame(BOTH, bot_speaking=True) is Verdict.ACCEPT


def test_sustained_overlap_with_the_target_is_uncertain():
    gate = _gate(overlap_on_frames=2)
    gate.frame(SLOT1, bot_speaking=False)

    assert gate.frame(BOTH, bot_speaking=False) is Verdict.ACCEPT
    assert gate.frame(BOTH, bot_speaking=False) is Verdict.UNCERTAIN


def test_a_target_later_proven_to_be_echo_is_released():
    gate = _gate()
    # A reverb tail longer than the playback tail binds the bot's own voice.
    gate.frame(SLOT0, bot_speaking=False)
    assert gate.target == 0
    for _ in range(30):
        gate.frame(SLOT0, bot_speaking=True)

    assert gate.target is None
    assert gate.frame(SLOT1, bot_speaking=False) is Verdict.ACCEPT
    assert gate.target == 1
