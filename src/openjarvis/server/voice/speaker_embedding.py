"""Voice speaker embeddings: the model, a bounded worker, the bot's voiceprint.

Model packages (NeMo, torch) are imported only inside TitaNetEmbedder: the
``voice-speaker`` extra is optional, like the diarizer's.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Protocol

import numpy as np
from loguru import logger

from openjarvis.server.voice.speaker_identity import EmbeddingJob, unit

SAMPLE_RATE = 16_000
BOT_ENROLL_SECS = 3.0
_MAX_TIMINGS = 512

# One GPU model, one thread, apart from the diarizer's so Sortformer never
# waits behind an embedding.
EMBEDDING_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedder")


class SpeakerEmbedder(Protocol):
    def embed(self, pcm: np.ndarray) -> np.ndarray:
        """int16 mono 16 kHz -> one float32 speaker embedding."""
        ...


class TitaNetEmbedder:
    """NeMo TitaNet (192-d). Not thread-safe; EMBEDDING_EXECUTOR serialises it."""

    def __init__(
        self, model_name: str = "titanet_small", *, device: str = "cuda"
    ) -> None:
        import torch
        from nemo.collections.asr.models import EncDecSpeakerLabelModel

        self._torch = torch
        self._model = EncDecSpeakerLabelModel.from_pretrained(
            model_name, map_location=device
        ).eval()
        started = time.monotonic()
        self.embed(np.zeros(SAMPLE_RATE, np.int16))
        logger.info(
            f"speaker embedder {model_name} loaded "
            f"(warm-up {time.monotonic() - started:.2f} s)"
        )

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        torch = self._torch
        device = self._model.device
        signal = torch.from_numpy(pcm.astype(np.float32) / 32768.0)[None].to(device)
        with torch.inference_mode():
            _, embedding = self._model.forward(
                input_signal=signal,
                input_signal_length=torch.tensor([signal.shape[1]], device=device),
            )
        return embedding[0].float().cpu().numpy()


class EmbeddingWorker:
    """At most one embedding running and one pending per slot.

    A newer job for a slot replaces its pending one and keeps its place; the
    slot waiting longest goes first, so a TV that never stops talking cannot
    starve the customer's slot. Results reach ``on_result`` on the event loop.
    """

    def __init__(
        self,
        embedder: SpeakerEmbedder,
        on_result: Callable[[EmbeddingJob, np.ndarray, float], None],
        *,
        executor: Executor | None = None,
    ) -> None:
        self._embedder = embedder
        self._on_result = on_result
        self._executor = executor or EMBEDDING_EXECUTOR
        self._pending: dict[int, EmbeddingJob] = {}
        self._order: deque[int] = deque()
        self._task: asyncio.Task | None = None
        self.failed = False
        self.elapsed_ms: deque[float] = deque(maxlen=_MAX_TIMINGS)

    @property
    def pending(self) -> int:
        return len(self._pending)

    def submit(self, job: EmbeddingJob) -> None:
        if self.failed or getattr(self._embedder, "disabled", False):
            return
        if job.slot not in self._pending:
            self._order.append(job.slot)
        self._pending[job.slot] = job
        self._kick()

    def clear(self) -> None:
        self._pending.clear()
        self._order.clear()

    def _kick(self) -> None:
        if self._task is not None or not self._order:
            return
        job = self._pending.pop(self._order.popleft())
        self._task = asyncio.get_running_loop().create_task(self._run(job))

    async def _run(self, job: EmbeddingJob) -> None:
        try:
            started = time.monotonic()
            embedding = await asyncio.get_running_loop().run_in_executor(
                self._executor, self._embedder.embed, job.pcm
            )
            elapsed = (time.monotonic() - started) * 1000.0
            self.elapsed_ms.append(elapsed)
            self._on_result(job, embedding, elapsed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - fusion runs on without voiceprints
            self.failed = True
            self._embedder.disabled = True
            self.clear()
            logger.exception("speaker embedder failed; fusion runs without voiceprints")
        finally:
            self._task = None
            if not self.failed:
                self._kick()

    async def close(self) -> None:
        self.clear()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


def resample_to_16k(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == SAMPLE_RATE:
        return pcm
    from scipy.signal import resample_poly

    g = math.gcd(SAMPLE_RATE, sample_rate)
    out = resample_poly(pcm.astype(np.float32), SAMPLE_RATE // g, sample_rate // g)
    return np.clip(out, -32768, 32767).astype(np.int16)


class BotVoiceprint:
    """Jarvis's own voice, embedded once per process from VieNeu's clean output.

    One VieNeu voice serves both languages, so one voiceprint covers the bot.
    """

    def __init__(self) -> None:
        self.embedding: np.ndarray | None = None
        self.failed = False
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._taken = False

    def offer(
        self, audio: bytes, sample_rate: int, num_channels: int
    ) -> np.ndarray | None:
        """Collect TTS audio; return 3 s of 16 kHz mono once, then None."""
        if self._taken or self.failed or num_channels != 1 or len(audio) % 2:
            return None
        pcm = resample_to_16k(np.frombuffer(audio, np.int16), sample_rate)
        self._chunks.append(pcm)
        self._samples += len(pcm)
        need = int(BOT_ENROLL_SECS * SAMPLE_RATE)
        if self._samples < need:
            return None
        self._taken = True
        joined = np.concatenate(self._chunks)[:need]
        self._chunks = []
        return joined


def bot_audio_sink(
    embedder: SpeakerEmbedder, bot: BotVoiceprint, executor: Executor | None = None
) -> Callable[[bytes, int, int], None]:
    """A VieNeu ``on_audio`` hook that embeds the bot's first 3 s, off the loop."""
    pool = executor or EMBEDDING_EXECUTOR

    def embed(pcm: np.ndarray) -> None:
        try:
            bot.embedding = unit(embedder.embed(pcm))
        except Exception:  # noqa: BLE001 - echo falls back to playback overlap
            bot.failed = True
            logger.exception("bot voiceprint unavailable; echo uses playback overlap")

    def sink(audio: bytes, sample_rate: int, num_channels: int) -> None:
        if getattr(embedder, "disabled", False):
            return
        pcm = bot.offer(audio, sample_rate, num_channels)
        if pcm is not None:
            pool.submit(embed, pcm)

    return sink
