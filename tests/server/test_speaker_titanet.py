"""Real TitaNet on the local GPU (opt-in: needs NeMo + CUDA; downloads once)."""

from __future__ import annotations

import time

import numpy as np
import pytest

pytestmark = pytest.mark.nvidia

pytest.importorskip("nemo.collections.asr")
torch = pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA not available", allow_module_level=True)

from openjarvis.server.voice.speaker_embedding import TitaNetEmbedder  # noqa: E402


def test_titanet_small_embeds_one_second_quickly():
    embedder = TitaNetEmbedder("titanet_small")
    rng = np.random.default_rng(0)
    pcm = (rng.standard_normal(16000) * 3000).astype(np.int16)
    started = time.monotonic()
    embedding = embedder.embed(pcm)
    elapsed_ms = (time.monotonic() - started) * 1000
    print(f"titanet_small 1 s embed: {elapsed_ms:.1f} ms")
    assert embedding.shape == (192,) and np.isfinite(embedding).all()
    assert elapsed_ms < 100
