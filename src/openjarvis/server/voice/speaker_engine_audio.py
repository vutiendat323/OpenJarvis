"""engine = "audio": speaker fusion from the ``audio`` library (spec 2026-10-16).

``AudioSpeakerProcessor`` is the Pipecat host for audio's ``SpeakerStream``. It
handles frames exactly as ``SpeakerAudioProcessor`` does and translates at the
boundary, so the tracker, turn strategies, LLM and STT only ever see OpenJarvis
frames and OpenJarvis's ``Verdict``. ``audio`` is imported only inside these
functions; without it the pipeline keeps the openjarvis engine.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import fields, replace
from typing import Any

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

from openjarvis.server.voice.speaker import SpeakerSettings, Verdict
from openjarvis.server.voice.speaker_audio import DIARIZER_EXECUTOR, SEPARATOR_EXECUTOR
from openjarvis.server.voice.speaker_embedding import EMBEDDING_EXECUTOR
from openjarvis.server.voice.transcription import SttAudioFrame
from openjarvis.server.voice.turn_detection import SpeakerVerdictFrame


class _ToLoguru(logging.Handler):
    """audio logs through stdlib logging; the kiosk log is loguru's."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.patch(
            lambda r: r.update(
                name=record.name, function=record.funcName, line=record.lineno
            )
        ).opt(exception=record.exc_info).log(level, record.getMessage())


def _forward_audio_logs() -> None:
    """Send audio's records, DEBUG up as before, to the kiosk log once."""
    audio_logger = logging.getLogger("audio")
    if not any(isinstance(h, _ToLoguru) for h in audio_logger.handlers):
        audio_logger.addHandler(_ToLoguru())
        audio_logger.setLevel(logging.DEBUG)
        audio_logger.propagate = False


# A missing package is reported once per process, not at every session.
_missing_reported = False


def audio_engine_available() -> bool:
    """True when ``audio`` imports; otherwise warn once and keep the old engine."""
    global _missing_reported
    try:
        import audio.application.stream  # noqa: F401
    except ImportError:
        if not _missing_reported:
            _missing_reported = True
            logger.warning(
                "voice speaker engine=audio but the audio package is not "
                "importable (uv pip install --no-deps -e ../Audio); voice "
                "sessions use the openjarvis engine"
            )
        return False
    _forward_audio_logs()
    return True


def audio_settings(speaker: SpeakerSettings) -> Any:
    """audio's SpeakerSettings: every field of the same name, already validated."""
    from audio.domain.settings import SpeakerSettings as AudioSettings

    return AudioSettings(
        **{field.name: getattr(speaker, field.name) for field in fields(AudioSettings)}
    )


def kiosk_distance_key() -> Any:
    """The operator's approach distance, tuned live as the old console did."""
    from audio.application.ports import LiveKey

    from openjarvis.kiosk.evaluate import get_config, set_config
    from openjarvis.server.voice.speaker_console import LIVE_SPEAKER_KEYS

    def set_distance(value: float) -> None:
        set_config(replace(get_config(), approach_threshold_m=value))

    return LiveKey(
        "approach_threshold_m",
        LIVE_SPEAKER_KEYS["approach_threshold_m"],
        lambda: get_config().approach_threshold_m,
        set_distance,
    )


class AudioSpeakerProcessor(FrameProcessor):
    """SpeakerAudioProcessor's frame handling around audio's SpeakerStream."""

    def __init__(
        self,
        *,
        diarizer: Any,
        gate: Any,
        executor: Any | None = None,
        stt_delay_secs: float = 0.0,
        separator: Any | None = None,
        separator_executor: Any | None = None,
        vision_audio: Any | None = None,
        embedder: Any | None = None,
        tracker: Any | None = None,
        speaker_settings: Any | None = None,
        audio_enhancer: Any | None = None,
        target_audio_monitor: Any | None = None,
        live_keys: Sequence[Any] = (),
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        from audio.application.stream import SpeakerStream

        # The models are process-wide and not thread-safe: use the executors
        # that already serialise them for the openjarvis engine.
        self._stream = SpeakerStream(
            diarizer=diarizer,
            gate=gate,
            executor=executor or DIARIZER_EXECUTOR,
            stt_delay_secs=stt_delay_secs,
            separator=separator,
            separator_executor=separator_executor or SEPARATOR_EXECUTOR,
            vision_audio=vision_audio,
            embedder=embedder,
            embedding_executor=EMBEDDING_EXECUTOR,
            tracker=tracker,
            speaker_settings=speaker_settings,
            audio_enhancer=audio_enhancer,
            target_audio_monitor=target_audio_monitor,
            live_keys=live_keys,
            on_verdict=self._on_verdict,
            on_stt_audio=self._on_stt_audio,
            name=self.name,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        stream = self._stream
        if isinstance(frame, (EndFrame, CancelFrame)):
            # Teardown: send what is left, without a GPU call.
            await stream.finish()
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            await stream.start()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await stream.close()
        elif isinstance(frame, BotStartedSpeakingFrame):
            stream.bot_started()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            stream.bot_stopped()
        elif (
            isinstance(frame, InputAudioRawFrame)
            and direction is FrameDirection.DOWNSTREAM
        ):
            from audio.application.stream import AudioChunk

            stream.push_audio(
                AudioChunk(frame.audio, frame.sample_rate, frame.num_channels)
            )

    async def drain(self) -> None:
        await self._stream.drain()

    async def cleanup(self) -> None:
        await self._stream.close()
        await super().cleanup()

    async def _on_verdict(self, item: Any) -> None:
        await self.push_frame(
            SpeakerVerdictFrame(
                verdict=Verdict(item.verdict.value),
                locked=item.locked,
                overlap_target=item.overlap_target,
                source=item.source,
            )
        )

    async def _on_stt_audio(self, item: Any) -> None:
        await self.push_frame(
            SttAudioFrame(
                audio=item.audio,
                sample_rate=item.sample_rate,
                num_channels=item.num_channels,
                verdict=item.verdict,
                reason=item.reason,
            )
        )


def build_audio_speaker_processor(
    speaker: SpeakerSettings,
    *,
    fusion: bool,
    faces: Any,
    bot_voiceprint: Any,
    **processor_args: Any,
) -> AudioSpeakerProcessor | None:
    """audio's gate and processor, chosen exactly as the openjarvis engine does.

    None when building fails: the session then keeps the openjarvis engine.
    """
    try:
        return _build_audio_speaker_processor(
            speaker,
            fusion=fusion,
            faces=faces,
            bot_voiceprint=bot_voiceprint,
            **processor_args,
        )
    except Exception:  # noqa: BLE001 - a customer must still be able to talk
        logger.exception(
            "voice speaker engine=audio failed to start; using the openjarvis engine"
        )
        return None


def _build_audio_speaker_processor(
    speaker: SpeakerSettings,
    *,
    fusion: bool,
    faces: Any,
    bot_voiceprint: Any,
    **processor_args: Any,
) -> AudioSpeakerProcessor:
    from audio.domain.fusion import FusionGate
    from audio.domain.gate import AudioOnlyGate

    settings = audio_settings(speaker)
    if fusion:
        from openjarvis.kiosk.runtime import current_state

        gate = FusionGate(
            settings,
            faces,
            fsm_state=current_state,
            bot_voiceprint=(
                (lambda: bot_voiceprint.embedding)
                if bot_voiceprint is not None
                else (lambda: None)
            ),
        )
    else:
        gate = AudioOnlyGate(settings, faces=faces if settings.vision_faces else None)
    return AudioSpeakerProcessor(
        gate=gate,
        speaker_settings=settings,
        live_keys=(kiosk_distance_key(),),
        **processor_args,
    )
