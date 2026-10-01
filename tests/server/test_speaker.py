"""Pure speaker-gate state: settings, turn verdicts, tracker."""

from __future__ import annotations

import time as _time

import pytest

from openjarvis.server.voice.speaker import (
    AudioOnlyGate,
    FaceTrackBuffer,
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
    assert SpeakerSettings().enhancer == "none"


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
        'separator = "sepformer"',
        'separator = "tse"',  # needs stt_mask: it holds the delayed STT copy
    ],
)
def test_malformed_diarizer_settings_fail_loudly(tmp_path, monkeypatch, body):
    preset = tmp_path / "preset.toml"
    preset.write_text(f"[voice.speaker]\n{body}\n", encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    with pytest.raises(ValueError):
        load_speaker_settings()


def test_separator_settings_load_from_the_preset(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text(
        '[voice.speaker]\nstt_mask = true\nseparator = "tse"\n'
        'separator_model = "/models/tse.ts"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    settings = load_speaker_settings()

    assert (settings.separator, settings.separator_model) == ("tse", "/models/tse.ts")
    assert SpeakerSettings().separator == "none"


def test_enhancer_loads_without_diarizer(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text(
        '[voice.speaker]\nenabled = true\nenhancer = "rnnoise"\n', encoding="utf-8"
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    settings = load_speaker_settings()
    assert settings.enhancer == "rnnoise"
    assert settings.diarizer == "none"


def test_unknown_enhancer_is_rejected(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text('[voice.speaker]\nenhancer = "maxine"\n', encoding="utf-8")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))

    with pytest.raises(ValueError, match="voice_speaker_enhancer"):
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


def test_audio_only_target_moves_to_a_voice_that_holds_the_floor():
    gate = _gate()
    gate.frame(SLOT0, bot_speaking=False)  # a bystander happened to speak first
    verdicts = [gate.frame(SLOT1, bot_speaking=False) for _ in range(8)]

    assert verdicts[0] is Verdict.UNCERTAIN
    assert gate.target == 1
    assert verdicts[-1] is Verdict.ACCEPT


def test_speech_during_playback_never_takes_the_floor():
    gate = _gate()
    gate.frame(SLOT1, bot_speaking=False)

    verdicts = [gate.frame(SLOT0, bot_speaking=True) for _ in range(8)]

    assert set(verdicts) == {Verdict.UNCERTAIN}
    assert gate.target == 1


def _faces(ts, *tracks):
    return {
        "event": "faces",
        "ts": ts,
        "tracks": [
            {
                "track_id": tid,
                "distance_m": d,
                "facing": d is not None,
                "cx_norm": 0.5,
                "mouth_activity": m,
                "active_speaker_probability": None,
            }
            for tid, d, m in tracks
        ],
    }


def test_face_buffer_picks_the_nearest_engaged_face():
    buffer = FaceTrackBuffer()
    buffer.add(_faces(10.0, (1, 2.4, 0.9), (2, 0.8, 0.1), (3, None, 1.0)))

    assert buffer.anchor(10.0, max_m=1.5) == 2
    assert buffer.anchor(10.0, max_m=0.5) is None


def test_face_buffer_averages_the_mouth_over_a_window():
    buffer = FaceTrackBuffer()
    for i, m in enumerate((0.1, 0.9, 0.2)):
        buffer.add(_faces(10.0 + i * 0.1, (2, 0.8, m)))

    assert buffer.mouth(2, 9.95, 10.25) == pytest.approx(0.4)
    assert buffer.mouth(2, 10.15, 10.25) == 0.2
    assert buffer.mouth(9, 9.0, 11.0) is None


def test_face_buffer_freshness_and_history_limit():
    buffer = FaceTrackBuffer(history_s=3.0)
    buffer.add(_faces(10.0, (2, 0.8, 0.9)))
    buffer.add(_faces(14.0, (2, 0.8, 0.1)))

    assert buffer.fresh(14.5) and not buffer.fresh(15.5)
    assert buffer.mouth(2, 9.0, 11.0) is None  # older than 3 s, dropped


def _asd_faces(probabilities, *, stream="current", ts=101.0, start=100.0, mouth=0.05):
    event = _faces(ts, (2, 0.7, mouth))
    event["tracks"][0]["asd"] = {
        "stream_id": stream,
        "t0": start,
        "frame_secs": 0.04,
        "probabilities": probabilities,
    }
    return event


def test_asd_lookup_requires_current_stream_complete_support_and_fresh_window():
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.8] * 25))
    assert faces.active_speaker(
        2, 100.16, 100.24, stream_id="current", now=101
    ) == pytest.approx(0.8)
    assert faces.active_speaker(2, 100.16, 100.24, stream_id="old", now=101) is None
    assert faces.active_speaker(2, 100.96, 101.04, stream_id="current", now=101) is None
    faces.add(_asd_faces([0.8] * 25, ts=102.01))  # fresh event, expired window
    assert (
        faces.active_speaker(2, 100.16, 100.24, stream_id="current", now=102.01) is None
    )


def test_asd_window_ending_a_float_step_early_still_covers_its_chunk():
    # At Unix-second magnitudes one float step is ~2.4e-7 s; Vision's window
    # end and the chunk end come from different sums of the same samples.
    end = 1790000000.24
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.8] * 25, ts=end, start=end - 1.0 - 5e-7))
    assert faces.active_speaker(
        2, end - 0.24, end, stream_id="current", now=end
    ) == pytest.approx(0.8, abs=1e-5)


def test_asd_lookup_weights_partial_bins_and_rejects_malformed_results():
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.2, 0.8] + [0.5] * 23))
    assert faces.active_speaker(
        2, 100.02, 100.06, stream_id="current", now=101
    ) == pytest.approx(0.5)
    for values in ([0.8] * 24, [float("nan")] + [0.8] * 24, [1.1] + [0.8] * 24):
        faces.add(_asd_faces(values, ts=101.01))
        assert faces.active_speaker(
            2, 100.02, 100.06, stream_id="current", now=101.01
        ) == pytest.approx(0.5)
    faces.set_asd_stream(None)
    assert faces.active_speaker(2, 100.02, 100.06, stream_id="current", now=101) is None
    assert faces.mouth(2, 100.9, 101.1) == pytest.approx(0.05)


def test_asd_lookup_selects_newest_valid_window():
    faces = FaceTrackBuffer()
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.2] * 25, ts=101.0))
    faces.add(_asd_faces([0.8] * 25, ts=101.04, start=100.04))
    assert faces.active_speaker(
        2, 100.16, 100.24, stream_id="current", now=101.04
    ) == pytest.approx(0.8)
    assert faces.active_speaker(
        2, 100.02, 100.06, stream_id="current", now=101.04
    ) == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("probability", "mouth", "expected"),
    [(0.7, 0.05, A), (0.3, 0.9, R), (0.5, 0.9, A), (0.5, 0.05, R)],
)
def test_asd_policy_uses_thresholds_then_mouth_fallback(
    probability, mouth, expected, monkeypatch
):
    monkeypatch.setattr("openjarvis.server.voice.speaker.time.time", lambda: 100.24)
    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    faces.add(_asd_faces([probability] * 25, ts=100.24, mouth=mouth))
    assert (
        gate.frame(SLOT1, bot_speaking=False, t=100.24, asd_stream_id="current")
        is expected
    )


def test_asd_rows_are_counted_by_reason_for_effective_use(monkeypatch):
    # Spec §9 needs "fraction of eligible post-warm-up anchor rows using ASD"
    # from the logs: every ASD-eligible row gets exactly one reason.
    monkeypatch.setattr("openjarvis.server.voice.speaker.time.time", lambda: 100.24)
    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    # Newer windows (later start) win the lookup.
    for start, probability in ((100.0, 0.9), (100.04, 0.1), (100.08, 0.5)):
        faces.add(_asd_faces([probability] * 25, ts=100.24, start=start))
        gate.frame(SLOT1, bot_speaking=False, t=100.24, asd_stream_id="current")
    assert gate.last_evidence_detail["fallback_reason"] == "asd_middle"
    assert dict(gate.asd_counts) == {"used_accept": 1, "used_reject": 1, "middle": 1}

    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    faces.add(_faces(100.24, (2, 0.7, 0.05)))
    for reason in ("late", "warmup", None):
        gate.asd_row_reason = reason
        gate.frame(SLOT1, bot_speaking=False, t=100.24, asd_stream_id="current")
        assert gate.last_evidence_detail["fallback_reason"] == f"asd_{reason or 'gap'}"
    assert dict(gate.asd_counts) == {"late": 1, "warmup": 1, "gap": 1}


def test_delayed_diarizer_cannot_use_an_expired_supporting_window():
    import time

    now = time.time()
    start = now - 3.5
    t = start + 0.24
    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.9] * 25, ts=t, start=start, mouth=0.05))

    assert gate.frame(SLOT1, bot_speaking=False, t=t, asd_stream_id="current") is R
    assert gate.last_evidence_detail["fallback_reason"] == "asd_gap"


def test_asd_never_resolves_overlap_or_missing_anchor(monkeypatch):
    # Fresh ASD: a high score over a still mouth keeps overlap UNCERTAIN.
    monkeypatch.setattr("openjarvis.server.voice.speaker.time.time", lambda: 100.24)
    gate, faces = _vision_gate(vision_asd=True, overlap_on_frames=1)
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.9] * 25, ts=100.24))
    assert gate.frame(BOTH, bot_speaking=False, t=100.24, asd_stream_id="current") is U
    assert gate.last_evidence_detail["probability"] == pytest.approx(0.9)
    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.9] * 25, ts=100.24))
    assert gate.frame(SLOT1, bot_speaking=False, t=100.24, asd_stream_id="old") is R
    faces.add(_asd_faces([0.9] * 25, ts=102.0))
    assert gate.frame(SLOT1, bot_speaking=False, t=102.0, asd_stream_id="current") is R
    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.9] * 25, ts=100.24))
    faces.add(_faces(100.3, (2, 2.0, 0.9)))
    assert gate.frame(SLOT1, bot_speaking=False, t=100.3, asd_stream_id="current") is U


def test_stale_vision_and_echo_keep_their_authority_with_asd():
    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    faces.add(_asd_faces([0.1] * 25, ts=100.24))
    assert gate.frame(SLOT1, bot_speaking=False, t=102.0, asd_stream_id="current") is A

    gate, faces = _vision_gate(vision_asd=True)
    faces.set_asd_stream("current")
    for i in range(26):
        t = 100.0 + i * 0.08
        faces.add(_asd_faces([0.9] * 25, ts=t))
        verdict = gate.frame(SLOT0, bot_speaking=True, t=t, asd_stream_id="current")
    assert verdict is R


def _vision_gate(**overrides):
    faces = FaceTrackBuffer()
    settings = SpeakerSettings(enabled=True, vision_faces=True, **overrides)
    return AudioOnlyGate(settings, faces=faces), faces


def test_voice_with_the_customers_mouth_moving_is_accepted():
    gate, faces = _vision_gate()
    faces.add(_faces(100.0, (2, 0.7, 0.9)))

    assert gate.frame(SLOT1, bot_speaking=True, t=100.1) is Verdict.ACCEPT


def test_voice_while_the_customers_mouth_is_still_is_rejected():
    gate, faces = _vision_gate()
    faces.add(_faces(100.0, (2, 0.7, 0.05)))

    # The 2026-09-26 live failure: a phone video talking over Jarvis.
    assert gate.frame(SLOT1, bot_speaking=True, t=100.1) is Verdict.REJECT


def test_a_farther_talker_is_not_the_customer():
    gate, faces = _vision_gate()
    faces.add(_faces(100.0, (2, 0.7, 0.05), (5, 1.2, 1.0)))

    assert gate.frame(SLOT1, bot_speaking=False, t=100.1) is Verdict.REJECT


def test_nobody_engaged_is_uncertain_not_accepted():
    gate, faces = _vision_gate()
    faces.add(_faces(100.0, (5, 2.5, 1.0)))

    assert gate.frame(SLOT1, bot_speaking=False, t=100.1) is Verdict.UNCERTAIN


def test_overlap_frames_still_log_the_customers_mouth():
    gate, faces = _vision_gate(overlap_on_frames=1)
    faces.add(_faces(100.0, (2, 0.7, 0.9)))

    assert gate.frame(BOTH, bot_speaking=False, t=100.1) is Verdict.UNCERTAIN
    assert gate.overlap and gate.last_evidence == (2, 0.9)
    assert gate.frame(SILENT, bot_speaking=False, t=100.2) is None
    assert gate.last_evidence is None


def test_overlap_while_the_customers_mouth_is_still_is_rejected():
    gate, faces = _vision_gate(overlap_on_frames=1)
    faces.add(_faces(100.0, (2, 0.7, 0.05)))

    # 2026-09-28 live leak: the customer stopped, the video kept talking,
    # and overlap hysteresis kept those words UNCERTAIN, so they were acted on.
    assert gate.frame(BOTH, bot_speaking=False, t=100.1) is Verdict.REJECT
    assert gate.overlap and gate.last_evidence_detail["mouth"] == 0.05


def _mouth_stream(faces, start, values, track=2, dist=0.7):
    for i, m in enumerate(values):
        faces.add(_faces(start + i * 0.1, (track, dist, m)))


def test_sustained_mouth_movement_accepts_and_stillness_rejects():
    gate, faces = _vision_gate()
    _mouth_stream(faces, 100.0, [0.8] * 8 + [0.05] * 10)

    assert gate.frame(SLOT1, bot_speaking=False, t=100.3) is Verdict.ACCEPT
    assert gate.frame(SLOT1, bot_speaking=False, t=101.4) is Verdict.REJECT


def test_one_noisy_mouth_sample_does_not_accept():
    gate, faces = _vision_gate()
    # A glance at the phone: one high sample among a still mouth, which
    # the old max-over-window turned into ~0.6 s of ACCEPT.
    _mouth_stream(faces, 100.0, [0.05] * 3 + [1.0] + [0.05] * 5)

    assert gate.frame(SLOT1, bot_speaking=True, t=100.3) is Verdict.REJECT


def test_stale_vision_falls_back_to_audio_only():
    gate, faces = _vision_gate()
    faces.add(_faces(100.0, (2, 0.7, 0.05)))

    # 2 s later with no new faces event: audio-only binds the first voice.
    assert gate.frame(SLOT1, bot_speaking=False, t=102.0) is Verdict.ACCEPT


def test_echo_is_still_rejected_with_vision():
    gate, faces = _vision_gate()
    for i in range(30):
        faces.add(_faces(100.0 + i * 0.08, (2, 0.7, 0.9)))
        gate.frame(SLOT0, bot_speaking=True, t=100.0 + i * 0.08)

    assert gate.frame(SLOT0, bot_speaking=True, t=102.4) is Verdict.REJECT


def test_vision_settings_load(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text(
        "[voice.speaker]\n"
        "vision_faces = true\n"
        "mouth_active = 0.4\n"
        "anchor_max_m = 1.2\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    settings = load_speaker_settings()

    assert (settings.vision_faces, settings.mouth_active, settings.anchor_max_m) == (
        True,
        0.4,
        1.2,
    )


def test_a_diarized_turn_without_evidence_is_uncertain():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True), diarized=True)
    tracker.begin_span()

    # The diarizer ran but never confirmed the speech (it lagged, or heard
    # nobody it could place): that is not the customer's word.
    assert tracker.has_evidence is True
    assert tracker.close_turn() is U


def _masked_turn(*frames):
    tracker = SpeakerTracker(SpeakerSettings(enabled=True, stt_mask=True))
    tracker.begin_span()
    for verdict, n in frames:
        for _ in range(n):
            tracker.record(verdict)
    return tracker


def test_masked_rejected_speech_does_not_decide_the_turn():
    """Live 2026-09-26 13:54: "Giờ là mấy giờ rồi bạn?" between phone-video
    speech closed REJECT (33 R, 14 A) and was dropped; the video never
    reached the transcript."""
    tracker = _masked_turn((R, 33), (A, 14))

    assert tracker.span_verdict() is R  # turn starts still count everything
    assert tracker.close_turn() is A
    assert _masked_turn((R, 18), (A, 17), (U, 3)).take_turn_verdict() is A
    assert _masked_turn((R, 9), (A, 5), (U, 8)).close_turn() is U


def test_masked_turn_of_only_rejected_speech_stays_rejected():
    assert _masked_turn((R, 31)).close_turn() is R


def test_without_stt_mask_rejected_speech_still_counts():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()
    for verdict, n in ((R, 33), (A, 14)):
        for _ in range(n):
            tracker.record(verdict)

    assert tracker.close_turn() is R


def test_without_a_diarizer_no_evidence_stays_the_legacy_accept():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.begin_span()

    assert tracker.has_evidence is False
    assert tracker.close_turn() is A


def test_stt_mask_settings_load(tmp_path, monkeypatch):
    preset = tmp_path / "preset.toml"
    preset.write_text(
        "[voice.speaker]\nstt_mask = true\nstt_mask_delay_secs = 0.6\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    settings = load_speaker_settings()

    assert (settings.stt_mask, settings.stt_mask_delay_secs) == (True, 0.6)
    assert SpeakerSettings().stt_mask is False


FUSION_PRESET = """[voice.speaker]
enabled = true
diarizer = "sortformer"
vision_faces = true
vision_asd = true
identity = "fusion"
"""


def _load(tmp_path, monkeypatch, body):
    path = tmp_path / "preset.toml"
    path.write_text(body)
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(path))
    return load_speaker_settings()


def test_fusion_defaults(tmp_path, monkeypatch):
    s = _load(tmp_path, monkeypatch, FUSION_PRESET)
    assert s.identity == "fusion"
    assert (s.lock_asd_frames, s.voiceprint_min_secs, s.embed_segment_secs) == (
        6,
        2.0,
        1.0,
    )
    assert (s.voice_match, s.voice_reject, s.bot_match) == (0.65, 0.45, 0.70)
    assert s.embedder_model == "titanet_small"


def test_identity_defaults_to_none():
    assert SpeakerSettings().identity == "none"


@pytest.mark.parametrize("missing", ["enabled", "vision_faces", "vision_asd"])
def test_fusion_needs_the_whole_speaker_gate(tmp_path, monkeypatch, missing):
    body = FUSION_PRESET.replace(f"{missing} = true", f"{missing} = false")
    if missing != "vision_asd":
        body = body.replace("vision_asd = true", "vision_asd = false")
    with pytest.raises(ValueError, match="fusion"):
        _load(tmp_path, monkeypatch, body)


@pytest.mark.parametrize(
    "extra, error",
    [
        ('identity = "clusters"', "identity"),
        ("voice_reject = 0.7", "voice_thresholds"),
        ("lock_asd_frames = 0", "lock_asd_frames"),
        ('embedder_model = ""', "embedder_model"),
        # Segments stop growing at 3 s (SEGMENT_MAX_SECS): longer never embeds.
        ("embed_segment_secs = 3.5", "embed_segment_secs_too_long"),
    ],
)
def test_bad_fusion_values_fail_the_load(tmp_path, monkeypatch, extra, error):
    if extra.startswith("identity"):
        body = FUSION_PRESET.replace('identity = "fusion"', extra)
    else:
        body = FUSION_PRESET + extra + "\n"
    with pytest.raises(ValueError, match=error):
        _load(tmp_path, monkeypatch, body)


def test_the_longest_embedding_segment_is_the_segment_cap(tmp_path, monkeypatch):
    s = _load(tmp_path, monkeypatch, FUSION_PRESET + "embed_segment_secs = 3.0\n")
    assert s.embed_segment_secs == 3.0


def test_kiosk_state_accessor_reads_the_fsm(monkeypatch):
    import openjarvis.kiosk.runtime as runtime

    monkeypatch.setattr(runtime, "_current_state", "active")
    assert runtime.current_state() == "active"


def _window(stream="s", track=7, t0=None, p=0.9):
    t0 = _time.time() - 0.9 if t0 is None else t0
    return {
        "stream_id": stream,
        "track_id": track,
        "t0": t0,
        "frame_secs": 0.04,
        "probabilities": [p] * 25,
    }


def _faces_event(ts, *tracks, asd=None):
    rows = []
    for track in tracks:
        row = {"track_id": track, "distance_m": 0.8, "mouth_activity": 0.2}
        if asd is not None and track == asd["track_id"]:
            row["asd"] = asd
        rows.append(row)
    return {"event": "faces", "ts": ts, "tracks": rows}


def test_pushed_asd_is_read_and_faces_asd_ignored():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    faces.add(_faces_event(now, 7, asd=_window(p=0.1)))  # a v1 field: ignored
    assert faces.active_speaker(7, now - 0.08, now, stream_id="s", now=now) is None
    faces.add_asd(_window(p=0.9))
    assert faces.active_speaker(
        7, now - 0.08, now, stream_id="s", now=now
    ) == pytest.approx(0.9)


def test_pushed_asd_for_another_stream_is_dropped():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    faces.add_asd(_window(stream="old"))
    assert faces.active_speaker(7, now - 0.08, now, stream_id="s", now=now) is None


def test_v1_mode_still_reads_asd_from_faces_events():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.add(_faces_event(now, 7, asd=_window(p=0.8)))
    assert faces.active_speaker(
        7, now - 0.08, now, stream_id="s", now=now
    ) == pytest.approx(0.8)


def test_snapshot_without_asd_drops_pushed_windows():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    faces.add_asd(_window())
    frozen = faces.snapshot(include_asd=False)
    assert frozen.active_speaker(7, now - 0.08, now, stream_id="s", now=now) is None
    assert faces.snapshot().active_speaker(7, now - 0.08, now, stream_id="s", now=now)


def test_tracks_at_and_present():
    faces = FaceTrackBuffer()
    faces.add(_faces_event(100.0, 7, 9))
    faces.add(_faces_event(100.5, 9))
    assert [t["track_id"] for t in faces.tracks_at(100.1)] == [7, 9]
    assert faces.present(7, 99.8, 100.2)
    assert not faces.present(7, 100.3, 100.7)
    assert FaceTrackBuffer().tracks_at(1.0) == []


def test_tracker_counts_sources_per_span():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.record(A, "asd")
    tracker.record(A, "voice")
    tracker.record(R)
    assert tracker.source_counts() == {"asd": 1, "voice": 1}
    tracker.begin_span()
    assert tracker.source_counts() == {}
    assert tracker.bargein_blocked == 0
