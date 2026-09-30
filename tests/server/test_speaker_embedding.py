"""Bounded embedding worker and the bot's own voiceprint."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from openjarvis.server.voice.speaker_embedding import (
    BotVoiceprint,
    EmbeddingWorker,
    bot_audio_sink,
    resample_to_16k,
)
from openjarvis.server.voice.speaker_identity import EmbeddingJob


class _Embedder:
    def __init__(self, gate=None, error=None):
        self.calls = []
        self.gate = gate
        self.error = error

    def embed(self, pcm):
        self.calls.append(len(pcm))
        if self.gate is not None:
            self.gate.wait(2.0)
        if self.error is not None:
            raise self.error
        return np.ones(4, np.float32)


def _job(slot, end):
    return EmbeddingJob(epoch=0, slot=slot, segment_end=end, seconds=1.0,
                        pcm=np.zeros(16000, np.int16), target_confirmed=False)


async def _eventually(check):
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    assert check()


@pytest.mark.anyio
async def test_one_running_one_pending_per_slot_newest_wins_oldest_first():
    gate = threading.Event()
    results = []
    worker = EmbeddingWorker(_Embedder(gate), lambda job, emb, ms: results.append(job),
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await asyncio.sleep(0.02)
    worker.submit(_job(1, 1.1))
    worker.submit(_job(2, 1.2))
    worker.submit(_job(1, 1.3))  # replaces slot 1's pending job, keeps its place
    assert worker.pending == 2
    gate.set()
    await _eventually(lambda: len(results) == 3)
    assert [(j.slot, j.segment_end) for j in results] == [(0, 1.0), (1, 1.3), (2, 1.2)]
    assert len(worker.elapsed_ms) == 3
    await worker.close()


@pytest.mark.anyio
async def test_clear_drops_pending_jobs():
    gate = threading.Event()
    results = []
    worker = EmbeddingWorker(_Embedder(gate), lambda job, emb, ms: results.append(job),
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await asyncio.sleep(0.02)
    worker.submit(_job(1, 1.1))
    worker.clear()
    gate.set()
    await asyncio.sleep(0.1)
    assert [j.slot for j in results] == [0]
    await worker.close()


@pytest.mark.anyio
async def test_a_failure_disables_the_embedder_for_the_process():
    embedder = _Embedder(error=RuntimeError("cuda gone"))
    worker = EmbeddingWorker(embedder, lambda *a: None,
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await _eventually(lambda: worker.failed)
    assert embedder.disabled is True
    worker.submit(_job(1, 1.1))
    await asyncio.sleep(0.05)
    assert embedder.calls == [16000]
    await worker.close()


def test_resample_48k_to_16k():
    out = resample_to_16k(np.zeros(48000, np.int16), 48000)
    assert out.dtype == np.int16 and len(out) == 16000
    same = np.arange(100, dtype=np.int16)
    assert resample_to_16k(same, 16000) is same


def _tts(samples, rate=48000, value=1000):
    return (np.ones(samples, np.int16) * value).tobytes()


def test_bot_voiceprint_takes_three_seconds_once():
    bot = BotVoiceprint()
    assert bot.offer(_tts(48000 * 2), 48000, 1) is None
    pcm = bot.offer(_tts(48000 * 2), 48000, 1)
    assert pcm is not None and len(pcm) == 48000
    assert bot.offer(_tts(48000), 48000, 1) is None
    assert BotVoiceprint().offer(_tts(4800), 48000, 2) is None


def test_bot_audio_sink_embeds_the_bot_once():
    bot = BotVoiceprint()
    embedder = _Embedder()
    executor = ThreadPoolExecutor(max_workers=1)
    sink = bot_audio_sink(embedder, bot, executor)
    for _ in range(4):
        sink(_tts(48000), 48000, 1)
    executor.shutdown(wait=True)
    assert embedder.calls == [48000]
    assert bot.embedding is not None
    assert np.linalg.norm(bot.embedding) == pytest.approx(1.0)


@pytest.mark.anyio
async def test_a_consumer_error_drops_the_result_but_keeps_the_worker():
    embedder = _Embedder()
    delivered = []

    def on_result(job, emb, ms):
        if not delivered and job.slot == 0:
            delivered.append(None)
            raise ValueError("consumer bug")
        delivered.append(job)

    worker = EmbeddingWorker(embedder, on_result,
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await _eventually(lambda: len(embedder.calls) == 1 and worker._task is None)
    assert worker.failed is False
    assert getattr(embedder, "disabled", False) is False
    worker.submit(_job(1, 1.1))
    await _eventually(lambda: len(delivered) == 2)
    assert delivered[1].slot == 1
    assert worker.failed is False
    await worker.close()


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [np.array([np.nan, 0, 0, 0], np.float32),
                                 np.zeros(4, np.float32)])
async def test_a_non_finite_embedding_is_dropped_not_a_failure(bad):
    class _Flaky(_Embedder):
        def embed(self, pcm):
            super().embed(pcm)
            return bad if len(self.calls) == 1 else np.ones(4, np.float32)

    embedder = _Flaky()
    results = []
    worker = EmbeddingWorker(embedder, lambda job, emb, ms: results.append(job),
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await _eventually(lambda: len(embedder.calls) == 1 and worker._task is None)
    assert results == [] and worker.failed is False
    assert getattr(embedder, "disabled", False) is False
    worker.submit(_job(1, 1.1))
    await _eventually(lambda: len(results) == 1)
    assert results[0].slot == 1 and worker.failed is False
    await worker.close()
