"""Pure identity layer: voiceprint, slot↔track binder, target lock, fusion gate."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from openjarvis.server.voice.speaker import SpeakerSettings
from openjarvis.server.voice.speaker_identity import (
    EmbeddingJob,
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
            [0, 3],
            anchor=7,
            anchor_asd_accept=True,
            target_visible=True,
            target_asd_accept=True,
        )
    assert 3 not in lock.target_slots
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
