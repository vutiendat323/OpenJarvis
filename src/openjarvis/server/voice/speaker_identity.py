"""Who the locked customer is: voiceprint, slot↔face evidence, target lock.

Pure on purpose, like speaker.py: no models, no Pipecat. Everything here runs
on the Voice pipeline's event loop, so nothing locks.
"""

from __future__ import annotations

import math
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np

from openjarvis.server.voice.speaker import (
    VISION_WINDOW_SECS,
    AudioOnlyGate,
    FaceTrackBuffer,
    SpeakerSettings,
    Verdict,
)

ROW_SECS = 0.08  # one diarizer frame
VOICEPRINT_MAX_EMBEDDINGS = 8
BINDER_HALF_LIFE_SECS = 5.0
BINDER_MIN_EVIDENCE = 1.0  # ≈ 1 s of full slot-and-face evidence
BINDER_DOMINANCE = 2.0
TRACK_FORGET_SECS = 5.0
LOCK_WINDOW_SECS = 3.0
TRACK_ABSENT_SECS = 0.5


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
    probability or MAR. A slot maps to a track holding ≥ 1.0 (≈ 1 s of full
    evidence) and ≥ 2× its runner-up.
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


class Label(str, Enum):
    TARGET = "TARGET"
    BOT = "BOT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class LockState(str, Enum):
    NONE = "NONE"
    PRE_LOCK = "PRE_LOCK"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class EmbeddingJob:
    """One slot's solo speech, sent to the embedder with what it belongs to."""

    epoch: int
    slot: int
    segment_end: float
    seconds: float
    pcm: np.ndarray
    target_confirmed: bool


@dataclass
class SlotIdentity:
    voice_sim: float | None = None
    bot_sim: float | None = None
    segment_end: float = -math.inf


@dataclass(frozen=True)
class LockEvent:
    event: str
    epoch: int
    track: int | None
    slots: tuple[int, ...]
    detail: str = ""

    def fields(self) -> str:
        return (
            f"event={self.event} epoch={self.epoch} track={self.track} "
            f"slots={list(self.slots)} {self.detail}"
        ).rstrip()


def _within(times: deque[float], t: float) -> int:
    """Record *t* and count the records inside the lock window ending at it."""
    times.append(t)
    while times and times[0] < t - LOCK_WINDOW_SECS:
        times.popleft()
    return len(times)


class TargetLock:
    """One kiosk session's locked customer (spec §5.1).

    NONE outside the FSM's ``active`` state; PRE_LOCK until Light-ASD confirms
    one face and one diarizer slot together; LOCKED until the FSM leaves
    ``active``. A face standing nearer never takes the lock.
    """

    def __init__(self, settings: SpeakerSettings) -> None:
        self._s = settings
        self.state = LockState.NONE
        self.epoch = 0
        self.target_track: int | None = None
        self.target_slots: set[int] = set()
        self.voiceprint = Voiceprint()
        self.binder = SlotTrackBinder()
        self.slots: dict[int, SlotIdentity] = {}
        self.bot_voiceprint: np.ndarray | None = None
        self.lock_count = 0
        self.last_locked_after_s: float | None = None
        self.rebinds: Counter[str] = Counter()
        self._pre_lock_at: float | None = None
        self._track_seen_at: float | None = None
        self._pairs: dict[tuple[int, int], deque[float]] = {}
        self._slot_asd: dict[int, deque[float]] = {}
        self._candidates: dict[int, deque[float]] = {}
        self._events: list[LockEvent] = []

    @property
    def locked(self) -> bool:
        return self.state is LockState.LOCKED

    @property
    def voice_ready(self) -> bool:
        return self.voiceprint.ready(self._s.voiceprint_min_secs)

    def drain_events(self) -> list[LockEvent]:
        events, self._events = self._events, []
        return events

    def observe_fsm(self, fsm_state: str, t: float) -> None:
        if fsm_state == "active":
            if self.state is LockState.NONE:
                self.state = LockState.PRE_LOCK
                self._pre_lock_at = t
            return
        if self.state is LockState.NONE:
            return
        released = self.locked
        self._reset()
        if released:
            self._events.append(
                LockEvent("release", self.epoch, None, (), f"reason=fsm_{fsm_state}")
            )

    def _reset(self) -> None:
        self.epoch += 1
        self.state = LockState.NONE
        self.target_track = None
        self.target_slots = set()
        self.voiceprint.clear()
        self.binder.clear()
        self.slots.clear()
        self._pre_lock_at = None
        self._track_seen_at = None
        self._pairs.clear()
        self._slot_asd.clear()
        self._candidates.clear()

    def observe_row(
        self,
        t: float,
        voices: Sequence[int],
        *,
        anchor: int | None,
        anchor_asd_accept: bool,
        target_visible: bool = False,
        target_asd_accept: bool = False,
    ) -> None:
        need = self._s.lock_asd_frames
        if self.state is LockState.PRE_LOCK:
            if anchor is not None and anchor_asd_accept and len(voices) == 1:
                pair = (anchor, voices[0])
                count = _within(self._pairs.setdefault(pair, deque()), t)
                if count >= need:
                    self._lock(t, anchor, voices[0], count)
            return
        if not self.locked:
            return
        if target_visible:
            self._track_seen_at = t
        if (
            target_asd_accept
            and len(voices) == 1
            and voices[0] not in self.target_slots
            and not self._voice_rejected(voices[0])
        ):
            slot = voices[0]
            if _within(self._slot_asd.setdefault(slot, deque()), t) >= need:
                self._add_slot(slot, "asd")
        if (
            self.target_absent(t)
            and anchor is not None
            and anchor != self.target_track
            and anchor_asd_accept
            and self.voice_ready
            and any(s in self.target_slots for s in voices)
        ):
            if _within(self._candidates.setdefault(anchor, deque()), t) >= need:
                old = self.target_track
                self.target_track = anchor
                self._track_seen_at = t
                self._candidates.clear()
                self.rebinds["track"] += 1
                self._events.append(
                    LockEvent(
                        "rebind_track",
                        self.epoch,
                        anchor,
                        tuple(sorted(self.target_slots)),
                        f"from={old}",
                    )
                )

    def _lock(self, t: float, track: int, slot: int, frames: int) -> None:
        self.state = LockState.LOCKED
        self.target_track, self.target_slots = track, {slot}
        self._track_seen_at = t
        self._pairs.clear()
        self.lock_count += 1
        after = t - self._pre_lock_at if self._pre_lock_at is not None else 0.0
        self.last_locked_after_s = after
        self._events.append(
            LockEvent(
                "lock",
                self.epoch,
                track,
                (slot,),
                f"asd_frames={frames} after_s={after:.2f}",
            )
        )

    def _add_slot(self, slot: int, reason: str) -> None:
        self.target_slots.add(slot)
        self.rebinds["slot"] += 1
        self._events.append(
            LockEvent(
                "rebind_slot",
                self.epoch,
                self.target_track,
                tuple(sorted(self.target_slots)),
                f"slot={slot} reason={reason}",
            )
        )

    def target_absent(self, t: float) -> bool:
        return (
            self._track_seen_at is None or t - self._track_seen_at > TRACK_ABSENT_SECS
        )

    def desired_pin(self, t: float) -> int | None:
        """The track Vision's ASD should stay on; None lets it pick the nearest."""
        if not self.locked or self.target_absent(t):
            return None
        return self.target_track

    def observe_embedding(self, job: EmbeddingJob, embedding) -> bool:
        """Apply one embedding result; False when it is stale."""
        if job.epoch != self.epoch:
            return False
        ident = self.slots.setdefault(job.slot, SlotIdentity())
        if job.segment_end <= ident.segment_end:
            return False
        ident.segment_end = job.segment_end
        ident.voice_sim = self.voiceprint.similarity(embedding)
        ident.bot_sim = (
            None
            if self.bot_voiceprint is None
            else cosine(self.bot_voiceprint, embedding)
        )
        if self.locked and job.target_confirmed:
            self.voiceprint.enroll(embedding, job.seconds)
        if (
            self.locked
            and job.slot not in self.target_slots
            and self.voice_ready
            and ident.voice_sim is not None
            and ident.voice_sim >= self._s.voice_match
        ):
            self._add_slot(job.slot, "voice")
        return True

    def label(self, slot: int, *, bot_echo: bool = False) -> Label:
        ident = self.slots.get(slot)
        if bot_echo or (
            ident is not None
            and ident.bot_sim is not None
            and ident.bot_sim >= self._s.bot_match
        ):
            return Label.BOT
        if slot in self.target_slots:
            return Label.TARGET
        if self._voice_rejected(slot):
            return Label.OTHER
        track = self.binder.track_of(slot)
        if self.locked and track is not None and track != self.target_track:
            return Label.OTHER
        return Label.UNKNOWN

    def _voice_rejected(self, slot: int) -> bool:
        ident = self.slots.get(slot)
        return (
            self.voice_ready
            and ident is not None
            and ident.voice_sim is not None
            and ident.voice_sim < self._s.voice_reject
        )

    def voice_ok(self, slot: int) -> bool:
        ident = self.slots.get(slot)
        return (
            self.voice_ready
            and ident is not None
            and ident.voice_sim is not None
            and ident.voice_sim >= self._s.voice_match
        )


class FusionGate(AudioOnlyGate):
    """The target-speaker gate with a locked customer (spec §5).

    Before the lock it is the AudioOnlyGate, plus bot-voiceprint echo. Once
    Light-ASD has confirmed one face and one diarizer slot together, a row's
    verdict follows who each active slot is, not whose mouth moves nearest.
    """

    def __init__(
        self,
        settings: SpeakerSettings,
        faces: FaceTrackBuffer | None,
        *,
        fsm_state: Callable[[], str],
        bot_voiceprint: Callable[[], np.ndarray | None] = lambda: None,
    ) -> None:
        super().__init__(settings, faces=faces)
        self.settings = settings
        self.lock = TargetLock(settings)
        self._fsm_state = fsm_state
        self._bot_voiceprint = bot_voiceprint
        self.row_voices: list[int] = []
        self.row_asd_track: int | None = None
        self.row_is_target = False
        self.target_overlap = False
        self.target_speaking_visibly = False
        self.last_source: str | None = None
        self.echo_rejects = 0

    @property
    def locked(self) -> bool:
        return self.lock.locked

    def frame(
        self,
        probs: Sequence[float],
        *,
        bot_speaking: bool,
        t: float | None = None,
        asd_stream_id: str | None = None,
    ) -> Verdict | None:
        t = time.time() if t is None else t
        self.lock.observe_fsm(self._fsm_state(), t)
        self.lock.bot_voiceprint = self._bot_voiceprint()
        self.row_voices, self.row_asd_track, self.row_is_target = [], None, False
        self.target_overlap = self.target_speaking_visibly = False
        self.last_source = None
        if self.lock.locked:
            verdict = self._locked_frame(probs, bot_speaking, t, asd_stream_id)
        else:
            verdict = self._pre_lock_frame(probs, bot_speaking, t, asd_stream_id)
        if (
            verdict is Verdict.REJECT
            and not self.row_voices
            and any(p >= self._threshold for p in probs)
        ):
            self.echo_rejects += 1
        return verdict

    def _pre_lock_frame(self, probs, bot_speaking, t, stream_id):
        verdict = super().frame(
            probs, bot_speaking=bot_speaking, t=t, asd_stream_id=stream_id
        )
        active = [s for s, p in enumerate(probs) if p >= self._threshold]
        self.row_voices = [
            s
            for s in active
            if self.lock.label(s, bot_echo=self.is_echo(s)) is not Label.BOT
        ]
        if active and not self.row_voices:
            verdict = Verdict.REJECT  # the bot's own voice, known by its voiceprint
        detail = self.last_evidence_detail or {}
        anchor = detail.get("track_id")
        confirmed = verdict is Verdict.ACCEPT and detail.get("source") == "asd"
        if verdict is Verdict.ACCEPT:
            self.last_source = detail.get("source")
        if confirmed:
            self.row_asd_track = anchor
        self._update_binder(t, probs, anchor, detail.get("probability"))
        if self.lock.state is LockState.PRE_LOCK:
            self.lock.observe_row(
                t, self.row_voices, anchor=anchor, anchor_asd_accept=confirmed
            )
        return verdict

    def _locked_frame(self, probs, bot_speaking, t, stream_id):
        lock = self.lock
        active = [s for s, p in enumerate(probs) if p >= self._threshold]
        for s in active:
            self._active_frames[s] += 1
            if bot_speaking:
                self._bot_frames[s] += 1
        labels = {s: lock.label(s, bot_echo=self.is_echo(s)) for s in active}
        voices = [s for s in active if labels[s] is not Label.BOT]
        self.row_voices = voices
        overlap = self._overlap.update(len(voices))
        visible, asd, mouth = self._target_evidence(t, stream_id)
        asd_ok = asd is not None and asd >= self._asd_accept
        mouth_ok = mouth is not None and mouth >= self._mouth_active
        anchor, anchor_ok = (
            self._rebind_evidence(t, stream_id)
            if lock.target_absent(t)
            else (None, False)
        )
        lock.observe_row(
            t,
            voices,
            anchor=anchor,
            anchor_asd_accept=anchor_ok,
            target_visible=visible,
            target_asd_accept=asd_ok,
        )
        self._update_binder(t, probs, lock.target_track, asd)
        targets = [s for s in voices if labels[s] is Label.TARGET]
        self.last_evidence = (lock.target_track, mouth)
        self.last_evidence_detail = {
            "source": None,
            "t0": t - ROW_SECS,
            "t1": t,
            "stream_id": stream_id,
            "track_id": lock.target_track,
            "probability": asd,
            "mouth": mouth,
            "labels": {s: labels[s].value for s in active},
            "fallback_reason": None,
        }
        if targets and self.vision_asd and stream_id is not None and visible:
            self._count_asd(asd)
        if not active:
            return None
        if not voices:
            return Verdict.REJECT
        if targets and overlap:
            self.target_overlap = True
            self.target_speaking_visibly = visible and (asd_ok or mouth_ok)
            return Verdict.UNCERTAIN
        if targets:
            self.row_is_target = True
            return self._target_verdict(targets[0], visible, asd_ok, mouth_ok)
        if overlap or all(labels[s] is Label.OTHER for s in voices):
            return Verdict.REJECT
        if asd_ok:
            # An UNKNOWN slot while the customer's face speaks: Sortformer gave
            # them a new slot. The binder learns it from this row.
            return self._accept("asd")
        return Verdict.UNCERTAIN

    def _target_verdict(self, slot, visible, asd_ok, mouth_ok):
        if visible:
            if asd_ok:
                return self._accept("asd")
            if mouth_ok:
                return self._accept("mar")
            if self.lock.voice_ok(slot):
                return self._accept("voice")  # MAR missed: hand or cup at the mouth
            return Verdict.REJECT
        if self.lock.voice_ok(slot):
            return self._accept("voice")
        return Verdict.UNCERTAIN

    def _accept(self, source: str) -> Verdict:
        self.last_source = source
        self.last_evidence_detail["source"] = source
        if source == "asd":
            self.row_asd_track = self.lock.target_track
        return Verdict.ACCEPT

    def _target_evidence(self, t, stream_id):
        """(visible, ASD probability, mouth) of the locked customer at row time t."""
        track, faces = self.lock.target_track, self._faces
        if (
            faces is None
            or track is None
            or not faces.fresh(t)
            or not faces.present(track, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS)
        ):
            return False, None, None
        asd = (
            self._asd_probability(track, t, stream_id)
            if self.vision_asd and stream_id is not None
            else None
        )
        mouth = faces.mouth(track, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS)
        return (asd is not None or mouth is not None), asd, mouth

    def _rebind_evidence(self, t, stream_id):
        """The nearest face and whether ASD hears it (Vision runs ASD on it
        while the locked customer is unpinned)."""
        faces = self._faces
        if faces is None or not faces.fresh(t):
            return None, False
        anchor = faces.anchor(t, self._anchor_max_m)
        if (
            anchor is None
            or anchor == self.lock.target_track
            or stream_id is None
            or not self.vision_asd
        ):
            return anchor, False
        probability = self._asd_probability(anchor, t, stream_id)
        return anchor, probability is not None and probability >= self._asd_accept

    def _update_binder(self, t, probs, asd_track, asd_probability) -> None:
        if self._faces is None or not self.row_voices:
            return
        scores: dict[int, float] = {}
        for track in self._faces.tracks_at(t):
            x = track.get("track_id")
            if isinstance(x, bool) or not isinstance(x, int):
                continue
            if x == asd_track and asd_probability is not None:
                scores[x] = float(asd_probability)
            else:
                mouth = self._faces.mouth(
                    x, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS
                )
                scores[x] = 0.0 if mouth is None else float(mouth)
        self.lock.binder.update(
            t, {s: float(probs[s]) for s in self.row_voices}, scores
        )

    def _count_asd(self, asd: float | None) -> None:
        if asd is None:
            self.asd_counts[self.asd_row_reason or "gap"] += 1
        elif asd >= self._asd_accept:
            self.asd_counts["used_accept"] += 1
        elif asd <= self._asd_reject:
            self.asd_counts["used_reject"] += 1
        else:
            self.asd_counts["middle"] += 1
