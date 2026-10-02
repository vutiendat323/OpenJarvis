"""Bounded observations and live controls for the operator's Vision console.

No model or network calls belong on the audio path. The bridge coalesces
snapshots; only an explicit Save command writes the active TOML preset.
"""

from __future__ import annotations

import math
import os
import tempfile
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import numpy as np

from openjarvis.server.voice.speaker import SpeakerSettings

LIVE_SPEAKER_KEYS = {
    "approach_threshold_m": (0.1, 6.0),
    "anchor_max_m": (0.1, 6.0),
    "mouth_active": (0.01, 1.0),
    "asd_accept_prob": (0.01, 1.0),
    "asd_reject_prob": (0.0, 0.99),
    "voice_match": (0.01, 1.0),
    "voice_reject": (0.01, 0.99),
    "bot_match": (0.01, 1.0),
}


def audio_levels(audio: bytes, *, include_waveform: bool = True) -> dict:
    if len(audio) % 2:
        return {"rms_dbfs": None, "peak": None, "waveform": []}
    samples = np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768.0
    if not len(samples):
        return {"rms_dbfs": -96.0, "peak": 0.0, "waveform": []}
    rms = float(np.sqrt(np.mean(samples * samples)))
    # Signed peaks retain short transients without transmitting microphone PCM.
    stride = max(1, math.ceil(len(samples) / 32))
    wave = []
    if include_waveform:
        for start in range(0, len(samples), stride):
            block = samples[start : start + stride]
            wave.extend((round(float(block.min()), 4), round(float(block.max()), 4)))
    return {
        "rms_dbfs": round(max(-96.0, 20 * math.log10(max(rms, 1e-8))), 1),
        "peak": round(float(np.max(np.abs(samples))), 4),
        "waveform": wave,
    }


def save_speaker_values(values: dict, config_path: str | None = None) -> None:
    import tomlkit

    selected = config_path or os.environ.get("OPENJARVIS_CONFIG", "").strip()
    if not selected:
        raise ValueError("No active OpenJarvis preset to save")
    path = Path(selected).expanduser().resolve(strict=True)
    document = tomlkit.parse(path.read_text())
    section = document.setdefault("voice", tomlkit.table()).setdefault(
        "speaker", tomlkit.table()
    )
    for key, value in values.items():
        if key not in LIVE_SPEAKER_KEYS:
            raise ValueError("Unsupported speaker setting")
        if key == "approach_threshold_m":
            document.setdefault("kiosk", tomlkit.table())[key] = value
        else:
            section[key] = value
    descriptor, temporary = tempfile.mkstemp(prefix=".console-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(tomlkit.dumps(document))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SpeakerConsole:
    def __init__(self, processor, settings: SpeakerSettings, enhancer=None):
        self.processor = processor
        self.settings = settings
        self.enhancer = enhancer
        self._next_publish = 0.0
        self._levels = {"rms_dbfs": -96.0, "peak": 0.0, "waveform": []}
        self._wave: deque[float] = deque(maxlen=128)
        self._verdict = None
        self._verdict_at = None
        self._audio_at = None

    def tune(self, key: str, value) -> dict:
        bounds = LIVE_SPEAKER_KEYS.get(key)
        if bounds is None or type(value) not in (int, float):
            raise ValueError("Unsupported setting or non-numeric value")
        if not math.isfinite(value) or not bounds[0] <= value <= bounds[1]:
            raise ValueError(f"Value must be within {bounds[0]}–{bounds[1]}")
        if key == "approach_threshold_m":
            from openjarvis.kiosk.evaluate import get_config, set_config

            set_config(replace(get_config(), approach_threshold_m=float(value)))
            self.publish(force=True)
            return {"key": key, "value": float(value)}
        updated = replace(self.settings, **{key: float(value)})
        if updated.asd_reject_prob >= updated.asd_accept_prob:
            raise ValueError("ASD reject threshold must be below accept threshold")
        if updated.voice_reject >= updated.voice_match:
            raise ValueError("Voice reject threshold must be below match threshold")
        self.processor._gate.apply_live_settings(updated)
        self.settings = updated
        self.publish(force=True)
        return {"key": key, "value": float(value)}

    def values(self) -> dict:
        from openjarvis.kiosk.evaluate import get_config

        return {key: get_config().approach_threshold_m if key == "approach_threshold_m"
                else getattr(self.settings, key) for key in LIVE_SPEAKER_KEYS}

    def audio(self, audio: bytes) -> None:
        self._levels = audio_levels(audio)
        self._wave.extend(self._levels["waveform"])
        self._audio_at = time.time()
        self.publish()

    def verdict(self, verdict, timestamp: float) -> None:
        self._verdict = verdict.value if verdict is not None else None
        self._verdict_at = timestamp

    def snapshot(self) -> dict:
        p = self.processor
        gate = p._gate
        fusion = p._fusion
        lock = fusion.lock if fusion is not None else None
        detail = dict(gate.last_evidence_detail or {})
        counts = gate.asd_counts
        used = counts["used_accept"] + counts["used_reject"]
        eligible = used + counts["middle"] + counts["late"] + counts["gap"]
        bridge = p._vision_audio
        slots = sorted(lock.target_slots) if lock is not None else []
        similarities = [
            lock.slots[s].voice_sim for s in slots
            if s in lock.slots and lock.slots[s].voice_sim is not None
        ] if lock is not None else []
        enhancer = self.enhancer
        audio = {**self._levels, "waveform": list(self._wave), "ts": self._audio_at}
        audio["raw_rms_dbfs"] = self._levels["rms_dbfs"] if enhancer is None else None
        if enhancer is not None:
            audio["raw_rms_dbfs"] = getattr(enhancer, "raw_rms_dbfs", None)
        return {
            "ts": time.time(),
            "audio": audio,
            "enhancer_requested": self.settings.enhancer,
            "enhancer_effective": getattr(enhancer, "effective_name", "none"),
            "identity": self.settings.identity,
            "verdict": self._verdict,
            "verdict_ts": self._verdict_at,
            "evidence": detail,
            "locked": lock.locked if lock is not None else False,
            "lock_state": lock.state.value if lock is not None else "NONE",
            "target_track": lock.target_track if lock is not None else None,
            "target_slots": slots,
            "voice_ready": lock.voice_ready if lock is not None else False,
            "voice_similarity": max(similarities) if similarities else None,
            "overlap": gate.overlap,
            "bot_speaking": time.monotonic() < p._bot_audible_until,
            "stt_mask": self.settings.stt_mask,
            "tse_ready": p._separator is not None
            and p._enrolled >= p._separator.enroll_samples,
            "stt_masked_no_verdict_frames": p.stt_masked_no_verdict,
            "asd_effective_use": used / eligible if eligible else None,
            "asd_late": getattr(bridge, "asd_late", 0),
            "asd_age_ms": bridge.asd_age_ms[-1] if bridge.asd_age_ms else None,
            "bargein_blocked": getattr(p._tracker, "bargein_blocked", 0),
            "echo_rejects": getattr(fusion, "echo_rejects", 0),
            "settings": self.values(),
            "bounds": {key: list(value) for key, value in LIVE_SPEAKER_KEYS.items()},
        }

    def publish(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now >= self._next_publish:
            self._next_publish = now + 0.1
            self.processor._vision_audio.offer_telemetry(self.snapshot())
