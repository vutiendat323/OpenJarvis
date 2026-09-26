"""Speaker models behind small protocols, so each can be benchmarked or swapped.

Model packages (NeMo, torch) are imported only inside the classes that need
them: the ``voice-speaker`` extra is optional.
"""

from __future__ import annotations

import asyncio
import bisect
import math
import time
from collections import deque
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Protocol

import numpy as np
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from openjarvis.server.voice.speaker import AudioOnlyGate, Verdict
from openjarvis.server.voice.transcription import SttAudioFrame
from openjarvis.server.voice.turn_detection import SpeakerVerdictFrame

SAMPLE_RATE = 16_000
SORTFORMER_MODEL = "nvidia/diar_streaming_sortformer_4spk-v2"
# (chunk, right context) in 80 ms frames, from the model card's presets.
LATENCY_PRESETS = {"ultra_low": (3, 1), "low": (6, 7)}
_FEATURE_OFFSET = 8  # 10 ms feature frames of context each side of a chunk
_LOG_MEL_ZERO = -16.635  # log-mel of digital silence


class Diarizer(Protocol):
    """Streaming speaker diarization, one fixed-size chunk at a time."""

    frame_secs: float
    chunk_samples: int

    def reset(self) -> None: ...

    def push(self, pcm: np.ndarray) -> np.ndarray:
        """int16 mono of ``chunk_samples`` -> (frames, speakers) probabilities."""
        ...


class SortformerDiarizer:
    """NeMo streaming Sortformer on one 16 kHz mono stream.

    Mirrors NeMo's ``NeMoStreamingDiarService`` and ``CacheFeatureBufferer``
    (nemo/agents/voice_agent/pipecat/services/nemo/), which cannot be imported
    next to Pipecat 1.8.1: that package pulls an older Pipecat API.
    Not thread-safe; the caller serialises push/reset on one thread.
    """

    frame_secs = 0.08

    def __init__(
        self,
        latency: str = "ultra_low",
        *,
        model_name: str = SORTFORMER_MODEL,
        device: str = "cuda",
    ) -> None:
        import torch
        from nemo.collections.asr.models import SortformerEncLabelModel

        chunk, right_context = LATENCY_PRESETS[latency]
        model = SortformerEncLabelModel.from_pretrained(model_name, map_location=device)
        modules = model.sortformer_modules
        modules.chunk_len = chunk
        modules.chunk_left_context = 1
        modules.chunk_right_context = right_context
        modules.fifo_len = 188
        modules.spkcache_update_period = 144
        modules.spkcache_len = 188
        modules._check_streaming_parameters()
        model.streaming_mode = True
        self._model = model.eval()
        self._torch = torch
        self._chunk = chunk
        stride = model.cfg.preprocessor.window_stride
        self.chunk_samples = int(round(chunk * self.frame_secs * SAMPLE_RATE))
        self._look_back = int(round(stride * SAMPLE_RATE))
        self._feature_chunk = int(round(chunk * self.frame_secs / stride))
        self._n_features = model.cfg.preprocessor.features
        self.reset()

    def reset(self) -> None:
        torch = self._torch
        device = self._model.device
        self._tail = np.zeros(self._look_back, np.float32)
        self._features = torch.full(
            (self._n_features, self._feature_chunk + 2 * _FEATURE_OFFSET),
            _LOG_MEL_ZERO,
            device=device,
        )
        self._state = self._model.sortformer_modules.init_streaming_state(
            batch_size=1, async_streaming=self._model.async_streaming, device=device
        )
        self._preds = torch.zeros(
            (1, 0, self._model.sortformer_modules.n_spk), device=device
        )

    def push(self, pcm: np.ndarray) -> np.ndarray:
        torch = self._torch
        device = self._model.device
        audio = pcm.astype(np.float32) / 32768.0
        samples = np.concatenate([self._tail, audio])
        self._tail = audio[-self._look_back :]
        with torch.inference_mode():
            signal = torch.from_numpy(samples).unsqueeze(0).to(device)
            features, _ = self._model.preprocessor(
                input_signal=signal,
                length=torch.tensor([signal.shape[1]], device=device),
            )
            features = features[0]
            if (extra := features.shape[1] - self._feature_chunk - 1) > 0:
                features = features[:, :-extra]
            self._features = torch.cat(
                [
                    self._features[:, self._feature_chunk :],
                    features[:, -self._feature_chunk :],
                ],
                dim=1,
            )
            window = self._features.T.unsqueeze(0)
            self._state, preds = self._model.forward_streaming_step(
                processed_signal=window,
                processed_signal_length=torch.tensor([window.shape[1]], device=device),
                streaming_state=self._state,
                total_preds=self._preds,
                left_offset=_FEATURE_OFFSET,
                right_offset=_FEATURE_OFFSET,
            )
            # Only the newest chunk is ever read; keep the history bounded.
            self._preds = preds[:, -self._chunk :]
        return self._preds[0].float().cpu().numpy()


# One GPU model, one thread: pushes and resets never interleave.
DIARIZER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diarizer")
# Room reverb keeps the bot audible briefly after playback ends.
BOT_TAIL_SECS = 0.3
_MAX_QUEUED_CHUNKS = 2


class SpeakerAudioProcessor(FrameProcessor):
    """Diarize the customer mic off the event loop; emit per-frame verdicts.

    Audio is pushed on before anything else, so the VAD never waits on the
    model. With ``stt_delay_secs`` it also releases a delayed copy of the mic
    for Gemini (``SttAudioFrame``), silencing every stretch the gate
    rejected, so a phone video or a bystander never lands in the customer's
    transcript. Audio whose verdict is not in yet goes through unchanged.
    """

    def __init__(
        self,
        *,
        diarizer: Diarizer,
        gate: AudioOnlyGate,
        executor: Executor | None = None,
        stt_delay_secs: float = 0.0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._stt_delay = stt_delay_secs
        self._stt_line: deque[tuple[float, InputAudioRawFrame]] = deque()
        # Start times and verdicts of recent 80 ms frames, in time order.
        self._verdict_starts: list[float] = []
        self._verdict_values: list[Verdict | None] = []
        self._releaser: asyncio.Task | None = None
        self._diarizer = diarizer
        self._gate = gate
        self._executor = executor or DIARIZER_EXECUTOR
        self._pending = bytearray()
        self._queue: asyncio.Queue[tuple[bytes, bool, float]] = asyncio.Queue(
            maxsize=_MAX_QUEUED_CHUNKS
        )
        self._worker: asyncio.Task | None = None
        self._bot_audible_until = 0.0
        self._warned_format = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, (EndFrame, CancelFrame)) and self._stt_delay:
            await self._release_stt(math.inf)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self._executor.submit(self._diarizer.reset)
            self._worker = self.create_task(self._diarize(), "diarize")
            if self._stt_delay:
                self._releaser = self.create_task(self._release_loop(), "stt_release")
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._stop()
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_audible_until = math.inf
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_audible_until = time.monotonic() + BOT_TAIL_SECS
        elif (
            isinstance(frame, InputAudioRawFrame)
            and direction is FrameDirection.DOWNSTREAM
        ):
            if self._stt_delay:
                self._stt_line.append((time.time(), frame))
            self._enqueue(frame)

    def _enqueue(self, frame: InputAudioRawFrame) -> None:
        if frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1:
            if not self._warned_format:
                logger.warning(
                    f"{self}: diarizer needs 16 kHz mono, got "
                    f"{frame.sample_rate} Hz x{frame.num_channels}; skipping"
                )
                self._warned_format = True
            return
        self._pending.extend(frame.audio)
        size = self._diarizer.chunk_samples * 2
        while len(self._pending) >= size:
            chunk = bytes(self._pending[:size])
            del self._pending[:size]
            if self._queue.full():
                self._queue.get_nowait()
                logger.warning(f"{self}: diarizer behind realtime; dropped a chunk")
            self._queue.put_nowait(
                (chunk, time.monotonic() < self._bot_audible_until, time.time())
            )

    async def _diarize(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            chunk, bot_speaking, ended = await self._queue.get()
            pcm = np.frombuffer(chunk, dtype=np.int16)
            probs = await loop.run_in_executor(self._executor, self._diarizer.push, pcm)
            step = self._diarizer.frame_secs
            for i, row in enumerate(probs):
                target = self._gate.target
                # Wall-clock time of this frame, to line it up with Vision.
                t = ended - (len(probs) - 1 - i) * step
                verdict = self._gate.frame(row, bot_speaking=bot_speaking, t=t)
                if self._stt_delay:
                    self._remember_verdict(t - step, verdict)
                if verdict is not None:
                    logger.debug(
                        f"{self}: speaker frame t={t:.2f} verdict={verdict.value} "
                        f"bot={bot_speaking} evidence={self._gate.last_evidence}"
                    )
                if self._gate.target != target:
                    logger.info(
                        f"{self}: speaker target slot {target} -> {self._gate.target}"
                    )
                if verdict is not None:
                    await self.push_frame(SpeakerVerdictFrame(verdict=verdict))

    def _remember_verdict(self, start: float, verdict: Verdict | None) -> None:
        self._verdict_starts.append(start)
        self._verdict_values.append(verdict)
        if len(self._verdict_starts) > 512:  # ~40 s of frames
            del self._verdict_starts[:256], self._verdict_values[:256]

    def _verdict_at(self, t: float) -> Verdict | None:
        i = bisect.bisect_right(self._verdict_starts, t) - 1
        if i >= 0 and t < self._verdict_starts[i] + self._diarizer.frame_secs:
            return self._verdict_values[i]
        return None

    async def _release_loop(self) -> None:
        while True:
            await asyncio.sleep(0.02)
            await self._release_stt(time.time() - self._stt_delay)

    async def _release_stt(self, cutoff: float) -> None:
        while self._stt_line and self._stt_line[0][0] <= cutoff:
            arrived, frame = self._stt_line.popleft()
            duration = frame.num_frames / frame.sample_rate if frame.sample_rate else 0
            rejected = self._verdict_at(arrived - duration / 2) is Verdict.REJECT
            await self.push_frame(
                SttAudioFrame(
                    audio=bytes(len(frame.audio)) if rejected else frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )

    async def _stop(self) -> None:
        if self._releaser is not None:
            await self.cancel_task(self._releaser)
            self._releaser = None
        if self._worker is not None:
            await self.cancel_task(self._worker)
            self._worker = None
        # Queued behind any in-flight push on the same thread.
        self._executor.submit(self._diarizer.reset)

    async def cleanup(self) -> None:
        await self._stop()
        await super().cleanup()
