"""Who is speaking to the kiosk: settings, verdicts and per-session state.

Pure on purpose: no Pipecat, no models. The Voice pipeline feeds per-frame
verdicts in; the turn strategies and the LLM service read turn verdicts out.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping
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


def _fraction(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"voice_speaker_{key}_must_be_a_number")
    if not 0 < value <= 1:
        raise ValueError(f"voice_speaker_{key}_must_be_in_(0,1]")
    return float(value)


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
    frames = section.get("bargein_accept_frames", defaults.bargein_accept_frames)
    if isinstance(frames, bool) or not isinstance(frames, int) or frames < 1:
        raise ValueError("voice_speaker_bargein_accept_frames_must_be_a_positive_int")

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
        self._counts.clear()
        return verdict


__all__ = [
    "SpeakerSettings",
    "SpeakerTracker",
    "Verdict",
    "load_speaker_settings",
    "turn_verdict",
]
