"""Who the locked customer is: voiceprint, slot↔face evidence, target lock.

Pure on purpose, like speaker.py: no models, no Pipecat. Everything here runs
on the Voice pipeline's event loop, so nothing locks.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping

import numpy as np

ROW_SECS = 0.08  # one diarizer frame
VOICEPRINT_MAX_EMBEDDINGS = 8
BINDER_HALF_LIFE_SECS = 5.0
# ≈ 1 s of full slot-and-face evidence. Decay is applied per row, so 13 rows
# (1.04 s) sum to 0.974, not 1.04; 0.95 keeps "13 rows map, 12 do not".
BINDER_MIN_EVIDENCE = 0.95
BINDER_DOMINANCE = 2.0
TRACK_FORGET_SECS = 5.0


def unit(vector) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if not math.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding_must_be_finite_and_nonzero")
    return array / norm


def cosine(a, b) -> float:
    return float(np.dot(unit(a), unit(b)))


class Voiceprint:
    """Normalised mean of the newest enrolled embeddings, with their audio length."""

    def __init__(self, max_embeddings: int = VOICEPRINT_MAX_EMBEDDINGS) -> None:
        self._embeddings: deque[tuple[np.ndarray, float]] = deque(maxlen=max_embeddings)

    @property
    def seconds(self) -> float:
        return sum(seconds for _, seconds in self._embeddings)

    def enroll(self, embedding, seconds: float) -> None:
        self._embeddings.append((unit(embedding), float(seconds)))

    def ready(self, min_secs: float) -> bool:
        return self.seconds >= min_secs

    def similarity(self, embedding) -> float | None:
        if not self._embeddings:
            return None
        mean = np.mean([e for e, _ in self._embeddings], axis=0)
        return cosine(mean, embedding)

    def clear(self) -> None:
        self._embeddings.clear()


class SlotTrackBinder:
    """Decaying evidence that diarizer slot s is the voice of face track x.

    Each row adds p(slot) × a(track) × 80 ms, where a is the track's ASD
    probability or MAR. A slot maps to a track holding ≥ 0.95 (≈ 1 s of full
    evidence after decay) and ≥ 2× its runner-up.
    """

    def __init__(self, half_life: float = BINDER_HALF_LIFE_SECS) -> None:
        self._half_life = half_life
        self._evidence: dict[int, dict[int, float]] = {}
        self._seen: dict[int, float] = {}
        self._t: float | None = None

    def update(
        self,
        t: float,
        slot_probs: Mapping[int, float],
        track_scores: Mapping[int, float],
    ) -> None:
        if self._t is not None and t > self._t:
            decay = 0.5 ** ((t - self._t) / self._half_life)
            for row in self._evidence.values():
                for track in row:
                    row[track] *= decay
        if self._t is None or t > self._t:
            self._t = t
        for track in track_scores:
            self._seen[track] = t
        stale = [x for x, seen in self._seen.items() if t - seen > TRACK_FORGET_SECS]
        for track in stale:
            del self._seen[track]
            for row in self._evidence.values():
                row.pop(track, None)
        for slot, p in slot_probs.items():
            row = self._evidence.setdefault(slot, {})
            for track, score in track_scores.items():
                row[track] = row.get(track, 0.0) + p * score * ROW_SECS

    def track_of(self, slot: int) -> int | None:
        ranked = sorted(
            self._evidence.get(slot, {}).items(), key=lambda kv: kv[1], reverse=True
        )
        if not ranked or ranked[0][1] < BINDER_MIN_EVIDENCE:
            return None
        if len(ranked) > 1 and ranked[0][1] < BINDER_DOMINANCE * ranked[1][1]:
            return None
        return ranked[0][0]

    def clear(self) -> None:
        self._evidence.clear()
        self._seen.clear()
        self._t = None
