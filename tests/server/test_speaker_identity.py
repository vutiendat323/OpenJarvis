"""Pure identity layer: voiceprint, slot↔track binder, target lock, fusion gate."""

from __future__ import annotations

import time
from dataclasses import replace

import numpy as np
import pytest

from openjarvis.server.voice.speaker import (
    AudioOnlyGate,
    FaceTrackBuffer,
    SpeakerSettings,
    Verdict,
)
from openjarvis.server.voice.speaker_identity import (
    EmbeddingJob,
    FusionGate,
    Label,
    LockState,
    SlotIdentity,
    SlotTrackBinder,
    TargetLock,
    Voiceprint,
    cosine,
    unit,
)

E0 = np.array([1.0, 0.0, 0.0, 0.0], np.float32)
E1 = np.array([0.0, 1.0, 0.0, 0.0], np.float32)


def test_unit_and_cosine():
    assert np.linalg.norm(unit([3.0, 4.0])) == pytest.approx(1.0)
    assert cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 5]) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        unit([0.0, 0.0])


def test_voiceprint_readiness_and_similarity():
    vp = Voiceprint()
    assert vp.similarity(E0) is None
    vp.enroll(E0 * 7, 1.0)
    assert not vp.ready(2.0)
    vp.enroll(E0, 1.0)
    assert vp.ready(2.0)
    assert vp.similarity(E0) == pytest.approx(1.0)
    assert vp.similarity(E1) == pytest.approx(0.0)


def test_voiceprint_keeps_the_newest_eight():
    vp = Voiceprint()
    for _ in range(8):
        vp.enroll(E1, 1.0)
    for _ in range(8):
        vp.enroll(E0, 1.0)
    assert vp.seconds == 8.0
    assert vp.similarity(E0) == pytest.approx(1.0)


def _rows(binder, n, slot, scores, t0=100.0):
    for i in range(n):
        binder.update(t0 + i * 0.08, {slot: 1.0}, scores)


def test_binder_maps_after_one_second_of_evidence():
    binder = SlotTrackBinder()
    _rows(binder, 13, 0, {5: 1.0})  # 13 rows = 0.974: per-row decay keeps it under 1.0
    assert binder.track_of(0) is None
    _rows(binder, 1, 0, {5: 1.0}, t0=101.04)
    assert binder.track_of(0) == 5


def test_binder_needs_a_dominant_track():
    binder = SlotTrackBinder()
    _rows(binder, 20, 0, {5: 1.0, 6: 0.6})
    assert binder.track_of(0) is None
    _rows(binder, 20, 1, {5: 1.0, 6: 0.4}, t0=102.0)
    assert binder.track_of(1) == 5


def test_binder_evidence_decays_and_absent_tracks_are_forgotten():
    binder = SlotTrackBinder()
    _rows(binder, 14, 0, {5: 1.0})
    assert binder.track_of(0) == 5
    binder.update(110.0, {}, {6: 1.0})  # 9 s later; track 5 unseen for > 5 s
    assert binder.track_of(0) is None


FUSION = SpeakerSettings(
    enabled=True,
    diarizer="sortformer",
    vision_faces=True,
    vision_asd=True,
    identity="fusion",
)


def _asd_rows(lock, n, *, track=7, slot=0, t0=100.0, step=0.08):
    for i in range(n):
        lock.observe_row(t0 + i * step, [slot], anchor=track, anchor_asd_accept=True)


def _locked(track=7, slot=0, t0=100.0):
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", t0 - 1.0)
    _asd_rows(lock, 6, track=track, slot=slot, t0=t0)
    assert lock.locked
    lock.drain_events()
    return lock


def _job(lock, slot=0, end=101.0, seconds=1.0, confirmed=True):
    return EmbeddingJob(
        epoch=lock.epoch,
        slot=slot,
        segment_end=end,
        seconds=seconds,
        pcm=np.zeros(16000, np.int16),
        target_confirmed=confirmed,
    )


def test_nothing_locks_outside_an_active_kiosk_session():
    lock = TargetLock(FUSION)
    _asd_rows(lock, 10)
    assert lock.state is LockState.NONE


def test_six_asd_rows_of_one_pair_within_three_seconds_lock():
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", 99.0)
    assert lock.state is LockState.PRE_LOCK
    _asd_rows(lock, 5)
    assert not lock.locked
    _asd_rows(lock, 1, t0=100.4)
    assert lock.locked and lock.target_track == 7 and lock.target_slots == {0}
    (event,) = lock.drain_events()
    assert event.event == "lock" and "asd_frames=6" in event.fields()
    assert lock.last_locked_after_s == pytest.approx(1.4)


def test_rows_spread_beyond_three_seconds_never_lock():
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", 99.0)
    _asd_rows(lock, 6, step=0.7)
    assert not lock.locked


def test_mar_rows_and_overlap_rows_never_lock():
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", 99.0)
    for i in range(10):
        lock.observe_row(100 + i * 0.08, [0], anchor=7, anchor_asd_accept=False)
        lock.observe_row(100 + i * 0.08, [0, 1], anchor=7, anchor_asd_accept=True)
    assert not lock.locked


def test_leaving_active_releases_and_bumps_the_epoch():
    lock = _locked()
    epoch = lock.epoch
    lock.observe_fsm("cleanup", 101.0)
    assert lock.state is LockState.NONE and lock.epoch == epoch + 1
    assert lock.target_track is None and lock.target_slots == set()
    (event,) = lock.drain_events()
    assert event.event == "release" and "reason=fsm_cleanup" in event.fields()


def test_fsm_flap_releases_then_relocks_on_fresh_evidence():
    lock = _locked()
    job = _job(lock)
    lock.observe_fsm("cleanup", 101.0)
    lock.observe_fsm("active", 101.1)
    assert not lock.observe_embedding(job, E0)  # the old customer's audio
    _asd_rows(lock, 6, track=9, slot=2, t0=101.2)
    assert lock.target_track == 9 and lock.target_slots == {2}
    assert lock.lock_count == 2


def test_a_closer_person_never_steals_the_lock():
    lock = _locked()
    lock.voiceprint.enroll(E0, 2.0)  # ready, so only the absence gate can block
    for i in range(12):
        lock.observe_row(
            100.5 + i * 0.08, [0], anchor=9, anchor_asd_accept=True, target_visible=True
        )
    assert lock.target_track == 7


def test_slot_rebind_by_asd_needs_solo_rows():
    lock = _locked()
    for i in range(6):
        lock.observe_row(
            100.5 + i * 0.08,
            [3, 4],
            anchor=7,
            anchor_asd_accept=True,
            target_visible=True,
            target_asd_accept=True,
        )
    assert not {3, 4} & lock.target_slots
    for i in range(6):
        lock.observe_row(
            101.0 + i * 0.08,
            [2],
            anchor=7,
            anchor_asd_accept=True,
            target_visible=True,
            target_asd_accept=True,
        )
    assert lock.target_slots == {0, 2}
    assert [e.event for e in lock.drain_events()] == ["rebind_slot"]


def test_absent_target_is_unpinned_and_a_returning_target_repinned():
    lock = _locked()  # last seen at 100.4
    assert lock.desired_pin(100.8) == 7
    assert lock.desired_pin(101.0) is None
    lock.observe_row(
        101.0, [], anchor=None, anchor_asd_accept=False, target_visible=True
    )
    assert lock.desired_pin(101.0) == 7


def test_track_rebind_needs_absence_asd_a_target_slot_and_a_ready_voiceprint():
    lock = _locked()
    _asd_rows(lock, 6, track=9, slot=0, t0=101.0)
    assert lock.target_track == 7  # voiceprint not ready
    lock.voiceprint.enroll(E0, 2.0)
    _asd_rows(lock, 6, track=9, slot=5, t0=102.0)
    assert lock.target_track == 7  # slot 5 is not the target's voice
    _asd_rows(lock, 6, track=9, slot=0, t0=103.0)
    assert lock.target_track == 9
    assert lock.desired_pin(103.45) == 9


def test_embeddings_from_an_older_epoch_or_segment_are_dropped():
    lock = _locked()
    job = _job(lock)
    assert lock.observe_embedding(job, E0)
    assert not lock.observe_embedding(replace(job, segment_end=100.5), E0)
    stale = replace(job, segment_end=102.0)
    lock.observe_fsm("idle", 102.0)
    lock.observe_fsm("active", 102.0)
    assert not lock.observe_embedding(stale, E0)


def test_only_target_confirmed_segments_enroll():
    lock = _locked()
    lock.observe_embedding(_job(lock, confirmed=False), E0)
    assert lock.voiceprint.seconds == 0.0
    lock.observe_embedding(_job(lock, end=102.0, confirmed=True), E0)
    assert lock.voiceprint.seconds == 1.0


def test_a_matching_voice_adds_its_slot_once_the_voiceprint_is_ready():
    lock = _locked()
    lock.observe_embedding(_job(lock, seconds=2.0), E0)
    assert lock.voice_ready
    lock.observe_embedding(_job(lock, slot=3, end=102.0, confirmed=False), E0 * 2)
    assert 3 in lock.target_slots
    assert "reason=voice" in lock.drain_events()[-1].fields()


def test_labels():
    lock = _locked()
    lock.bot_voiceprint = E1
    lock.observe_embedding(_job(lock, slot=2, confirmed=False), E1)
    assert lock.label(2) is Label.BOT
    assert lock.label(5, bot_echo=True) is Label.BOT  # no embedding yet: echo rule
    assert lock.label(0) is Label.TARGET
    lock.observe_embedding(_job(lock, slot=0, end=102.0, seconds=2.0), E0)
    lock.observe_embedding(
        _job(lock, slot=3, end=102.0, confirmed=False),
        np.array([0.0, 0.0, 1.0, 0.0], np.float32),
    )
    assert lock.label(3) is Label.OTHER
    for i in range(14):  # 14 rows: evidence decays per row, 13 give 0.974 < 1.0
        lock.binder.update(103.0 + i * 0.08, {4: 1.0}, {9: 1.0})
    assert lock.label(4) is Label.OTHER
    assert lock.label(1) is Label.UNKNOWN


def test_voice_ok_needs_a_ready_voiceprint_and_a_match():
    lock = _locked()
    lock.slots[0] = SlotIdentity(voice_sim=0.9)
    assert not lock.voice_ok(0)
    lock.voiceprint.enroll(E0, 2.0)
    assert lock.voice_ok(0)
    lock.slots[0] = SlotIdentity(voice_sim=0.5)
    assert not lock.voice_ok(0)


def test_asd_never_promotes_a_voice_rejected_slot():
    lock = _locked()
    lock.observe_embedding(_job(lock, seconds=2.0), E0)
    lock.observe_embedding(
        _job(lock, slot=3, end=102.0, confirmed=False),
        np.array([0.0, 0.0, 1.0, 0.0], np.float32),
    )
    for i in range(8):
        lock.observe_row(
            101.0 + i * 0.08,
            [3],
            anchor=7,
            anchor_asd_accept=True,
            target_visible=True,
            target_asd_accept=True,
        )
    assert 3 not in lock.target_slots
    assert lock.label(3) is Label.OTHER


A, R, U = Verdict.ACCEPT, Verdict.REJECT, Verdict.UNCERTAIN


def _row(*slots):
    return tuple(0.9 if s in slots else 0.0 for s in range(4))


def _faces(*, mouth=None, asd=None, tracks=(7,)):
    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    for k in range(-2, 25):
        faces.add(
            {
                "event": "faces",
                "ts": now + k * 0.1,
                "tracks": [
                    {"track_id": x, "distance_m": 0.8, "mouth_activity": mouth}
                    for x in tracks
                ],
            }
        )
    if asd is not None:
        faces.add_asd(
            {
                "stream_id": "s",
                "track_id": tracks[0],
                "t0": now - 0.9,
                "frame_secs": 0.04,
                "probabilities": [asd] * 25,
            }
        )
    return faces, now


def _locked_gate(faces, now, *, voice=False):
    gate = FusionGate(FUSION, faces, fsm_state=lambda: "active")
    gate.lock.observe_fsm("active", now - 1.0)
    for i in range(6):
        gate.lock.observe_row(
            now - 0.45 + i * 0.08, [0], anchor=7, anchor_asd_accept=True
        )
    if voice:
        gate.lock.voiceprint.enroll(E0, 2.0)
        gate.lock.slots[0] = SlotIdentity(voice_sim=0.9)
    return gate


def _frame(gate, now, *slots, times=1):
    verdict = None
    for i in range(times):
        verdict = gate.frame(
            _row(*slots), bot_speaking=False, t=now + i * 0.001, asd_stream_id="s"
        )
    return verdict


@pytest.mark.parametrize(
    "case, faces_kw, voice, slots, expected, source",
    [
        ("silence", {"mouth": 0.0}, False, (), None, None),
        ("target_asd", {"asd": 0.9}, False, (0,), A, "asd"),
        ("target_mar", {"mouth": 0.8}, False, (0,), A, "mar"),
        ("target_still_voice", {"mouth": 0.0}, True, (0,), A, "voice"),
        ("target_still_no_voice", {"mouth": 0.0}, False, (0,), R, None),
        ("target_hidden_voice", {"mouth": 0.0, "tracks": (9,)}, True, (0,), A, "voice"),
        (
            "target_hidden_no_voice",
            {"mouth": 0.0, "tracks": (9,)},
            False,
            (0,),
            U,
            None,
        ),
        ("unknown_with_target_asd", {"asd": 0.9}, False, (1,), A, "asd"),
        ("unknown_without_evidence", {"mouth": 0.0}, False, (1,), U, None),
    ],
)
def test_locked_verdict_table(case, faces_kw, voice, slots, expected, source):
    faces, now = _faces(**faces_kw)
    gate = _locked_gate(faces, now, voice=voice)
    assert _frame(gate, now, *slots) is expected
    assert gate.last_source == source
    if source == "asd":
        assert gate.row_asd_track == 7


def test_bot_only_rows_are_rejected_and_counted():
    faces, now = _faces(mouth=0.9)
    gate = _locked_gate(faces, now)
    gate.lock.slots[2] = SlotIdentity(bot_sim=0.9)
    assert _frame(gate, now, 2) is R
    assert gate.echo_rejects == 1


def test_other_slot_is_rejected_even_while_the_target_mouth_moves():
    faces, now = _faces(mouth=0.9)  # the customer chews; the TV talks
    gate = _locked_gate(faces, now, voice=True)
    gate.lock.slots[3] = SlotIdentity(voice_sim=0.1)
    assert _frame(gate, now, 3) is R


def test_target_overlap_is_uncertain_and_flags_visible_speech():
    faces, now = _faces(mouth=0.8)
    gate = _locked_gate(faces, now, voice=True)
    gate.lock.slots[3] = SlotIdentity(voice_sim=0.1)
    assert _frame(gate, now, 0, 3, times=3) is U
    assert gate.target_overlap and gate.target_speaking_visibly


def test_overlap_without_the_target_is_rejected():
    faces, now = _faces(mouth=0.8)
    gate = _locked_gate(faces, now, voice=True)
    gate.lock.slots[3] = SlotIdentity(voice_sim=0.1)
    assert _frame(gate, now, 1, 3, times=3) is R
    assert not gate.target_overlap


def test_before_the_lock_it_is_the_audio_only_gate_and_asd_rows_lock_it():
    faces, now = _faces(mouth=0.9, asd=0.9)
    fusion = FusionGate(FUSION, faces, fsm_state=lambda: "active")
    plain = AudioOnlyGate(FUSION, faces=faces)
    for i in range(6):
        t = now + i * 0.01
        expected = plain.frame(_row(0), bot_speaking=False, t=t, asd_stream_id="s")
        assert (
            fusion.frame(_row(0), bot_speaking=False, t=t, asd_stream_id="s")
            is expected
        )
    assert fusion.locked and fusion.lock.target_track == 7


def test_without_asd_evidence_the_lock_never_forms():
    faces, now = _faces(mouth=0.9)  # MAR only: Vision ASD unavailable
    gate = FusionGate(FUSION, faces, fsm_state=lambda: "active")
    for i in range(20):
        gate.frame(_row(0), bot_speaking=False, t=now + i * 0.01, asd_stream_id="s")
    assert not gate.locked


def test_outside_an_active_session_it_never_locks():
    faces, now = _faces(mouth=0.9, asd=0.9)
    gate = FusionGate(FUSION, faces, fsm_state=lambda: "idle")
    for i in range(10):
        gate.frame(_row(0), bot_speaking=False, t=now + i * 0.01, asd_stream_id="s")
    assert not gate.locked
