"""Who is speaking to the kiosk: settings, verdicts and per-session state.

Pure on purpose: no Pipecat, no models. The Voice pipeline feeds per-frame
verdicts in; the turn strategies and the LLM service read turn verdicts out.
"""

from __future__ import annotations

import os
from collections import Counter
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
    diarizer_latency: str = "ultra_low"
    speaker_active_prob: float = 0.5
    overlap_on_frames: int = 3
    overlap_off_frames: int = 4


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
    """Frame verdicts from diarizer probabilities alone (spec §5.4 rules 1, 2, 6).

    Vision fusion (PR-5) replaces the bystander rule; until then audio can
    only say "not the target", which is UNCERTAIN, never REJECT.
    """

    def __init__(self, settings: SpeakerSettings) -> None:
        self._threshold = settings.speaker_active_prob
        self._overlap = OverlapDetector(
            on_frames=settings.overlap_on_frames,
            off_frames=settings.overlap_off_frames,
        )
        self._active_frames: Counter[int] = Counter()
        self._bot_frames: Counter[int] = Counter()
        self.target: int | None = None

    def is_echo(self, slot: int) -> bool:
        heard = self._active_frames[slot]
        return (
            heard >= ECHO_MIN_FRAMES
            and self._bot_frames[slot] / heard >= ECHO_BOT_FRACTION
        )

    def frame(self, probs: Sequence[float], *, bot_speaking: bool) -> Verdict | None:
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
        if not active:
            return None
        if not voices:
            return Verdict.REJECT
        if self.target is None and not bot_speaking and len(voices) == 1:
            self.target = voices[0]
        if self.target not in voices:
            return Verdict.UNCERTAIN
        if overlap:
            return Verdict.UNCERTAIN
        return Verdict.ACCEPT


class SpeakerTracker:
    """One Voice session's speaker evidence, shared by the turn strategies
    and the LLM service. Everything runs on the pipeline's event loop."""

    def __init__(self, settings: SpeakerSettings) -> None:
        self._settings = settings
        self._counts: Counter[Verdict] = Counter()
        self._closed: Verdict | None = None
        self.has_evidence = False

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

    def span_verdict(self) -> Verdict:
        return turn_verdict(
            self._counts,
            accept_fraction=self._settings.accept_turn_fraction,
            reject_fraction=self._settings.reject_turn_fraction,
        )

    def close_turn(self) -> Verdict:
        self._closed = self.span_verdict()
        return self._closed

    def take_turn_verdict(self) -> Verdict:
        """The verdict for the turn the LLM is about to answer, consumed once.

        Falls back to the live count when the turn was finalized without
        ``close_turn`` (Pipecat's stop watchdog).
        """
        verdict = self._closed if self._closed is not None else self.span_verdict()
        self._closed = None
        return verdict


__all__ = [
    "AudioOnlyGate",
    "OverlapDetector",
    "SpeakerSettings",
    "SpeakerTracker",
    "Verdict",
    "load_speaker_settings",
    "turn_verdict",
]
