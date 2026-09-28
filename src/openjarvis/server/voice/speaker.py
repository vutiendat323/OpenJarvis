"""Who is speaking to the kiosk: settings, verdicts and per-session state.

Pure on purpose: no Pipecat, no models. The Voice pipeline feeds per-frame
verdicts in; the turn strategies and the LLM service read turn verdicts out.
"""

from __future__ import annotations

import os
import threading
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import tomllib


class Verdict(str, Enum):
    """The target-speaker gate's answer for a frame or a turn."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class SpeakerSettings:
    """The ``[voice.speaker]`` preset section. Off by default."""

    enabled: bool = False
    uncertain_allowed_tools: tuple[str, ...] = ("display_menu",)
    bargein_accept_frames: int = 3
    accept_turn_fraction: float = 0.7
    reject_turn_fraction: float = 0.7
    diarizer: str = "none"
    enhancer: str = "none"
    diarizer_latency: str = "ultra_low"
    speaker_active_prob: float = 0.5
    overlap_on_frames: int = 3
    overlap_off_frames: int = 4
    vision_faces: bool = False
    mouth_active: float = 0.5
    anchor_max_m: float = 1.5
    stt_mask: bool = False
    stt_mask_delay_secs: float = 0.5
    separator: str = "none"
    separator_model: str = "~/.cache/openjarvis/tse/bsrnn_spk_emb_100.ts"


def _fraction(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"voice_speaker_{key}_must_be_a_number")
    if not 0 < value <= 1:
        raise ValueError(f"voice_speaker_{key}_must_be_in_(0,1]")
    return float(value)


def _positive_int(section: Mapping[str, Any], key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"voice_speaker_{key}_must_be_a_positive_int")
    return value


def _positive_float(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"voice_speaker_{key}_must_be_a_positive_number")
    return float(value)


def _boolean(section: Mapping[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"voice_speaker_{key}_must_be_a_boolean")
    return value


def _choice(
    section: Mapping[str, Any], key: str, default: str, choices: tuple[str, ...]
) -> str:
    value = section.get(key, default)
    if value not in choices:
        raise ValueError(f"voice_speaker_{key}_must_be_one_of_{'|'.join(choices)}")
    return value


def load_speaker_settings() -> SpeakerSettings:
    """Load optional ``[voice.speaker]`` settings from the active preset."""
    config_value = os.environ.get("OPENJARVIS_CONFIG", "").strip()
    if not config_value:
        return SpeakerSettings()
    config_path = Path(config_value).expanduser()
    if not config_path.exists():
        return SpeakerSettings()
    with config_path.open("rb") as config_file:
        data = tomllib.load(config_file)

    voice = data.get("voice", {})
    if not isinstance(voice, Mapping):
        raise ValueError("voice_config_must_be_a_table")
    section = voice.get("speaker", {})
    if not isinstance(section, Mapping):
        raise ValueError("voice_speaker_config_must_be_a_table")

    defaults = SpeakerSettings()
    enabled = section.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ValueError("voice_speaker_enabled_must_be_a_boolean")
    tools = section.get(
        "uncertain_allowed_tools", list(defaults.uncertain_allowed_tools)
    )
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError("voice_speaker_uncertain_allowed_tools_must_be_a_string_list")
    frames = _positive_int(
        section, "bargein_accept_frames", defaults.bargein_accept_frames
    )

    stt_mask = _boolean(section, "stt_mask", defaults.stt_mask)
    separator = _choice(section, "separator", defaults.separator, ("none", "tse"))
    if separator != "none" and not stt_mask:
        # Separation holds overlapped audio in the delayed STT copy.
        raise ValueError("voice_speaker_separator_needs_stt_mask")
    separator_model = section.get("separator_model", defaults.separator_model)
    if not isinstance(separator_model, str) or not separator_model.strip():
        raise ValueError("voice_speaker_separator_model_must_be_a_path")

    return SpeakerSettings(
        enabled=enabled,
        uncertain_allowed_tools=tuple(
            dict.fromkeys(t.strip() for t in tools if t.strip())
        ),
        bargein_accept_frames=frames,
        accept_turn_fraction=_fraction(
            section, "accept_turn_fraction", defaults.accept_turn_fraction
        ),
        reject_turn_fraction=_fraction(
            section, "reject_turn_fraction", defaults.reject_turn_fraction
        ),
        diarizer=_choice(
            section, "diarizer", defaults.diarizer, ("none", "sortformer")
        ),
        enhancer=_choice(section, "enhancer", defaults.enhancer, ("none", "rnnoise")),
        diarizer_latency=_choice(
            section, "diarizer_latency", defaults.diarizer_latency, ("ultra_low", "low")
        ),
        speaker_active_prob=_fraction(
            section, "speaker_active_prob", defaults.speaker_active_prob
        ),
        overlap_on_frames=_positive_int(
            section, "overlap_on_frames", defaults.overlap_on_frames
        ),
        overlap_off_frames=_positive_int(
            section, "overlap_off_frames", defaults.overlap_off_frames
        ),
        vision_faces=_boolean(section, "vision_faces", defaults.vision_faces),
        mouth_active=_fraction(section, "mouth_active", defaults.mouth_active),
        anchor_max_m=_positive_float(section, "anchor_max_m", defaults.anchor_max_m),
        stt_mask=stt_mask,
        stt_mask_delay_secs=_positive_float(
            section, "stt_mask_delay_secs", defaults.stt_mask_delay_secs
        ),
        separator=separator,
        separator_model=separator_model,
    )


def turn_verdict(
    counts: Mapping[Verdict, int], *, accept_fraction: float, reject_fraction: float
) -> Verdict:
    """Fold a turn's frame verdicts into one.

    No frames means no diarizer evidence, which is today's behaviour: accept.
    """
    total = sum(counts.values())
    if total == 0:
        return Verdict.ACCEPT
    accepted = counts.get(Verdict.ACCEPT, 0) / total
    rejected = counts.get(Verdict.REJECT, 0) / total
    if rejected >= reject_fraction:
        return Verdict.REJECT
    if accepted >= accept_fraction and rejected < 0.2:
        return Verdict.ACCEPT
    return Verdict.UNCERTAIN


# A slot is judged as echo only after 2 s of its speech, most of it heard
# while Jarvis was playing: the TTS voice diarizes as a speaker of its own.
ECHO_MIN_FRAMES = 25
ECHO_BOT_FRACTION = 0.8
# Audio alone cannot tell the customer from a bystander, so the target follows
# whoever holds the floor alone for 0.5 s while Jarvis is silent. Without this,
# a bystander who spoke first would lock the customer out for the session.
# ponytail: audio-only heuristic; vision fusion (PR-5) replaces it.
FLOOR_FRAMES = 6
# Vision samples mouths at 10 Hz and scores the last 0.5 s, so an audio frame
# is judged by the customer's mouth within 0.3 s either side of it.
VISION_WINDOW_SECS = 0.3


class OverlapDetector:
    """Two or more voices at once, with hysteresis so 80 ms spikes don't count."""

    def __init__(self, *, on_frames: int, off_frames: int) -> None:
        self._on_frames = on_frames
        self._off_frames = off_frames
        self._run = 0
        self.active = False

    def update(self, n_voices: int) -> bool:
        if (n_voices >= 2) != self.active:
            self._run += 1
        else:
            self._run = 0
        if self._run >= (self._off_frames if self.active else self._on_frames):
            self.active = not self.active
            self._run = 0
        return self.active


class AudioOnlyGate:
    """Frame verdicts from diarizer probabilities (spec §5.4).

    With fresh Vision face tracks, speech counts only while the engaged
    customer's mouth moves; anything else heard is REJECTed. Without them,
    audio alone can only say "not the target", which is UNCERTAIN.
    """

    def __init__(
        self, settings: SpeakerSettings, faces: FaceTrackBuffer | None = None
    ) -> None:
        self._faces = faces
        self._mouth_active = settings.mouth_active
        self._anchor_max_m = settings.anchor_max_m
        self._threshold = settings.speaker_active_prob
        self._overlap = OverlapDetector(
            on_frames=settings.overlap_on_frames,
            off_frames=settings.overlap_off_frames,
        )
        self._active_frames: Counter[int] = Counter()
        self._bot_frames: Counter[int] = Counter()
        self.target: int | None = None
        # (anchor track, mouth activity) behind the last vision verdict, for logs.
        self.last_evidence: tuple[int | None, float | None] | None = None
        self._floor_slot: int | None = None
        self._floor_run = 0

    @property
    def overlap(self) -> bool:
        return self._overlap.active

    def is_echo(self, slot: int) -> bool:
        heard = self._active_frames[slot]
        return (
            heard >= ECHO_MIN_FRAMES
            and self._bot_frames[slot] / heard >= ECHO_BOT_FRACTION
        )

    def frame(
        self, probs: Sequence[float], *, bot_speaking: bool, t: float | None = None
    ) -> Verdict | None:
        active = [slot for slot, p in enumerate(probs) if p >= self._threshold]
        for slot in active:
            self._active_frames[slot] += 1
            if bot_speaking:
                self._bot_frames[slot] += 1
        voices = [slot for slot in active if not self.is_echo(slot)]
        # A playback tail longer than the processor's can bind Jarvis's own
        # voice; once that slot proves to be echo, let the customer rebind.
        if self.target is not None and self.is_echo(self.target):
            self.target = None
        overlap = self._overlap.update(len(voices))
        self.last_evidence = None
        if not active:
            return None
        if not voices:
            return Verdict.REJECT
        if not bot_speaking and len(voices) == 1:
            if voices[0] == self._floor_slot:
                self._floor_run += 1
            else:
                self._floor_slot, self._floor_run = voices[0], 1
            if self.target is None or self._floor_run >= FLOOR_FRAMES:
                self.target = voices[0]
        else:
            self._floor_slot, self._floor_run = None, 0
        if self._faces is not None and t is not None and self._faces.fresh(t):
            anchor = self._faces.anchor(t, self._anchor_max_m)
            mouth = (
                self._faces.mouth(
                    anchor, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS
                )
                if anchor is not None
                else None
            )
            self.last_evidence = (anchor, mouth)
            if overlap or anchor is None:
                return Verdict.UNCERTAIN
            if mouth is not None and mouth >= self._mouth_active:
                return Verdict.ACCEPT
            return Verdict.REJECT
        if self.target not in voices:
            return Verdict.UNCERTAIN
        if overlap:
            return Verdict.UNCERTAIN
        return Verdict.ACCEPT


class FaceTrackBuffer:
    """The last few seconds of Vision's ``faces`` events (spec §5.2)."""

    def __init__(self, history_s: float = 3.0) -> None:
        self._history = history_s
        self._events: deque[dict] = deque()
        self._lock = threading.Lock()

    def add(self, event: dict) -> None:
        with self._lock:
            self._events.append(event)
            newest = event.get("ts", 0.0)
            while (
                self._events and self._events[0].get("ts", 0.0) < newest - self._history
            ):
                self._events.popleft()

    def fresh(self, now: float, max_age: float = 1.0) -> bool:
        with self._lock:
            return bool(self._events) and now - self._events[-1]["ts"] <= max_age

    def anchor(self, t: float, max_m: float) -> int | None:
        with self._lock:
            if not self._events:
                return None
            event = min(self._events, key=lambda e: abs(e["ts"] - t))
        near = [
            (f["distance_m"], f["track_id"])
            for f in event.get("tracks", ())
            if f.get("distance_m") is not None and f["distance_m"] <= max_m
        ]
        return min(near)[1] if near else None

    def mouth(self, track_id: int, t0: float, t1: float) -> float | None:
        with self._lock:
            values = [
                f["mouth_activity"]
                for e in self._events
                if t0 <= e["ts"] <= t1
                for f in e.get("tracks", ())
                if f["track_id"] == track_id and f.get("mouth_activity") is not None
            ]
        # Mean, not max: one noisy sample (a glance, a nod) must not read as
        # the customer talking for the whole window.
        return sum(values) / len(values) if values else None


class SpeakerTracker:
    """One Voice session's speaker evidence, shared by the turn strategies
    and the LLM service. Everything runs on the pipeline's event loop."""

    def __init__(self, settings: SpeakerSettings, *, diarized: bool = False) -> None:
        self._settings = settings
        self._diarized = diarized
        self._counts: Counter[Verdict] = Counter()
        self._closed: Verdict | None = None
        # With a diarizer, every decision waits for its evidence from the
        # first frame of the session; without one, the legacy behaviour.
        self.has_evidence = diarized

    def record(self, verdict: Verdict) -> None:
        self.has_evidence = True
        self._counts[verdict] += 1

    def begin_span(self) -> None:
        """Speech started outside a turn: its frames start a fresh count."""
        self._counts.clear()

    def begin_turn(self) -> None:
        """A turn opened. A closed verdict nobody took is stale: its turn had
        no transcript, or this turn's interruption dropped its LLM frame."""
        self._closed = None

    def span_verdict(self, counts: Mapping[Verdict, int] | None = None) -> Verdict:
        counts = self._counts if counts is None else counts
        if self._diarized and not counts:
            # The diarizer ran but never confirmed this speech (it lagged, or
            # placed nobody): not the customer's word.
            return Verdict.UNCERTAIN
        return turn_verdict(
            counts,
            accept_fraction=self._settings.accept_turn_fraction,
            reject_fraction=self._settings.reject_turn_fraction,
        )

    def _turn_verdict(self) -> Verdict:
        """The finished turn's verdict, from the speech its transcript holds.

        With ``stt_mask``, REJECTed audio reached Gemini as silence, so it put
        no words in the transcript: only the other frames decide. Live
        2026-09-26: a phone video between the customer's words turned their
        turns UNCERTAIN, or REJECTed and dropped them. A turn of nothing but
        rejected speech stays REJECT. Turn starts still count every frame.
        """
        counts = self._counts
        if self._settings.stt_mask and (
            counts[Verdict.ACCEPT] or counts[Verdict.UNCERTAIN]
        ):
            counts = Counter(
                {v: n for v, n in counts.items() if v is not Verdict.REJECT}
            )
        return self.span_verdict(counts)

    def close_turn(self) -> Verdict:
        self._closed = self._turn_verdict()
        return self._closed

    def frame_counts(self) -> dict[str, int]:
        """This span's frame verdicts, for logs."""
        return {verdict.value: n for verdict, n in self._counts.items()}

    def take_turn_verdict(self) -> Verdict:
        """The verdict for the turn the LLM is about to answer, consumed once.

        Falls back to the live count when the turn was finalized without
        ``close_turn`` (Pipecat's stop watchdog).
        """
        verdict = self._closed if self._closed is not None else self._turn_verdict()
        self._closed = None
        return verdict


__all__ = [
    "AudioOnlyGate",
    "FaceTrackBuffer",
    "OverlapDetector",
    "SpeakerSettings",
    "SpeakerTracker",
    "Verdict",
    "load_speaker_settings",
    "turn_verdict",
]
