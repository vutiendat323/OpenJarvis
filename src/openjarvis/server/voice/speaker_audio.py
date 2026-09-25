"""Speaker models behind small protocols, so each can be benchmarked or swapped.

Model packages (NeMo, torch) are imported only inside the classes that need
them: the ``voice-speaker`` extra is optional.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

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
