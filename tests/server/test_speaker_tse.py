"""Real TSE separator on the local GPU (opt-in: needs the exported model + CUDA).

Export it first with ``scripts/voice_tse_export.py``.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.nvidia

torch = pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA not available", allow_module_level=True)

from openjarvis.server.voice.speaker import SpeakerSettings  # noqa: E402
from openjarvis.server.voice.speaker_audio import (  # noqa: E402
    SAMPLE_RATE,
    TseSeparator,
)

_MODEL = Path(SpeakerSettings().separator_model).expanduser()
# pyannote-audio tutorial asset (MIT), fetched by test_speaker_sortformer.
_SAMPLE = Path.home() / ".cache" / "openjarvis-test-assets" / "sample.wav"


def _si_snr(est: np.ndarray, ref: np.ndarray) -> float:
    est, ref = est - est.mean(), ref - ref.mean()
    proj = (est @ ref) / (ref @ ref) * ref
    return float(10 * np.log10((proj @ proj) / ((est - proj) @ (est - proj))))


def test_separator_keeps_the_enrolled_speaker_intact():
    """Runs on the GPU in time and passes the customer alone through unharmed.

    Extraction quality from a mix is not asserted: this Libri2Mix-trained
    checkpoint is erratic on far-field speech (spec §11, 2026-09-26 spike).
    """
    if not _MODEL.exists() or not _SAMPLE.exists():
        pytest.skip("exported TSE model or sample asset missing")
    with wave.open(str(_SAMPLE)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16) / 32768.0

    def seg(a: float, b: float) -> np.ndarray:
        return audio[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)].astype(np.float32)

    # speaker91 alone: 4 s to pass through, 3 s of other solo speech to enroll.
    target, enroll = seg(21.8, 25.8), seg(14.8, 17.8)
    separator = TseSeparator(str(_MODEL))
    separator.separate(target, enroll)  # warm-up

    started = time.monotonic()
    out = separator.separate(target, enroll)
    elapsed = time.monotonic() - started

    assert out.shape == target.shape and np.isfinite(out).all()
    assert _si_snr(out, target) > 15.0
    assert elapsed < 1.0
