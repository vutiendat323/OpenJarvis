"""Pure identity layer: voiceprint, slot↔track binder, target lock, fusion gate."""

from __future__ import annotations

import numpy as np
import pytest

from openjarvis.server.voice.speaker_identity import (
    SlotTrackBinder,
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
