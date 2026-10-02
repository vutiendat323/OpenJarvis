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
from pathlib import Path
from typing import Any, Protocol

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

from openjarvis.server.voice.speaker import SEGMENT_MAX_SECS, AudioOnlyGate, Verdict
from openjarvis.server.voice.speaker_embedding import EmbeddingWorker, SpeakerEmbedder
from openjarvis.server.voice.speaker_identity import EmbeddingJob, FusionGate
from openjarvis.server.voice.speaker_vision import AudioSpan, VisionAudioBridge
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


class Separator(Protocol):
    """Target-speaker extraction over a held stretch of mixed audio."""

    window_samples: int
    enroll_samples: int

    def separate(self, mix: np.ndarray, enroll: np.ndarray) -> np.ndarray:
        """float32 mix (<= ``window_samples``) + enrollment -> the target's audio."""
        ...


class TseSeparator:
    """REAL-TSE WeSep BSRNN, exported by ``scripts/voice_tse_export.py``.

    Non-causal: it needs the whole overlapped stretch, so the processor holds
    that stretch back from STT and separates it in one call (~0.3 s for 8 s on
    the dev laptop, ~0.7 GB VRAM). Trained on clean Libri2Mix: the 2026-09-26
    spike only partly recovered an order under a loud phone video (spec §11).
    """

    window_samples = 8 * SAMPLE_RATE
    enroll_samples = 3 * SAMPLE_RATE
    _PAD = 128  # one STFT hop, as exported

    def __init__(self, path: str, *, device: str = "cuda") -> None:
        import torch

        # TorchScript's default executor stalls for minutes on this traced
        # LSTM graph or misplaces its constants on the CPU, and its fuser fails
        # to compile kernels. The simple executor with optimisation off (per
        # call, below) runs it as traced. Process-wide, but nothing else in
        # the Voice process runs TorchScript.
        torch._C._jit_set_profiling_executor(False)
        self._torch = torch
        self._device = device
        self._model = torch.jit.load(str(Path(path).expanduser()), map_location=device)
        # The first call costs ~1.2 s, past the STT's drain timeout.
        started = time.monotonic()
        self.separate(
            np.zeros(SAMPLE_RATE, np.float32),
            np.zeros(self.enroll_samples, np.float32),
        )
        logger.info(
            f"TSE separator loaded from {path} "
            f"(warm-up {time.monotonic() - started:.2f} s)"
        )

    def separate(self, mix: np.ndarray, enroll: np.ndarray) -> np.ndarray:
        torch = self._torch
        padded = np.zeros(self.window_samples + self._PAD, np.float32)
        padded[: len(mix)] = mix[: self.window_samples]
        with torch.inference_mode(), torch.jit.optimized_execution(False):
            out = self._model(
                torch.from_numpy(padded)[None].to(self._device),
                torch.from_numpy(enroll[-self.enroll_samples :])[None].to(self._device),
            )
        return out[0, : len(mix)].float().cpu().numpy()


# One GPU model, one thread: pushes and resets never interleave.
DIARIZER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diarizer")
SEPARATOR_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="separator")
# Clean audio before a held overlap that the separator also sees.
SEPARATION_CONTEXT_SECS = 2.0
# Room reverb keeps the bot audible briefly after playback ends.
BOT_TAIL_SECS = 0.3
_MAX_QUEUED_CHUNKS = 2
# A segment enrolls the customer's voiceprint only when this share of its rows
# was ASD-confirmed on the locked face.
TARGET_CONFIRMED_FRACTION = 0.8


def _percentile(values, pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))], 1)


class _Segment:
    """One slot's solo speech since its last embedding, bounded to 3 s."""

    def __init__(self) -> None:
        self.rows: deque[tuple[np.ndarray, int | None]] = deque()
        self.end = 0.0

    @property
    def seconds(self) -> float:
        return sum(len(pcm) for pcm, _ in self.rows) / SAMPLE_RATE

    def add(self, pcm: np.ndarray, asd_track: int | None, t: float) -> None:
        self.rows.append((pcm.copy(), asd_track))
        self.end = t
        while self.seconds > SEGMENT_MAX_SECS:
            self.rows.popleft()

    def job(self, slot: int, lock) -> EmbeddingJob:
        tracks = [track for _, track in self.rows]
        confirmed = (
            lock.locked
            and lock.target_track is not None
            and sum(track == lock.target_track for track in tracks)
            >= TARGET_CONFIRMED_FRACTION * len(tracks)
        )
        return EmbeddingJob(
            epoch=lock.epoch,
            slot=slot,
            segment_end=self.end,
            seconds=self.seconds,
            pcm=np.concatenate([pcm for pcm, _ in self.rows]),
            target_confirmed=confirmed,
        )


def _dbfs(audio: np.ndarray) -> float:
    """RMS level, to tell in logs whether TSE kept or dropped the voice."""
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    return 20 * math.log10(max(rms, 1e-6))


class SpeakerAudioProcessor(FrameProcessor):
    """Diarize the customer mic off the event loop; emit per-frame verdicts.

    Audio is pushed on before anything else, so the VAD never waits on the
    model. With ``stt_delay_secs`` it also releases a delayed copy of the mic
    for Gemini (``SttAudioFrame``), silencing every stretch the gate
    rejected, so a phone video or a bystander never lands in the customer's
    transcript, and every stretch the diarizer heard no voice in. Audio whose
    verdict is not in yet goes through unchanged.

    With a ``separator``, overlapped speech is held back from Gemini and
    replaced by the customer's voice extracted from it, enrolled from their
    clean ACCEPTed speech. Held frames the gate REJECTed still go out silent.
    """

    def __init__(
        self,
        *,
        diarizer: Diarizer,
        gate: AudioOnlyGate,
        executor: Executor | None = None,
        stt_delay_secs: float = 0.0,
        separator: Separator | None = None,
        separator_executor: Executor | None = None,
        vision_audio: VisionAudioBridge | None = None,
        embedder: SpeakerEmbedder | None = None,
        tracker: Any | None = None,
        speaker_settings: Any | None = None,
        audio_enhancer: Any | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._stt_delay = stt_delay_secs
        self._separator = separator if stt_delay_secs else None
        self._separator_executor = separator_executor or SEPARATOR_EXECUTOR
        self._enroll: deque[np.ndarray] = deque()
        self._enrolled = 0
        self._held: list[tuple[float, InputAudioRawFrame]] = []
        self._context: deque[np.ndarray] = deque()
        self._context_samples = 0
        self._release_lock = asyncio.Lock()
        self._stt_line: deque[tuple[float, InputAudioRawFrame]] = deque()
        # Start times and verdicts of recent 80 ms frames, in time order.
        self._verdict_starts: list[float] = []
        self._verdict_values: list[Verdict | None] = []
        self._overlap_values: list[bool] = []
        self._releaser: asyncio.Task | None = None
        self._diarizer = diarizer
        self._vision_audio = vision_audio
        self._gate = gate
        self._asd_enabled = isinstance(gate, AudioOnlyGate) and gate.vision_asd
        self._asd_reported = False
        self._executor = executor or DIARIZER_EXECUTOR
        self._pending = bytearray()
        # Chunk times: one origin plus bytes consumed since it, so chunk ends
        # stay on the bridge's sample clock instead of drifting.
        self._pending_origin: float | None = None
        self._pending_offset = 0
        self._pending_stream: str | None = None
        self._queue: asyncio.Queue[tuple[bytes, bool, float, str | None]] = (
            asyncio.Queue(maxsize=_MAX_QUEUED_CHUNKS)
        )
        self._worker: asyncio.Task | None = None
        self._bot_audible_until = 0.0
        self._warned_format = False
        self._fusion = gate if isinstance(gate, FusionGate) else None
        self._tracker = tracker
        self._segments: dict[int, _Segment] = {}
        self._epoch = self._fusion.lock.epoch if self._fusion is not None else 0
        self._pinned: int | None = None
        self._masked_values: list[bool] = []
        self.stt_masked_no_verdict = 0
        self._fusion_row_errors = 0
        self._embeddings = (
            EmbeddingWorker(embedder, self._on_embedding)
            if self._fusion is not None
            and embedder is not None
            and not getattr(embedder, "disabled", False)
            else None
        )
        self._console = None
        if speaker_settings is not None and hasattr(vision_audio, "offer_telemetry"):
            from openjarvis.server.voice.speaker_console import SpeakerConsole

            self._console = SpeakerConsole(self, speaker_settings, audio_enhancer)
            vision_audio.console = self._console

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, (EndFrame, CancelFrame)) and self._stt_delay:
            # Teardown: send what is left, without a GPU call.
            await self._release_stt(math.inf, separate=False)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            if self._vision_audio is not None:
                await self._vision_audio.start()
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
            span = None
            if self._vision_audio is not None:
                span = self._vision_audio.offer(
                    frame.audio, frame.sample_rate, frame.num_channels
                )
            if self._stt_delay:
                arrival = span.end if span is not None else time.time()
                self._stt_line.append((arrival, frame))
            self._enqueue(frame, span)
            if self._console is not None:
                try:
                    self._console.audio(frame.audio)
                except Exception:
                    logger.exception("operator console audio observation failed")

    def _enqueue(
        self, frame: InputAudioRawFrame, span: AudioSpan | None = None
    ) -> None:
        if frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1:
            if not self._warned_format:
                logger.warning(
                    f"{self}: diarizer needs 16 kHz mono, got "
                    f"{frame.sample_rate} Hz x{frame.num_channels}; skipping"
                )
                self._warned_format = True
            return
        if span is not None:
            expected = (
                self._pending_origin
                + (self._pending_offset + len(self._pending)) / (SAMPLE_RATE * 2)
                if self._pending_origin is not None
                else None
            )
            # A bridge stream's sample clock is exact, so 20 ms off is a gap.
            # Spans without a stream are arrival-timed and jitter by nature;
            # only a real pause (the bridge's 250 ms divergence) re-anchors.
            tolerance = 0.02 if span.stream_id is not None else 0.25
            if span.stream_id != self._pending_stream or (
                expected is not None and abs(span.start - expected) > tolerance
            ):
                self._pending.clear()
                self._pending_origin = None
                self._pending_stream = span.stream_id
            if self._pending_origin is None:
                self._pending_origin, self._pending_offset = span.start, 0
        self._pending.extend(frame.audio)
        size = self._diarizer.chunk_samples * 2
        while len(self._pending) >= size:
            chunk = bytes(self._pending[:size])
            del self._pending[:size]
            if self._queue.full():
                self._queue.get_nowait()
                logger.warning(f"{self}: diarizer behind realtime; dropped a chunk")
            if span is not None:
                self._pending_offset += size
            ended = (
                time.time()
                if span is None
                else self._pending_origin + self._pending_offset / (SAMPLE_RATE * 2)
            )
            self._queue.put_nowait(
                (
                    chunk,
                    time.monotonic() < self._bot_audible_until,
                    ended,
                    self._pending_stream,
                )
            )

    async def _diarize(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            chunk, bot_speaking, ended, stream_id = await self._queue.get()
            pcm = np.frombuffer(chunk, dtype=np.int16)
            probs = await loop.run_in_executor(self._executor, self._diarizer.push, pcm)
            step = self._diarizer.frame_secs
            row_samples = len(pcm) // max(len(probs), 1)
            wait_timed_out = False
            asd_reason = None
            if (
                self._asd_enabled
                and self._vision_audio is not None
                and stream_id is not None
                and getattr(self._vision_audio, "audio_seconds", 1.0) < 1.0
            ):
                asd_reason = "warmup"
            if (
                self._asd_enabled
                and self._vision_audio is not None
                and stream_id is not None
                and stream_id == self._vision_audio.stream_id
                and self._gate.asd_wait_candidate(ended, probs)
            ):
                # Fusion waits for the locked customer's track, the one Vision
                # is pinned to; the audio-only gate keeps the bridge's anchor.
                wait_track = (
                    {"track_id": self._fusion.asd_wait_track(ended)}
                    if self._fusion is not None
                    else {}
                )
                await self._vision_audio.wait_for_evidence(
                    self._gate._faces,
                    ended - len(probs) * step,
                    ended,
                    timeout=self._gate.asd_wait_secs,
                    **wait_track,
                )
                wait_timed_out = getattr(
                    self._vision_audio, "last_wait_timed_out", False
                )
                if wait_timed_out:
                    asd_reason = "late"
            if self._asd_enabled and self._gate._faces is not None:
                self._gate._asd_snapshot = self._gate._faces.snapshot(
                    include_asd=not wait_timed_out
                )
                self._gate.asd_row_reason = asd_reason
            try:
                for i, row in enumerate(probs):
                    target = self._gate.target
                    # Wall-clock time of this frame, to line it up with Vision.
                    t = ended - (len(probs) - 1 - i) * step
                    if self._asd_enabled:
                        verdict = self._gate.frame(
                            row,
                            bot_speaking=bot_speaking,
                            t=t,
                            asd_stream_id=stream_id,
                        )
                    else:
                        verdict = self._gate.frame(row, bot_speaking=bot_speaking, t=t)
                    if self._stt_delay:
                        self._remember_verdict(
                            t - step,
                            verdict,
                            self._gate.overlap,
                            mask=self._mask_row(verdict),
                        )
                    if (
                        self._separator is not None
                        and verdict is Verdict.ACCEPT
                        and not self._gate.overlap
                        and self._confirmed_customer()
                    ):
                        enrollment = pcm[i * row_samples : (i + 1) * row_samples]
                        self._add_enrollment(enrollment)
                    if self._fusion is not None:
                        self._after_fusion_row(
                            pcm[i * row_samples : (i + 1) * row_samples],
                            t,
                            bot_speaking,
                        )
                    if verdict is not None:
                        evidence = (
                            getattr(self._gate, "last_evidence_detail", None)
                            or self._gate.last_evidence
                        )
                        logger.debug(
                            f"{self}: speaker frame t={t:.2f} verdict={verdict.value} "
                            f"bot={bot_speaking} overlap={self._gate.overlap} "
                            f"evidence={evidence}"
                        )
                    if self._gate.target != target:
                        logger.info(
                            f"{self}: speaker target slot {target} -> "
                            f"{self._gate.target}"
                        )
                    if self._console is not None:
                        self._console.verdict(verdict, t)
                    if verdict is not None:
                        fusion = self._fusion
                        await self.push_frame(
                            SpeakerVerdictFrame(
                                verdict=verdict,
                                locked=fusion is not None and fusion.locked,
                                overlap_target=fusion is not None
                                and fusion.target_overlap,
                                source=fusion.last_source if fusion else None,
                            )
                        )
            finally:
                if self._asd_enabled:
                    self._gate._asd_snapshot = None
                    self._gate.asd_row_reason = None

    def _confirmed_customer(self) -> bool:
        """Only ASD-confirmed speech enrolls when ASD runs: live 2026-09-29,
        MAR-only ACCEPTs during a playing video enrolled the video's voice."""
        if self._fusion is not None and self._fusion.locked:
            # Locked: only the customer's own slot, confirmed by ASD, enrolls TSE.
            return self._fusion.last_source == "asd" and self._fusion.row_is_target
        if not getattr(self._gate, "vision_asd", False):
            return True
        detail = getattr(self._gate, "last_evidence_detail", None) or {}
        return detail.get("source") == "asd"

    def _remember_verdict(
        self,
        start: float,
        verdict: Verdict | None,
        overlap: bool = False,
        mask: bool = False,
    ) -> None:
        self._verdict_starts.append(start)
        self._verdict_values.append(verdict)
        self._overlap_values.append(overlap)
        self._masked_values.append(mask)
        if len(self._verdict_starts) > 512:  # ~40 s of frames
            del self._verdict_starts[:256], self._verdict_values[:256]
            del self._overlap_values[:256], self._masked_values[:256]

    def _frame_at(self, t: float) -> int | None:
        i = bisect.bisect_right(self._verdict_starts, t) - 1
        if i >= 0 and t < self._verdict_starts[i] + self._diarizer.frame_secs:
            return i
        return None

    def _silenced(self, t: float) -> bool:
        """Rejected speech, sound the diarizer heard no voice in, or -- once a
        customer is locked -- anything not confirmed as them.

        The 2026-09-26 live test: a quiet phone video tripped the VAD but not
        Sortformer, and Gemini transcribed it into turns with no speaker
        evidence. Before a lock, audio the diarizer has not reached yet still
        goes through; after it, audio with no verdict by release time is a
        dropped diarizer chunk and stays out (fail closed).
        """
        i = self._frame_at(t)
        if i is None:
            if self._fusion is not None and self._fusion.locked:
                self.stt_masked_no_verdict += 1
                return True
            return False
        return self._masked_values[i] or self._verdict_values[i] in (
            Verdict.REJECT,
            None,
        )

    def _mask_row(self, verdict: Verdict | None) -> bool:
        """After the lock, UNCERTAIN speech stays out of STT, except the
        customer talking over another voice when TSE can extract them or their
        face visibly speaks."""
        gate = self._fusion
        if gate is None or not gate.locked or verdict is not Verdict.UNCERTAIN:
            return False
        if gate.target_overlap:
            can_separate = (
                self._separator is not None
                and self._enrolled >= self._separator.enroll_samples
            )
            return not (can_separate or gate.target_speaking_visibly)
        return True

    def _after_fusion_row(
        self, pcm_row: np.ndarray, t: float, bot_speaking: bool
    ) -> None:
        """Per row: log lock events, keep Vision's ASD on the customer, and
        gather solo speech for embeddings.

        Runs inside the ``_diarize`` task: a bug here must cost this
        bookkeeping, never the verdicts that follow.
        """
        try:
            self._fusion_bookkeeping(pcm_row, t, bot_speaking)
        except Exception:  # noqa: BLE001 - verdicts keep flowing
            self._fusion_row_errors += 1
            if self._fusion_row_errors == 1:
                logger.exception(f"{self}: speaker fusion bookkeeping failed")

    def _fusion_bookkeeping(
        self, pcm_row: np.ndarray, t: float, bot_speaking: bool
    ) -> None:
        gate = self._fusion
        lock = gate.lock
        if lock.epoch != self._epoch:
            self._epoch = lock.epoch
            self._segments.clear()
            if self._embeddings is not None:
                self._embeddings.clear()
            # TSE must not separate the next customer with this one's voice.
            self._enroll.clear()
            self._enrolled = 0
        for event in lock.drain_events():
            logger.info(f"{self}: speaker_lock {event.fields()}")
        if self._vision_audio is not None and hasattr(self._vision_audio, "pin"):
            want = lock.desired_pin(t)
            if want != self._pinned:
                self._pinned = want
                self._vision_audio.pin(want)
        if self._embeddings is None:
            return
        voices = gate.row_voices
        if len(voices) != 1 or bot_speaking or gate.target_overlap:
            return
        slot = voices[0]
        segment = self._segments.setdefault(slot, _Segment())
        segment.add(pcm_row, gate.row_asd_track, t)
        if segment.seconds >= gate.settings.embed_segment_secs:
            self._embeddings.submit(segment.job(slot, lock))
            del self._segments[slot]

    def _on_embedding(
        self, job: EmbeddingJob, embedding: np.ndarray, elapsed_ms: float
    ) -> None:
        # Called by EmbeddingWorker on the event loop; the worker logs and
        # drops anything this raises.
        gate = self._fusion
        if gate is None or not gate.lock.observe_embedding(job, embedding):
            return
        ident = gate.lock.slots[job.slot]
        fmt = lambda v: "none" if v is None else f"{v:.2f}"  # noqa: E731
        logger.info(
            f"{self}: speaker_identity slot={job.slot} "
            f"label={gate.lock.label(job.slot).value} "
            f"voice_sim={fmt(ident.voice_sim)} bot_sim={fmt(ident.bot_sim)} "
            f"seg_s={job.seconds:.2f} embed_ms={elapsed_ms:.0f} "
            f"pending={self._embeddings.pending if self._embeddings else 0}"
        )

    def _overlap_at(self, t: float) -> bool:
        i = self._frame_at(t)
        return i is not None and self._overlap_values[i]

    def _add_enrollment(self, pcm: np.ndarray) -> None:
        # One enrollment per customer: fusion clears it on each lock epoch
        # (_fusion_bookkeeping); without fusion, one per Voice session.
        self._enroll.append(pcm.astype(np.float32) / 32768.0)
        self._enrolled += len(pcm)
        limit = self._separator.enroll_samples
        while self._enrolled - len(self._enroll[0]) >= limit:
            self._enrolled -= len(self._enroll.popleft())

    def _remember_context(self, audio: np.ndarray) -> None:
        self._context.append(audio)
        self._context_samples += len(audio)
        limit = int(SEPARATION_CONTEXT_SECS * SAMPLE_RATE)
        while self._context_samples - len(self._context[0]) >= limit:
            self._context_samples -= len(self._context.popleft())

    async def _release_loop(self) -> None:
        while True:
            await asyncio.sleep(0.02)
            await self._release_stt(time.time() - self._stt_delay)

    async def _release_stt(self, cutoff: float, *, separate: bool = True) -> None:
        async with self._release_lock:
            while self._stt_line and self._stt_line[0][0] <= cutoff:
                arrived, frame = self._stt_line.popleft()
                duration = (
                    frame.num_frames / frame.sample_rate if frame.sample_rate else 0
                )
                t = arrived - duration / 2
                if self._can_separate(frame) and self._overlap_at(t):
                    self._held.append((arrived, frame))
                    if self._held_samples() >= self._separator.window_samples // 2:
                        await self._flush_held(separate=separate)
                    continue
                await self._flush_held(separate=separate)
                await self._push_stt(
                    frame, bytes(len(frame.audio)) if self._silenced(t) else frame.audio
                )
            if not math.isfinite(cutoff):
                await self._flush_held(separate=separate)

    def _can_separate(self, frame: InputAudioRawFrame) -> bool:
        return (
            self._separator is not None
            and self._enrolled >= self._separator.enroll_samples
            and frame.sample_rate == SAMPLE_RATE
            and frame.num_channels == 1
        )

    def _held_samples(self) -> int:
        return sum(frame.num_frames for _, frame in self._held)

    async def _push_stt(self, frame: InputAudioRawFrame, audio: bytes) -> None:
        if self._separator is not None:
            self._remember_context(np.frombuffer(audio, np.int16) / 32768.0)
        await self.push_frame(
            SttAudioFrame(
                audio=audio,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
            )
        )

    async def _flush_held(self, *, separate: bool = True) -> None:
        """Give Gemini the held overlap as the customer's voice alone."""
        if not self._held:
            return
        held, self._held = self._held, []
        target = await self._separate(held) if separate else None
        start = 0
        for arrived, frame in held:
            end = start + frame.num_frames
            audio = frame.audio if target is None else target[start:end].tobytes()
            # The separator saw the whole mix, but a REJECTed frame is the
            # other voice alone: TSE output of it still carried a video live.
            if self._silenced(arrived - frame.num_frames / frame.sample_rate / 2):
                audio = bytes(len(frame.audio))
            await self._push_stt(frame, audio)
            start = end

    async def _separate(
        self, held: list[tuple[float, InputAudioRawFrame]]
    ) -> np.ndarray | None:
        """int16 target audio for the held frames, or None to send the mix."""
        mixed = np.concatenate([np.frombuffer(f.audio, np.int16) for _, f in held])
        context = np.concatenate(self._context) if self._context else np.zeros(0)
        started = time.monotonic()
        try:
            out = await asyncio.get_running_loop().run_in_executor(
                self._separator_executor,
                self._separator.separate,
                np.concatenate([context, mixed / 32768.0]).astype(np.float32),
                np.concatenate(self._enroll).astype(np.float32),
            )
        except Exception:  # noqa: BLE001 - never lose the customer's audio
            logger.exception(f"{self}: separation failed; sending the mix")
            return None
        target = np.asarray(out[len(context) :], np.float32)
        logger.info(
            f"{self}: separated {len(mixed) / SAMPLE_RATE:.2f} s of overlap "
            f"in {time.monotonic() - started:.2f} s "
            f"(mix {_dbfs(mixed / 32768.0):.0f} dBFS -> {_dbfs(target):.0f} dBFS)"
        )
        return (np.clip(target, -1.0, 1.0) * 32767).astype(np.int16)

    async def drain(self) -> None:
        """Release everything due, separating held overlap, before finalizing.

        The STT service awaits this at end of utterance: overlap held back at
        that moment would otherwise miss the customer's final transcript.
        """
        await self._release_stt(time.time() - self._stt_delay)
        async with self._release_lock:
            await self._flush_held()

    async def _stop(self) -> None:
        if self._asd_enabled and not self._asd_reported:
            self._asd_reported = True
            counts = self._gate.asd_counts
            used = counts["used_accept"] + counts["used_reject"]
            eligible = used + counts["middle"] + counts["late"] + counts["gap"]
            logger.info(
                f"{self}: ASD session rows used={used} "
                f"(accept={counts['used_accept']} reject={counts['used_reject']}) "
                f"middle={counts['middle']} late={counts['late']} "
                f"gap={counts['gap']} warmup={counts['warmup']} "
                f"effective_use={used / eligible if eligible else 0:.0%} "
                f"late_chunks={getattr(self._vision_audio, 'asd_late', 0)}"
            )
        if self._fusion is not None and not getattr(self, "_fusion_reported", False):
            self._fusion_reported = True
            lock = self._fusion.lock
            ages = list(getattr(self._vision_audio, "asd_age_ms", ()) or ())
            embeds = list(self._embeddings.elapsed_ms) if self._embeddings else []
            protocol = getattr(self._vision_audio, "protocol_version", None)
            logger.info(
                f"{self}: speaker_fusion session locks={lock.lock_count} "
                f"lock_after_s={lock.last_locked_after_s} "
                f"rebinds={dict(lock.rebinds)} "
                f"asd_protocol=v{protocol} "
                f"asd_age_ms_p50={_percentile(ages, 50)} "
                f"asd_age_ms_p95={_percentile(ages, 95)} "
                f"embed_ms_p50={_percentile(embeds, 50)} "
                f"embed_ms_p95={_percentile(embeds, 95)} "
                f"stt_masked_no_verdict_frames={self.stt_masked_no_verdict} "
                f"echo_rejects={self._fusion.echo_rejects} "
                f"bargein_blocked={getattr(self._tracker, 'bargein_blocked', 0)} "
                f"fusion_row_errors={self._fusion_row_errors}"
            )
        if self._embeddings is not None:
            await self._embeddings.close()
        if self._vision_audio is not None:
            await self._vision_audio.close()
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
