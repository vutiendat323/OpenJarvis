"""Real streaming Sortformer on the local GPU (opt-in: needs NeMo + CUDA)."""

from __future__ import annotations

import itertools
import math
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.nvidia

nemo_asr = pytest.importorskip("nemo.collections.asr")
torch = pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA not available", allow_module_level=True)

from openjarvis.server.voice.speaker_audio import (  # noqa: E402
    SAMPLE_RATE,
    SortformerDiarizer,
)

# Two speakers, 30 s, with reference RTTM (pyannote-audio tutorial asset, MIT).
_ASSETS = "https://github.com/pyannote/pyannote-audio/raw/develop/tutorials/assets/"
_CACHE = Path.home() / ".cache" / "openjarvis-test-assets"


def _asset(name: str) -> Path:
    path = _CACHE / name
    if not path.exists():
        _CACHE.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(_ASSETS + name, path)
        except OSError as error:
            pytest.skip(f"sample asset unavailable: {error}")
    return path


def _reference(frames: int) -> np.ndarray:
    speakers: dict[str, list[tuple[float, float]]] = {}
    for line in _asset("sample.rttm").read_text().splitlines():
        f = line.split()
        speakers.setdefault(f[7], []).append((float(f[3]), float(f[3]) + float(f[4])))
    truth = np.zeros((frames, len(speakers)), bool)
    for column, segments in enumerate(speakers.values()):
        for start, end in segments:
            truth[int(start / 0.08) : math.ceil(end / 0.08), column] = True
    return truth


def _frame_der(pred: np.ndarray, truth: np.ndarray) -> float:
    errors = min(
        np.logical_xor(pred[:, list(perm)], truth).sum()
        for perm in itertools.permutations(range(pred.shape[1]), truth.shape[1])
    )
    return errors / truth.sum()


def _samples() -> np.ndarray:
    import wave

    with wave.open(str(_asset("sample.wav"))) as wav:
        assert wav.getframerate() == SAMPLE_RATE and wav.getnchannels() == 1
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)


@pytest.mark.parametrize("latency", ["ultra_low", "low"])
def test_streaming_sortformer_is_realtime_and_accurate(latency):
    diarizer = SortformerDiarizer(latency)
    audio = _samples()
    rows, steps = [], []
    for start in range(
        0, len(audio) - diarizer.chunk_samples + 1, diarizer.chunk_samples
    ):
        began = time.perf_counter()
        rows.append(diarizer.push(audio[start : start + diarizer.chunk_samples]))
        steps.append(time.perf_counter() - began)
    probs = np.concatenate(rows)

    budget = diarizer.chunk_samples / SAMPLE_RATE
    assert np.percentile(steps[1:], 95) < budget / 2
    assert _frame_der(probs > 0.5, _reference(len(probs))) < 0.2


def test_reset_starts_a_new_session_with_fresh_slots():
    diarizer = SortformerDiarizer("ultra_low")
    audio = _samples()
    first = np.concatenate(
        [
            diarizer.push(audio[i : i + diarizer.chunk_samples])
            for i in range(0, 20 * diarizer.chunk_samples, diarizer.chunk_samples)
        ]
    )
    diarizer.reset()
    again = np.concatenate(
        [
            diarizer.push(audio[i : i + diarizer.chunk_samples])
            for i in range(0, 20 * diarizer.chunk_samples, diarizer.chunk_samples)
        ]
    )

    np.testing.assert_allclose(first, again, atol=1e-4)


def _replay_module():
    import importlib.util

    path = Path(__file__).parents[2] / "scripts" / "voice_speaker_replay.py"
    spec = importlib.util.spec_from_file_location("voice_speaker_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replay_rejects_the_playback_voice_and_accepts_the_customer():
    from openjarvis.server.voice.speaker import Verdict

    rttm = [line.split() for line in _asset("sample.rttm").read_text().splitlines()]

    def segments(who):
        return [(float(f[3]), float(f[3]) + float(f[4])) for f in rttm if f[7] == who]

    bot, customer = segments("speaker90"), segments("speaker91")
    frames = _replay_module().replay(_asset("sample.wav"), bot, latency="ultra_low")
    t = np.arange(len(frames)) * 0.08

    def within(spans, collar=0.0):
        inside = np.zeros(len(frames), bool)
        for start, end in spans:
            inside |= (t >= start - collar) & (t < end + collar)
        return inside

    accepted = np.array([v is Verdict.ACCEPT for _, v in frames])
    # Spec §9's 0.25 s collar: the diarizer smears a speaker's edges by ~0.2 s,
    # and those edge frames are the customer's own voice, not echo.
    playback_only = within(bot) & ~within(customer, 0.25)
    customer_only = within(customer) & ~within(bot, 0.25)

    assert (accepted & playback_only).sum() == 0
    assert accepted[customer_only].mean() >= 0.9
